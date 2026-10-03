"""Movie source to QC-approved MP4s, using ClipRank's existing worker/media systems."""
import asyncio,hashlib,json,logging,shutil
import re
from datetime import datetime,timezone
from pathlib import Path
from PIL import Image,ImageDraw
from app.core import config,database
from app.core.runtime import run_blocking,check_cancelled,run_process
from app.storage.manager import StorageManager
from app.sources.ingestion import SourceIngestion
from app.ai.router import AIRouter
from app.media.ffmpeg_core import FFmpegCore
from app.media.source_screening import SourceScreening
from app.media.moments import MomentAnalyzer
from app.media.captions import CaptionRenderer,font
from app.media.reframer import VideoReframer
from app.media.production_qc import ProductionQC
from app.media.verify import MAX_SHORT_SECONDS
from app.core.qc import enabled as qc_on
from app.transcription.transcriber import Transcriber
from app.tts.voice_engine import TTSEngine
from app.studio.policy import RightsPolicyEngine
from app.pipelines.movie.analyzer import MovieSceneAnalyzer
from app.pipelines.movie.director import MovieMomentScorer,MovieFormatDirector
from app.pipelines.movie.framing import MovieReframing
from app.pipelines.movie.music import CinematicMusicDirector
from app.pipelines.movie.models import ProtectedDialogueRegion
from app.pipelines.movie.quality import MovieQualityControl
from app.pipelines.movie.narration import MovieNarrationDirector

logger=logging.getLogger('cliprank.movie')

class MoviePipeline:
    @staticmethod
    def resources(temp):
        total=sum(p.stat().st_size for p in temp.rglob('*') if p.is_file() and not p.is_symlink())
        if total>4*1024**3:raise ValueError('Movie workspace exceeded its 4 GB temporary disk quota.')
        if shutil.disk_usage(temp).free<512*1024**2:raise ValueError('Movie production needs at least 512 MB of free disk space.')
    @staticmethod
    def provenance(settings,metadata):
        attested=settings.get('authorization_attested') is True
        license=settings.get('source_license') or 'User-authorized audiovisual reuse'
        proof=settings.get('license_reference','').strip()
        documented=bool(proof and settings.get('source_license') and attested)
        attribution=settings.get('source_attribution','').strip()
        if re.search(r'cc[\s-]*by\b|creative commons attribution',license,re.I) and not attribution:
            creator=settings.get('source_creator') or metadata.get('creator')
            if not creator:raise ValueError('This attribution license needs the source creator and a credit line in Advanced.')
            attribution=f'{metadata["title"]} · {creator} · {license} · Edited by ClipRank'
        return {'source_url':metadata.get('url') or 'user-upload:'+metadata['source_id'],
            'source_type':settings.get('source_type') or ('authorized_video_url' if metadata.get('url') else 'user_owned_upload'),
            'title':settings.get('source_title') or metadata['title'],'creator':settings.get('source_creator') or metadata.get('creator') or 'Source owner identified by user',
            'license':license,'rights_status':'commercial_use_permitted' if documented else 'user_authorized',
            'authorization_attested':attested,'license_evidence_url':proof or None,
            'attribution_required':bool(attribution),'attribution':attribution,
            'fetched_at':datetime.now(timezone.utc).isoformat(),'acquired_at':datetime.now(timezone.utc).isoformat(),
            'source_audio_authorized':attested and settings.get('source_audio_authorized',True) is True}
    @staticmethod
    def credit_graphic(path,provenance):
        text=provenance.get('attribution','').strip()
        if not text:return None
        image=Image.new('RGBA',(1080,1920),(0,0,0,0));draw=ImageDraw.Draw(image)
        lines=[];line=''
        for word in text.split():
            trial=(line+' '+word).strip()
            if draw.textbbox((0,0),trial,font=font(22))[2]>830:
                lines.append(line);line=word
            else:line=trial
        if line:lines.append(line)
        if len(lines)>4:raise ValueError('Attribution is too long for the mobile-safe credit area; shorten the credit line.')
        for i,value in enumerate(lines):draw.text((88,1610+i*28),value,font=font(22),fill=(255,255,255,235),stroke_width=1,stroke_fill=(0,0,0,220))
        image.save(path);return path
    @staticmethod
    def fingerprint(video):
        with video.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()
    @classmethod
    async def run(cls,job_id,project_id,video_source,count,settings,on_progress):
        temp=StorageManager.get_job_temp_dir(job_id);published=[];finished=False
        existing=database.get_project(project_id).get('result_data',{})
        checkpoint=existing.get('movie_checkpoint',{}) or existing.get('analysis_archive',{})
        rejected=[];records=[]
        def progress(status,value,stage):
            database.update_job(job_id,status=status,progress=value,current_stage=stage)
            on_progress(status,value,stage)
        def save():
            database.update_project(project_id,result_data={'movie_checkpoint':checkpoint,'rejected_moments':rejected})
        def record_review(analysis,attempt,review):
            checkpoint.setdefault('render_reviews',[]).append({'source_start':analysis['start'],
                'source_end':analysis['end'],'format':analysis['format'],'attempt':attempt+1,'review':review})
            save()
        try:
            if not video_source:raise ValueError('An authorized video URL or upload is required.')
            if settings.get('authorization_attested') is not True:raise ValueError('Confirm ownership or permission covering both video and its audio.')
            if settings.get('source_audio_authorized',True) is not True:raise ValueError('Movie source permission must include its original audio.')
            provider,name=await AIRouter.get_active_provider(settings)
            if not provider:raise ValueError('Connect a vision provider in Settings to verify movie scenes and final outputs.')
            cls.resources(temp)
            progress('INGESTING',8,'Loading your authorized movie')
            video=next((p for p in (temp/'downloads').glob('source.*') if p.is_file() and p.suffix in ('.mp4','.mkv','.mov','.webm')),None)
            if video:metadata=checkpoint.get('source_metadata') or {'title':settings.get('source_title','Movie'),'url':video_source if str(video_source).startswith('http') else None,'source_id':project_id}
            elif str(video_source).startswith(('http://','https://')):
                video,metadata=await run_blocking(SourceIngestion.download_video,video_source,temp/'downloads'/'source.mp4')
            else:
                incoming=Path(video_source).resolve()
                if incoming.is_relative_to(temp.resolve()):
                    await run_blocking(SourceIngestion.verify,incoming);video=incoming
                else:video=await run_blocking(SourceIngestion.ingest_video_file,incoming,temp/'downloads'/'source.mp4')
                metadata={'title':settings.get('source_title') or Path(video_source).stem,'url':None,'source_id':project_id}
            info=await run_blocking(FFmpegCore.get_video_info,video)
            if qc_on() and min(info['width'],info['height'])<360:raise ValueError('No strong publishable moments were found. The source is below the movie quality floor.')
            # Full decode catches corrupt footage before spending analysis API calls.
            await run_blocking(run_process,['ffmpeg','-v','error','-xerror','-i',str(video),'-map','0:v:0','-f','null','-'],timeout=900)
            fingerprint=await run_blocking(cls.fingerprint,video)
            if checkpoint.get('source_hash')!=fingerprint:checkpoint={}
            provenance=cls.provenance(settings,metadata)
            rights=RightsPolicyEngine.evaluate([provenance],settings.get('rights_policy','user_managed'))
            if not rights['passed']:raise ValueError('No strong publishable moments were found. '+rights['reason'])
            checkpoint.update(source_hash=fingerprint,source_metadata=metadata,provenance=provenance,rights=rights)
            save();cls.resources(temp)
            if not checkpoint.get('source_cleanliness'):
                progress('ANALYZING',15,'Checking for a clean source')
                screen=await run_blocking(SourceScreening.inspect,{**metadata,'file_path':str(video),'duration':info['duration']},temp/'source-review')
                clean=await MovieSceneAnalyzer.understand(provider,name,screen['sheet'],None,None,settings,source_check=True)
                if qc_on() and (clean['clean_source'] is not True or clean['contains_watermark'] is not False):
                    raise ValueError('No strong publishable moments were found. Use an authorized clean source without third-party watermark overlays.')
                checkpoint['source_cleanliness']=clean;save()
            if 'transcript' not in checkpoint:
                progress('TRANSCRIBING',22,'Listening to the movie and its dialogue')
                transcript={'segments':[],'text':'','available':True}
                if info['has_audio']:
                    audio=await run_blocking(FFmpegCore.extract_audio,video,temp/'audio'/'movie.wav')
                    transcript=await run_blocking(Transcriber.transcribe,audio)
                    if transcript.get('available') is not True:raise ValueError('Movie dialogue transcription is unavailable; reconnect the cached speech model.')
                checkpoint['transcript']=transcript;save()
            transcript=checkpoint['transcript']
            if checkpoint.get('analysis_version')!=2:
                checkpoint.pop('analyses',None);checkpoint.pop('verified_analyses',None)
                checkpoint['analysis_version']=2
            if checkpoint.get('verification_version')!=3:
                checkpoint.pop('verified_analyses',None);checkpoint['verification_version']=3
            if not checkpoint.get('analyses'):
                progress('ANALYZING',32,'Finding strong moments across the complete movie')
                # A trailer or clip that already fits in a Short is used whole; only a feature-length source needs scenes picked from it.
                whole=bool(settings.get('whole_video',True)) and info['duration']<=MAX_SHORT_SECONDS
                candidates,preprocessing=await run_blocking(MovieSceneAnalyzer.proposals,video,transcript,
                    info['duration'] if whole else settings.get('target_duration',30),count)
                if whole and candidates:
                    candidates=[{**candidates[0],'start':0.0,'end':float(info['duration'])}]
                checkpoint['preprocessing']=preprocessing
                analyses=[];invalid_run=0
                for idx,candidate in enumerate(candidates):
                    cls.resources(temp)
                    progress('ANALYZING',32+round(20*idx/max(1,len(candidates))),f'Understanding moment {idx+1}/{len(candidates)}')
                    sheet=await run_blocking(MomentAnalyzer.contact_sheet,video,[candidate],temp/'frames'/f'candidate_{idx}.jpg',dense=True,layout='fit')
                    context=[s for s in transcript.get('segments',[]) if s['end']>candidate['start']-15 and s['start']<candidate['end']+15]
                    try:
                        analysis=await MovieSceneAnalyzer.understand(provider,name,sheet,candidate,context,settings)
                        invalid_run=0
                        start,end=MovieQualityControl.safe_bounds(analysis,transcript)
                        if settings.get('whole_video',True) and candidate['start']==0 and abs(candidate['end']-info['duration'])<.01 and info['duration']<=MAX_SHORT_SECONDS:
                            start,end=candidate['start'],candidate['end']      # keep the video whole; do not trim to speech edges
                        if not candidate['start']<=start<end<=candidate['end']:raise ValueError('Safe dialogue bounds escape the reviewed candidate.')
                        analysis.update(start=start,end=end)
                        if MovieMomentScorer.qualifies(analysis):analyses.append(analysis)
                        else:rejected.append({'start':start,'end':end,'reason':'Movie editorial or clean-source quality gate rejected this moment.'})
                    except ValueError as exc:
                        rejected.append({'start':candidate['start'],'end':candidate['end'],'reason':str(exc)})
                        invalid_run+=1
                        if invalid_run>=3:
                            save()
                            raise ValueError('Movie vision returned invalid analysis for three consecutive candidates. Reconnect the model or retry. '+str(exc)[-350:]) from exc
                checkpoint['analyses']=analyses;save()
            if 'verified_analyses' not in checkpoint and hasattr(provider,'analyze_video'):
                # Only bounded finalists are sent as video, never the full movie.
                # Listening before production protects dialogue and grounds plot claims.
                finalists=MovieSceneAnalyzer.diverse(checkpoint['analyses'],min(10,max(6,count*2)))
                verified=[]
                for idx,candidate in enumerate(finalists):
                    progress('ANALYZING',52,f'Watching and listening to finalist {idx+1}/{len(finalists)}')
                    proxy=temp/'source-review'/f'finalist_{idx}.mp4'
                    proxy.parent.mkdir(parents=True,exist_ok=True)
                    await run_blocking(run_process,['ffmpeg','-v','error','-y','-ss',str(candidate['start']),'-i',str(video),
                        '-t',str(candidate['end']-candidate['start']),'-vf','scale=960:-2,fps=12','-c:v','libx264',
                        '-crf','28','-preset','veryfast','-maxrate','1100k','-bufsize','2200k','-c:a','aac','-b:a','96k',str(proxy)])
                    sheet=await run_blocking(MomentAnalyzer.contact_sheet,video,[candidate],temp/'frames'/f'finalist_{idx}.jpg',dense=True,layout='fit')
                    try:
                        checked=await MovieSceneAnalyzer.understand(provider,name,sheet,candidate,
                            [s for s in transcript['segments'] if s['end']>candidate['start'] and s['start']<candidate['end']],
                            settings,video_proxy=proxy)
                        start,end=MovieQualityControl.safe_bounds(checked,transcript)
                        if settings.get('whole_video',True) and candidate['start']==0 and abs(candidate['end']-info['duration'])<.01 and info['duration']<=MAX_SHORT_SECONDS:
                            start,end=candidate['start'],candidate['end']
                        if not candidate['start']<=start<end<=candidate['end']:raise ValueError('Verified speech bounds escape the candidate.')
                        checked.update(start=start,end=end)
                        if MovieMomentScorer.qualifies(checked):verified.append(checked)
                        else:rejected.append({'start':start,'reason':'Actual source-video review rejected the proposed moment.'})
                    except ValueError as exc:rejected.append({'start':candidate['start'],'reason':str(exc)})
                    finally:proxy.unlink(missing_ok=True)
                checkpoint['verified_analyses']=verified;save()
            progress('ANALYZING',55,'Choosing the best presentation for each scene')
            options=[]
            for analysis in checkpoint.get('verified_analyses',checkpoint['analyses']):
                speech=MovieQualityControl.captions(transcript,analysis['start'],analysis['end'])
                track=CinematicMusicDirector.select(analysis['mood'])
                original_music=info['has_audio'] and provenance['source_audio_authorized']
                decision=MovieFormatDirector.choose(analysis,bool(speech),info['has_audio'],bool(track or original_music))
                if decision['format']=='REJECT':rejected.append({'start':analysis['start'],'reason':decision['reason']});continue
                options.append({**analysis,'format':decision['format'],'audio_decision':decision,'music_track':track})
            selections=MovieSceneAnalyzer.diverse(options,count)
            if not selections:raise ValueError('No strong publishable moments were found. Try another authorized source or add mood-matched licensed music.')
            for idx,analysis in enumerate(selections):
                accepted=None;feedback=None;prepared_narration=None
                key=f'{analysis["start"]:.3f}_{analysis["end"]:.3f}'
                speech_cache=checkpoint.setdefault('selected_transcripts',{})
                if key not in speech_cache:
                    speech_cache[key]=await run_blocking(MovieQualityControl.source_speech,video,analysis['start'],analysis['end'],
                        temp/'audio'/f'selected_{idx}.wav') if info['has_audio'] else {'available':True,'segments':[],'text':''}
                    save()
                selected_transcript=speech_cache[key]
                for attempt in range(3):
                    try:
                        cls.resources(temp)
                        folder=temp/'renders'/f'clip_{idx}'/f'attempt_{attempt}';folder.mkdir(parents=True,exist_ok=True)
                        start,end=analysis['start'],analysis['end'];duration=end-start;format=analysis['format']
                        ProductionQC.require_duration(duration)
                        original=MovieQualityControl.captions(selected_transcript,start,end)
                        regions=[ProtectedDialogueRegion(start=s['start'],end=s['end']).model_dump() for s in original]
                        voice,speech,offset=None,None,0.
                        music_wav,music=None,None
                        if format=='COMMENTARY':
                            progress('GENERATING_VOICE',60+round(idx*23/len(selections)),'Creating commentary for the full Short')
                            voice=folder/'narration.wav'
                            try:
                                proxy=folder/'commentary_source.mp4'
                                await run_blocking(run_process,['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(video),
                                    '-t',str(duration),'-vf','scale=960:-2,fps=12','-c:v','libx264','-crf','28','-preset','veryfast',
                                    '-maxrate','1100k','-bufsize','2200k','-c:a','aac','-b:a','96k',str(proxy)])
                                sheet=await run_blocking(MomentAnalyzer.contact_sheet,video,[analysis],folder/'commentary_source.jpg',dense=True)
                                if prepared_narration:
                                    script,speech,voice=prepared_narration
                                else:
                                    script=await MovieNarrationDirector.write(provider,proxy,sheet,duration,transcript.get('text',''),feedback)
                                    speech=await MovieNarrationDirector.synthesize_timed(script,voice,MovieFormatDirector.voice(analysis['mood'],settings.get('default_voice')),provider)
                                analysis={**analysis,'commentary':script['text'],'narration_evidence':script,
                                    'verified_source_speech':transcript.get('text','')}
                                offset=MovieQualityControl.narration_slot(regions,duration,speech['duration'])
                                if speech['duration']<duration-2 or speech['duration']>duration-.15:
                                    desired=max(5,round(len(script['text'].split())*(duration-1)/speech['duration']))
                                    raise ValueError(f'Actual narration lasts {speech["duration"]:.2f}s for {duration:.2f}s footage. Rewrite to about {desired} words to last {duration-1:.2f}s naturally; do not speed up audio.')
                                prepared_narration=(script,speech,voice)
                            except ValueError as exc:
                                feedback=str(exc)
                                continue
                        elif format=='AESTHETIC':
                            if analysis.get('music_track'):
                                music_wav,music=await run_blocking(CinematicMusicDirector.prepare,analysis['music_track'],folder/'music',analysis['payoff_timestamp']-start,duration)
                                music['rights_passed']=RightsPolicyEngine.evaluate([music['provenance']],'documented_permission')['passed']
                            else:
                                # The source's authorized score/ambience already fits this scene.
                                music={'track_name':'Original movie soundtrack and ambience','source':provenance['source_url'],
                                    'license':provenance['license'],'proof_reference':provenance.get('license_evidence_url'),
                                    'rights_passed':rights['passed'] and provenance['source_audio_authorized'],
                                    'usage_rights':rights['reason'],'method':'Preserve original cinematic sound and edit rhythm',
                                    'provenance':provenance}
                        captions=list(original) if format!='AESTHETIC' else []
                        if speech:
                            shifted=[{**w,'start':w['start']+offset,'end':w['end']+offset} for w in speech['words']]
                            captions.append({'start':shifted[0]['start'],'end':shifted[-1]['end'],'text':speech['text'],'words':shifted})
                            captions.sort(key=lambda s:s['start'])
                        # Keep useful actor lines in cinematic footage captioned if any are retained.
                        if format=='AESTHETIC' and original:captions=original
                        caption_file=await run_blocking(CaptionRenderer.write_ass,folder/'captions.ass',captions,0,duration) if captions else None
                        hook=await run_blocking(CaptionRenderer.render_overlay_card,folder/'hook.png',title=analysis['header_text']) if analysis['header_text'] and attempt<2 else None
                        # Attribution belongs in publish descriptions and metadata,
                        # never a copyright message burned over the movie.
                        credit=None
                        portrait=settings.get('movie_layout','fill')=='fill'
                        framing=(await run_blocking(MovieReframing.plan,video,start,end,format,True,'motion' if attempt==1 else 'faces')
                            if portrait else await run_blocking(MovieReframing.plan,video,start,end,format) if attempt==0
                            else {'layout':'fit','reason':'Repair preserves the complete source composition.'})
                        if portrait:
                            framing=await MovieReframing.refine(provider,video,start,end,folder/'crop-review',framing)
                        timeline=[{'timeline_start':0.,'duration':duration,'source_start':start,'source_end':end,
                            'payoff_relative':analysis['payoff_timestamp']-start,'narration':speech['text'] if speech else '',
                            'speech':speech,'narration_offset':offset,'original_speech':original,'protected_dialogue':regions,'format':format,
                            'commentary_scope':'full_short' if format=='COMMENTARY' else None}]
                        MovieQualityControl.validate_format(format,timeline,music)
                        progress('RENDERING',62+round(idx*23/len(selections)),f'Creating {format.lower()} moment {idx+1}')
                        rendered=await run_blocking(VideoReframer.reframe_to_vertical,video,folder/'edited.mp4',start=start,duration=duration,
                            overlay_png=hook,captions_ass=caption_file,narration_wav=voice,narration_offset=offset,
                            protected_regions=regions,music_wav=music_wav,framing=framing,layout='fill' if portrait else 'fit',hook_duration=2.5,credit_png=credit)
                        master=await run_blocking(ProductionQC.master_audio,rendered,folder/'movie_clip.mp4',settings)
                        progress('QC',86,'Checking the actual finished movie clip')
                        try:
                            ProductionQC.require_duration(FFmpegCore.get_video_info(master)['duration'])
                            qc=await run_blocking(ProductionQC.inspect,master,duration,folder/'qc',timeline,False)
                            review=await MovieQualityControl.review(provider,name,qc,analysis,format,timeline,music,feedback)
                            record_review(analysis,attempt,review)
                            if not review['passed']:
                                feedback=json.dumps(review,ensure_ascii=False)
                                if review.get('commentary_grounded') is False or review.get('no_spoilers') is False or review.get('format_correct') is False:
                                    prepared_narration=None
                                continue
                        except ValueError as exc:
                            feedback=str(exc);record_review(analysis,attempt,{'passed':False,'reason':feedback,'method':'Objective final MP4 QC'});continue
                        qc['captions_checked']=bool(captions);qc['format_checked']=True
                        accepted={'file':master,'analysis':analysis,'format':format,'timeline':timeline,'music':music,'framing':framing,
                            'duration':duration,'qc':{k:v for k,v in qc.items() if k not in ('proxy','sheets','info')},'final_review':review,
                            'production_attempts':attempt+1};break
                    except (ValueError,RuntimeError,TimeoutError) as exc:
                        feedback=str(exc);continue
                if accepted:records.append(accepted)
                else:rejected.append({'start':analysis['start'],'reason':'Movie final QC rejected after three repairs: '+str(feedback)[-500:]})
            if not records:raise ValueError('No strong publishable moments were found. Every rendered candidate failed final quality review.')
            progress('RENDERING',95,'Saving your finished movie Shorts')
            metadata_records=[]
            for idx,result in enumerate(records):
                clip_id=f'{project_id}_{job_id}_movie_clip_{idx+1:02}'
                final=await run_blocking(StorageManager.move_final_clip,result['file'],'movie',clip_id);published.append(final)
                preview=final.with_name(clip_id+'_preview.jpg')
                await run_blocking(FFmpegCore.extract_frame,final,preview,.6);published.append(preview)
                database.create_or_update_clip(clip_id=clip_id,project_id=project_id,job_id=job_id,title=result['analysis']['title'],
                    subtitle={'DIALOGUE':'Dialogue Moment','COMMENTARY':'Commentary Moment','AESTHETIC':'Cinematic Moment'}[result['format']],
                    duration=round(result['duration'],2),reason=result['analysis']['description'],status='READY',
                    preview_path=f'/output/movie/{preview.name}',video_path=f'/output/movie/{final.name}')
                row={k:v for k,v in result.items() if k!='file'}
                row.update(clip_id=clip_id,source_url=provenance['source_url'],creator=provenance['creator'],license=provenance['license'],
                    attribution=provenance['attribution'],license_evidence_url=provenance['license_evidence_url'],
                    script={'hook':result['analysis']['header_text'],'narration':result['analysis']['commentary'] if result['format']=='COMMENTARY' else '',
                        'observed_action':result['analysis']['description']},final_score=MovieMomentScorer.score(result['analysis']))
                metadata_records.append(row)
            data={'production_qc_passed':True,'sources':[provenance],'moments':metadata_records,'rights':rights,
                'analysis_archive':checkpoint,
                'preprocessing':checkpoint.get('preprocessing',{}),'source_hash':fingerprint,'source_cleanliness':checkpoint['source_cleanliness'],
                'requested_count':count,'generated_count':len(records),'rejected_moments':rejected,'scores_are_estimates':True,
                'voice_provenance':[b['speech']['provenance'] for r in records for b in r['timeline'] if (b.get('speech') or {}).get('provenance')],
                'warnings':[f'{len(records)} strong movie moments passed QC; {count} requested.'] if len(records)<count else []}
            metadata_path=config.MOVIE_OUTPUT_DIR/(project_id+'_metadata.json')
            metadata_path.write_text(json.dumps(data,indent=2,ensure_ascii=False))
            database.update_project(project_id,title='Movie moments · '+provenance['title'],result_data=data,status='COMPLETED')
            finished=True
            progress('CLEANING',98,'Removing temporary movie footage after final QC')
            progress('COMPLETED',100,'Your movie clips are ready')
        except asyncio.CancelledError:raise
        except Exception as exc:
            for path in published:path.unlink(missing_ok=True)
            database.update_project(project_id,status='FAILED',result_data={'movie_checkpoint':checkpoint,'rejected_moments':rejected})
            database.record_job_failure(job_id,exc)
            on_progress('FAILED',0,'Failed');raise
        finally:
            StorageManager.cleanup_job_temp(job_id)

import json
import logging
from pathlib import Path
from app.core.config import VIRAL_OUTPUT_DIR
from app.core.database import update_job, update_project, create_or_update_clip, record_job_failure
from app.core.runtime import run_blocking
from app.storage.manager import StorageManager
from app.sources.ingestion import SourceIngestion
from app.sources.reuse import SourceReuse, source_keys, file_fingerprint
from app.ai.router import AIRouter
from app.ai.production_director import ProductionDirector
from app.ai.ranking_verifier import RankingVerifier
from app.core.runtime import run_process
from app.media.ffmpeg_core import FFmpegCore
from app.media.reframer import VideoReframer
from app.media.captions import CaptionRenderer, clean_label
from app.media.moments import MomentAnalyzer
from app.media.production_qc import ProductionQC
from app.transcription.transcriber import Transcriber
from app.tts.voice_engine import TTSEngine
from app.tts.alignment import NarrationAlignmentError

logger = logging.getLogger('ai_shorts.viral_pipeline')
from app.media.verify import MAX_SHORT_SECONDS
from app.core.qc import enabled as qc_on   # YouTube Shorts hold at most 3 minutes


class ViralPipeline:
    @classmethod
    async def run(cls,job_id,project_id,video_source,count,settings,on_progress):
        temp=StorageManager.get_job_temp_dir(job_id)
        from app.studio.strictness import production_level
        bar=production_level()
        published=[]
        rejected=[]
        completed=False
        def progress(status,value,stage):
            update_job(job_id,status=status,progress=value,current_stage=stage)
            on_progress(status,value,stage)
        try:
            if not video_source: raise ValueError('A video URL or uploaded file is required.')
            provider,name=await AIRouter.get_active_provider(settings)
            if not provider: raise ValueError('Production export requires a connected vision model for story and final-video QC. Connect Gemini or local vision in Settings.')
            progress('INGESTING',10,'Loading real source footage')
            if str(video_source).startswith(('http://','https://')):
                video,metadata=await run_blocking(SourceIngestion.download_video,video_source,temp/'downloads'/'source.mp4')
            else:
                video=await run_blocking(SourceIngestion.ingest_video_file,Path(video_source),temp/'downloads'/'source.mp4')
                metadata={'title':settings.get('source_title') or Path(video_source).stem,'url':None,'source_id':project_id}
            info=await run_blocking(FFmpegCore.get_video_info,video)
            ProductionQC.require_duration(info['duration'])
            metadata['source_sha256']=await run_blocking(file_fingerprint,video)
            if qc_on() and min(info['width'],info['height'])<360: raise ValueError('Source resolution is below the production quality floor. Supply clearer footage.')
            transcription={'segments':[],'text':''}
            if info['has_audio'] and not settings.get('visuals_only'):
                progress('TRANSCRIBING',25,'Recognizing speech and actual caption word boundaries')
                audio=await run_blocking(FFmpegCore.extract_audio,video,temp/'audio'/'source.wav')
                transcription=await run_blocking(Transcriber.transcribe,audio)
                if not transcription.get('available',True): raise ValueError('Speech transcription failed. Production exports cannot omit required captions.')
            progress('ANALYZING',42,'Finding complete standalone stories')
            whole=bool(settings.get('whole_video'))
            narrate=not settings.get('no_narration')
            if whole:
                # The whole video, not just the window discovery pointed at. A Short cannot exceed MAX_SHORT_SECONDS.
                base=dict(settings.get('selected_moment') or {'score':60,'title':metadata['title'],'reason':'The whole source video.'})
                chosen={**base,'start':0.0,'end':float(min(info['duration'],MAX_SHORT_SECONDS))}
                if info['duration']>MAX_SHORT_SECONDS:
                    rejected.append({'start':MAX_SHORT_SECONDS,'end':info['duration'],'reason':f'The source is {info["duration"]:.0f}s; a Short holds at most {MAX_SHORT_SECONDS:.0f}s, so the first {MAX_SHORT_SECONDS:.0f}s were used.'})
                candidates=[chosen]
            elif settings.get('selected_moment'):
                chosen=dict(settings['selected_moment'])
                if chosen['end']-chosen['start']<ProductionQC.min_duration():
                    # Use real surrounding context and aftermath, then verify
                    # the expanded story again. Never repeat/freeze footage.
                    extra=ProductionQC.min_duration()-(chosen['end']-chosen['start'])
                    chosen['end']=min(info['duration'],chosen['end']+extra)
                    chosen['start']=max(0,chosen['end']-ProductionQC.min_duration())
                candidates=[chosen]
            else:
                candidates=await run_blocking(MomentAnalyzer.analyze,video,count=count,target_duration=settings.get('target_duration',25),
                    segments=transcription['segments'],title=metadata['title'],minimum_duration=ProductionQC.min_duration())
            moments=MomentAnalyzer.clamp_selections(candidates,info['duration'],count)
            fresh=[]
            for moment in moments:
                if moment['end']-moment['start']<ProductionQC.min_duration():
                    rejected.append({**moment,'reason':'The source moment must be longer than 10 seconds.'})
                elif SourceReuse.overlaps(metadata,moment['start'],moment['end'],project_id):
                    rejected.append({**moment,'reason':'This source moment already appears in another Short. Supply fresh footage.'})
                elif any(min(moment['end'],m['end'])-max(moment['start'],m['start'])>.05 for m in fresh):
                    rejected.append({**moment,'reason':'This source moment overlaps another output in this production.'})
                else:fresh.append(moment)
            moments=fresh
            accepted=[]
            for idx,moment in enumerate(moments):
                base=50+idx*45/len(moments)
                span=45/len(moments)
                evidence=await run_blocking(MomentAnalyzer.contact_sheet,video,[moment],temp/'frames'/f'story_{idx}.jpg',dense=True)
                actual_speech=[s for s in transcription['segments'] if s['end']>moment['start'] and s['start']<moment['end']]
                feedback=None
                failed_lines=[]
                for attempt in range(3):
                    progress('SCRIPTING',50+round(idx/len(moments)*35),f'Producing standalone Short {idx+1} · attempt {attempt+1}')
                    try:
                        if settings.get('force_commentary'):
                            plan=await ProductionDirector.plan_viral(provider,name,moment,evidence,actual_speech,feedback,force_commentary=True,contextual=settings.get('contextual_commentary',False),failed_lines=failed_lines,level=bar,narrate=narrate,style=settings.get('style'),whole=whole)
                        else: plan=await ProductionDirector.plan_viral(provider,name,moment,evidence,actual_speech,feedback,failed_lines=failed_lines,level=bar,narrate=narrate,style=settings.get('style'),whole=whole)
                    except ValueError as exc:
                        feedback=str(exc); continue
                    if whole:
                        plan['cuts']=[{'start':moment['start'],'end':moment['end']}]
                    folder=temp/'renders'/f'clip_{idx}'/f'attempt_{attempt}'
                    folder.mkdir(parents=True,exist_ok=True)
                    if settings.get('verified_topic'):
                        proxy=folder/'source_review.mp4'
                        first,last=plan['cuts'][0]['start'],plan['cuts'][-1]['end']
                        await run_blocking(run_process,['ffmpeg','-v','error','-y','-ss',str(first),'-i',str(video),
                            '-t',str(last-first),'-vf','scale=480:-2,fps=10' if last-first>60 else 'scale=640:-2,fps=15','-c:v','libx264','-preset','veryfast',
                            '-crf','32' if last-first>60 else '28','-c:a','aac','-b:a','48k' if last-first>60 else '64k',str(proxy)])
                        independent=await RankingVerifier.review(provider,name,{'label':plan['title'],'commentary':plan.get('narration') or plan['observed_action']},
                            evidence,settings['verified_topic'],video_path=proxy,level=bar)
                        if not independent['passed']:
                            feedback=independent['reason'];continue
                        plan['independent_source_review']=independent
                    segments,timeline,cursor=[],[],0.
                    for cut_idx,cut in enumerate(plan['cuts']):
                        duration=cut['end']-cut['start']
                        selected_speech=[]
                        for source_segment in actual_speech:
                            words=Transcriber.words_in_window(source_segment.get('words',[]),cut['start'],cut['end'])
                            if words:
                                ProductionQC.validate_words(words,duration)
                                selected_speech.append({'start':words[0]['start'],'end':words[-1]['end'],'text':' '.join(w['word'] for w in words),'words':words})
                        voice,speech=None,None
                        if narrate and (not actual_speech or settings.get('force_commentary')):
                            progress('GENERATING_VOICE',round(base+span*.15),'Recording natural commentary')
                            voice=folder/f'voice_{cut_idx}.wav'
                            try:
                                if settings.get('voice_synthesizer'): speech=await settings['voice_synthesizer'](plan['narration'],voice)
                                else: speech=await TTSEngine.synthesize_timed(plan['narration'],voice,settings.get('default_voice'))
                            except NarrationAlignmentError as exc:
                                failed_lines.append(plan['narration'])
                                feedback=f'Rewrite this narration in simpler spoken language: {plan["narration"]!r}. {exc}'
                                break
                            if speech['duration']>duration-.15:
                                failed_lines.append(plan['narration'])
                                feedback='Narration exceeds the cut. Rewrite with fewer words or keep more complete footage.'; break
                            source_after=[]
                            for segment in selected_speech:
                                words=[w for w in segment['words'] if w['start']>=speech['duration']+.25]
                                if words:source_after.append({'start':words[0]['start'],'end':words[-1]['end'],
                                    'text':' '.join(w['word'] for w in words),'words':words})
                            selected_speech=[*speech['segments'],*source_after]
                        progress('EDITING',round(base+span*.35),'Reframing actual footage and synchronizing captions')
                        caption_file=await run_blocking(CaptionRenderer.write_ass,folder/f'captions_{cut_idx}.ass',selected_speech,0,duration)
                        heading=bool(settings.get('heading'))
                        overlay=await run_blocking(CaptionRenderer.render_overlay_card,folder/f'hook_{cut_idx}.png',title=plan['hook']) if (cut_idx==0 or heading) else None
                        music,music_info,regions=None,None,[]
                        if settings.get('music')=='sad':
                            progress('EDITING',round(base+span*.4),'Preparing the background music')
                            from app.media.music_gen import sad_bed
                            music,music_info=await run_blocking(sad_bed,folder,duration)
                            # Music ducks under any spoken words in the footage so voices stay clear.
                            for segment in selected_speech:
                                lo,hi=max(0.,segment['start']-.15),min(duration-.001,segment['end']+.2)
                                if hi>lo:
                                    if regions and lo-regions[-1]['end']<.5: regions[-1]['end']=hi
                                    else: regions.append({'start':lo,'end':hi})
                        rendered=await run_blocking(VideoReframer.reframe_to_vertical,video,folder/f'segment_{cut_idx}.mp4',start=cut['start'],duration=duration,
                            overlay_png=overlay,captions_ass=caption_file,narration_wav=voice,layout=settings.get('layout','smart'),
                            music_wav=music,protected_regions=regions,persistent_overlay=heading,
                            music_level=(0.85 if settings.get('mute_source') else 0.24) if music else None,mute_source=bool(settings.get('mute_source')))
                        segments.append(rendered)
                        timeline.append({'timeline_start':cursor,'duration':duration,'source_start':cut['start'],'source_end':cut['end'],
                            'speech':speech,'original_speech':selected_speech,'narration':speech['text'] if speech else ' '.join(s['text'] for s in selected_speech),
                            'music':{'provenance':music_info} if music_info else None,'heading':plan['hook'] if heading else None})
                        cursor+=duration
                    if len(segments)!=len(plan['cuts']): continue
                    progress('RENDERING',round(base+span*.6),'Assembling the finished Short and mixing audio')
                    raw=await run_blocking(FFmpegCore.concatenate_clips,segments,folder/'assembled.mp4')
                    master=await run_blocking(ProductionQC.master_audio,raw,folder/f'viral_clip_{idx+1:02}.mp4',settings)
                    try:
                        ProductionQC.require_duration(cursor)
                        ProductionQC.require_duration(FFmpegCore.get_video_info(master)['duration'])
                        progress('QC',round(base+span*.8),'Reviewing the finished video, captions and audio')
                        qc=await run_blocking(ProductionQC.inspect,master,cursor,folder/'qc',timeline,narrate and (not actual_speech or settings.get('force_commentary',False)))
                    except ValueError as exc:
                        feedback=str(exc); continue
                    review=await ProductionDirector.final_review(provider,name,qc['proxy'],qc['sheets'],settings.get('verified_topic') or plan['observed_action'],timeline,feedback,mode='viral',level=bar,visuals_only=bool(settings.get('visuals_only')))
                    if not review['passed']:
                        feedback=json.dumps(review,ensure_ascii=False); continue
                    accepted.append({'file':master,'duration':cursor,'plan':plan,'timeline':timeline,'moment':moment,
                        'qc':{k:v for k,v in qc.items() if k not in ('proxy','sheets','info')},'final_review':review}); break
                else:
                    rejected.append({'start':moment['start'],'end':moment['end'],'reason':str(feedback)[-1000:]})
                    logger.warning('Skipped viral candidate %s after quality retries: %s',idx+1,feedback)
            if not accepted:
                detail=' '.join(r['reason'] for r in rejected)[-1000:]
                raise ValueError('No standalone Short passed production QC after candidate retries. '+detail)
            staged=[]
            SourceReuse.claim(project_id,job_id,[{'keys':sorted(source_keys(metadata)),'start':cut['source_start'],'end':cut['source_end']}
                for result in accepted for cut in result['timeline']])
            for idx,result in enumerate(accepted):
                clip_id=f'{project_id}_{job_id}_viral_clip_{idx+1:02}'
                final=await run_blocking(StorageManager.move_final_clip,result['file'],'viral',clip_id)
                published.append(final)
                preview=VIRAL_OUTPUT_DIR/f'{clip_id}_preview.jpg'
                await run_blocking(FFmpegCore.extract_frame,final,preview,.6)
                published.append(preview); staged.append((clip_id,result))
            records=[]
            for clip_id,result in staged:
                create_or_update_clip(clip_id=clip_id,project_id=project_id,job_id=job_id,title=result['plan']['title'],
                    subtitle='1080p · Finished audio · QC passed',duration=round(result['duration'],2),viral_score=result['moment']['score'],
                    reason=result['plan']['observed_action'],status='READY',preview_path=f'/output/viral/{clip_id}_preview.jpg',video_path=f'/output/viral/{clip_id}.mp4')
                records.append({**metadata,**result['moment'],'title':result['plan']['title'],'clip_id':clip_id,'script':result['plan'],
                                'timeline':result['timeline'],'qc':result['qc'],'final_review':result['final_review']})
            update_project(project_id,title=f'Clips · {clean_label(accepted[0]["plan"]["title"],6)}',result_data={'sources':[metadata],
                'moments':records,'analysis_basis':f'{name} complete-story and finished-video QC','production_qc_passed':True,'quality_control':'on' if qc_on() else 'off',
                'voice_provenance':[beat['speech']['provenance'] for r in accepted for beat in r['timeline'] if (beat.get('speech') or {}).get('provenance')],
                'requested_count':count,'generated_count':len(records),'scores_are_estimates':True,
                'rejected_moments':rejected,
                'warnings':([f'{len(records)} of {count} requested Shorts passed quality review; {len(rejected)} candidate(s) were excluded.']
                    if rejected else [f'Source supports {len(records)} distinct complete stories; {count} requested.']) if len(records)<count else []})
            progress('CLEANING',98,'Removing managed job intermediates after finished video QC')
            StorageManager.cleanup_job_temp(job_id)
            update_project(project_id,status='COMPLETED')
            progress('COMPLETED',100,'Finished production Shorts ready')
            completed=True
        except Exception as exc:
            SourceReuse.release(job_id)
            for path in published: path.unlink(missing_ok=True)
            logger.exception('Viral production failed for %s',job_id)
            record_job_failure(job_id,exc)
            update_project(project_id,status='FAILED',result_data={'rejected_moments':rejected})
            on_progress('FAILED',0,'Failed'); raise
        finally:
            if completed:
                StorageManager.cleanup_job_temp(job_id)

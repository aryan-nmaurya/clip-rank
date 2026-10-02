import json
import logging
from pathlib import Path
from app.core.config import VIRAL_OUTPUT_DIR
from app.core.database import update_job, update_project, create_or_update_clip
from app.core.runtime import run_blocking
from app.storage.manager import StorageManager
from app.sources.ingestion import SourceIngestion
from app.ai.router import AIRouter
from app.ai.production_director import ProductionDirector
from app.media.ffmpeg_core import FFmpegCore
from app.media.reframer import VideoReframer
from app.media.captions import CaptionRenderer, clean_label
from app.media.moments import MomentAnalyzer
from app.media.production_qc import ProductionQC
from app.transcription.transcriber import Transcriber
from app.tts.voice_engine import TTSEngine
from app.tts.alignment import NarrationAlignmentError

logger = logging.getLogger('ai_shorts.viral_pipeline')


class ViralPipeline:
    @classmethod
    async def run(cls,job_id,project_id,video_source,count,settings,on_progress):
        temp=StorageManager.get_job_temp_dir(job_id)
        published=[]
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
            if min(info['width'],info['height'])<360: raise ValueError('Source resolution is below the production quality floor. Supply clearer footage.')
            transcription={'segments':[],'text':''}
            if info['has_audio']:
                progress('TRANSCRIBING',25,'Recognizing speech and actual caption word boundaries')
                audio=await run_blocking(FFmpegCore.extract_audio,video,temp/'audio'/'source.wav')
                transcription=await run_blocking(Transcriber.transcribe,audio)
                if not transcription.get('available',True): raise ValueError('Speech transcription failed. Production exports cannot omit required captions.')
            progress('ANALYZING',42,'Finding complete standalone stories')
            if settings.get('selected_moment'):
                candidates=[settings['selected_moment']]
            else:
                candidates=await run_blocking(MomentAnalyzer.analyze,video,count=count,target_duration=settings.get('target_duration',25),
                    segments=transcription['segments'],title=metadata['title'])
            moments=MomentAnalyzer.clamp_selections(candidates,info['duration'],count)
            accepted=[]
            for idx,moment in enumerate(moments):
                evidence=await run_blocking(MomentAnalyzer.contact_sheet,video,[moment],temp/'frames'/f'story_{idx}.jpg',dense=True)
                actual_speech=[s for s in transcription['segments'] if s['end']>moment['start'] and s['start']<moment['end']]
                feedback=None
                for attempt in range(3):
                    progress('SCRIPTING',50+round(idx/len(moments)*35),f'Producing standalone Short {idx+1} · attempt {attempt+1}')
                    try:
                        if settings.get('force_commentary'):
                            plan=await ProductionDirector.plan_viral(provider,name,moment,evidence,actual_speech,feedback,force_commentary=True,contextual=settings.get('contextual_commentary',False))
                        else: plan=await ProductionDirector.plan_viral(provider,name,moment,evidence,actual_speech,feedback)
                    except ValueError as exc:
                        feedback=str(exc); continue
                    folder=temp/'renders'/f'clip_{idx}'/f'attempt_{attempt}'
                    folder.mkdir(parents=True,exist_ok=True)
                    segments,timeline,cursor=[],[],0.
                    for cut_idx,cut in enumerate(plan['cuts']):
                        duration=cut['end']-cut['start']
                        selected_speech=[]
                        for source_segment in actual_speech:
                            words=[{**w,'start':w['start']-cut['start'],'end':w['end']-cut['start']} for w in source_segment.get('words',[])
                                if cut['start']-.03<=w['start'] and w['end']<=cut['end']+.03]
                            if words:
                                for w in words: w['start']=max(0,w['start']); w['end']=min(duration,w['end'])
                                ProductionQC.validate_words(words,duration)
                                selected_speech.append({'start':words[0]['start'],'end':words[-1]['end'],'text':' '.join(w['word'] for w in words),'words':words})
                        voice,speech=None,None
                        if not actual_speech or settings.get('force_commentary'):
                            voice=folder/f'voice_{cut_idx}.wav'
                            try:
                                if settings.get('voice_synthesizer'): speech=await settings['voice_synthesizer'](plan['narration'],voice)
                                else: speech=await TTSEngine.synthesize_timed(plan['narration'],voice,settings.get('default_voice'))
                            except NarrationAlignmentError as exc:
                                feedback=f'Rewrite this narration in simpler spoken language: {plan["narration"]!r}. {exc}'
                                break
                            if speech['duration']>duration-.15:
                                feedback='Narration exceeds the cut. Rewrite with fewer words or keep more complete footage.'; break
                            source_after=[]
                            for segment in selected_speech:
                                words=[w for w in segment['words'] if w['start']>=speech['duration']+.25]
                                if words:source_after.append({'start':words[0]['start'],'end':words[-1]['end'],
                                    'text':' '.join(w['word'] for w in words),'words':words})
                            selected_speech=[*speech['segments'],*source_after]
                        caption_file=await run_blocking(CaptionRenderer.write_ass,folder/f'captions_{cut_idx}.ass',selected_speech,0,duration)
                        overlay=await run_blocking(CaptionRenderer.render_overlay_card,folder/f'hook_{cut_idx}.png',title=plan['hook']) if cut_idx==0 else None
                        rendered=await run_blocking(VideoReframer.reframe_to_vertical,video,folder/f'segment_{cut_idx}.mp4',start=cut['start'],duration=duration,
                            overlay_png=overlay,captions_ass=caption_file,narration_wav=voice,layout='fit')
                        segments.append(rendered)
                        timeline.append({'timeline_start':cursor,'duration':duration,'source_start':cut['start'],'source_end':cut['end'],
                            'speech':speech,'original_speech':selected_speech,'narration':speech['text'] if speech else ' '.join(s['text'] for s in selected_speech)})
                        cursor+=duration
                    if len(segments)!=len(plan['cuts']): continue
                    raw=await run_blocking(FFmpegCore.concatenate_clips,segments,folder/'assembled.mp4')
                    master=await run_blocking(ProductionQC.master_audio,raw,folder/f'viral_clip_{idx+1:02}.mp4')
                    try:
                        qc=await run_blocking(ProductionQC.inspect,master,cursor,folder/'qc',timeline,not actual_speech or settings.get('force_commentary',False))
                    except ValueError as exc:
                        feedback=str(exc); continue
                    review=await ProductionDirector.final_review(provider,name,qc['proxy'],qc['sheets'],settings.get('verified_topic') or plan['observed_action'],timeline,feedback,mode='viral')
                    if not review['passed']:
                        feedback=json.dumps(review,ensure_ascii=False); continue
                    accepted.append({'file':master,'duration':cursor,'plan':plan,'timeline':timeline,'moment':moment,
                        'qc':{k:v for k,v in qc.items() if k not in ('proxy','sheets','info')},'final_review':review}); break
                else:
                    raise ValueError('Standalone Short did not pass production QC after three attempts. '+str(feedback)[-600:])
            if not accepted: raise ValueError('No complete production-ready stories were found.')
            staged=[]
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
                'moments':records,'analysis_basis':f'{name} complete-story and finished-video QC','production_qc_passed':True,
                'voice_provenance':[beat['speech']['provenance'] for r in accepted for beat in r['timeline'] if (beat.get('speech') or {}).get('provenance')],
                'requested_count':count,'generated_count':len(records),'scores_are_estimates':True,
                'warnings':[f'Source supports {len(records)} distinct complete stories; {count} requested.'] if len(records)<count else []})
            progress('CLEANING',98,'Removing managed job intermediates after finished video QC')
            StorageManager.cleanup_job_temp(job_id)
            update_project(project_id,status='COMPLETED')
            progress('COMPLETED',100,'Finished production Shorts ready')
            completed=True
        except Exception as exc:
            for path in published: path.unlink(missing_ok=True)
            logger.exception('Viral production failed for %s',job_id)
            update_job(job_id,status='FAILED',current_stage='Failed',error_message=str(exc)[:1000],detailed_error=str(exc))
            update_project(project_id,status='FAILED')
            on_progress('FAILED',0,'Failed'); raise
        finally:
            if completed:
                StorageManager.cleanup_job_temp(job_id)

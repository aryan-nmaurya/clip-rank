import json
import logging
from pathlib import Path
from app.core.config import RANKING_OUTPUT_DIR
from app.core.database import update_job, update_project, create_or_update_clip
from app.core.runtime import run_blocking
from app.storage.manager import StorageManager
from app.ai.router import AIRouter
from app.ai.ranking_verifier import RankingVerifier
from app.ai.production_director import ProductionDirector
from app.sources.discovery import SourceDiscovery
from app.pipelines.ranking.topic_parser import TopicParser
from app.pipelines.ranking.scorer import Deduplicator
from app.tts.voice_engine import TTSEngine
from app.tts.alignment import NarrationAlignmentError
from app.transcription.transcriber import Transcriber
from app.media.reframer import VideoReframer
from app.media.captions import CaptionRenderer
from app.media.ffmpeg_core import FFmpegCore
from app.media.moments import MomentAnalyzer
from app.media.source_screening import SourceScreening
from app.media.production_qc import ProductionQC

logger = logging.getLogger('ai_shorts.ranking_pipeline')
MAX_PRODUCTION_ATTEMPTS = 3


class RankingPipeline:
    @classmethod
    async def run(cls, job_id, project_id, topic, count, settings, on_progress):
        temp = StorageManager.get_job_temp_dir(job_id)
        rejections, published = [], []
        completed = False
        def progress(status, value, stage):
            update_job(job_id,status=status,progress=value,current_stage=stage)
            on_progress(status,value,stage)
        try:
            topic,inferred = TopicParser.parse_topic(topic,count or 5)
            count = count or inferred
            progress('ANALYZING',4,'Checking vision and natural speech connections')
            provider_info = await AIRouter.require_ranking_provider(settings)
            if settings.get('voice_synthesizer'): await settings['voice_synthesizer']('Watch the landing.',temp/'voice'/'connection.wav')
            else: await TTSEngine.synthesize_timed('Watch the landing.',temp/'voice'/'connection.wav',settings.get('default_voice'))
            progress('INGESTING',9,'Finding raw individual videos for two distinct Shorts')
            sources = await run_blocking(SourceDiscovery.discover_candidate_videos,topic,count,temp,
                source_urls=settings.get('source_urls'),source_files=settings.get('source_files'),
                source_titles=settings.get('source_titles'),source_platforms=settings.get('source_platforms'),rejections=rejections)
            moments = []
            for idx,source in enumerate(sources):
                progress('ANALYZING',20+round(idx/len(sources)*30),f'Verifying source {idx+1}/{len(sources)}')
                info = await run_blocking(FFmpegCore.get_video_info,Path(source['file_path']))
                if min(info['width'],info['height']) < 360:
                    rejections.append({'title':source['title'],'url':source.get('url'),'reason':'Source resolution is below the production quality floor (360 pixels on its short side).'})
                    continue
                screen = await run_blocking(SourceScreening.inspect,source,temp/'screening'/source['id'])
                if screen['rejected']:
                    rejections.append({'title':source['title'],'url':source.get('url'),'reason':screen['reason']}); continue
                verified = await AIRouter.screen_ranking_source(screen['sheet'],source,settings,provider_info=provider_info,required=True)
                if not verified['suitable_raw'] or verified['already_ranked'] or verified['compilation']:
                    rejections.append({'title':source['title'],'url':source.get('url'),'reason':verified.get('reason') or 'Existing ranking/compilation.'}); continue
                source['screening'] = {'method':screen['method']+' + '+verified['method'],'sample_count':screen['sample_count'],'passed':True}
                selected_window=settings.get('source_windows',{}).get(source.get('url'))
                if selected_window: candidates=[selected_window]
                else:
                    candidates = await run_blocking(MomentAnalyzer.analyze,Path(source['file_path']),count=8,
                        target_duration=settings.get('segment_duration',7),title=source['title'],coverage=True)
                candidates = [m for m in candidates if m['clarity'] >= .45]
                if not candidates:
                    rejections.append({'title':source['title'],'reason':'No visually clear candidate windows.'}); continue
                sheet = await run_blocking(MomentAnalyzer.contact_sheet,Path(source['file_path']),candidates,temp/'frames'/f'source_{idx}.jpg',dense=True)
                revised,_ = await AIRouter.evaluate_moments(candidates,sheet,settings,topic=topic,provider_info=provider_info)
                chosen,reason = None,f'No complete, confidently verified {topic} event.'
                for proposal in revised:
                    clamped = MomentAnalyzer.clamp_selections([proposal],source['duration'],1)
                    if not clamped: continue
                    candidate = clamped[0]
                    review_sheet = await run_blocking(MomentAnalyzer.contact_sheet,Path(source['file_path']),[candidate],
                        temp/'frames'/f'review_{idx}_{candidate["start"]}.jpg',dense=True,layout='fit')
                    review = await RankingVerifier.review(*provider_info,candidate,review_sheet,topic)
                    if review['passed']:
                        chosen = {**candidate,'final_review':review}; break
                    reason = review['reason']
                if not chosen:
                    rejections.append({'title':source['title'],'url':source.get('url'),'reason':reason}); continue
                source_transcript = {'segments':[],'text':''}
                if info['has_audio']:
                    audio = await run_blocking(FFmpegCore.extract_audio,Path(source['file_path']),temp/'audio'/f'{source["id"]}.wav')
                    source_transcript = await run_blocking(Transcriber.transcribe,audio)
                    if not source_transcript.get('available',True):
                        raise ValueError('Source speech could not be checked for captions. Resolve transcription before production export.')
                moments.append({**source,**chosen,'source_transcript':source_transcript})
            pool = Deduplicator.deduplicate_moments(moments)
            if len(pool) < count:
                raise ValueError(f'Only {len(pool)} individual clips passed screening for Top {count}; excluded {len(rejections)} unsuitable sources. Add raw clips or choose another topic. Existing rankings are never used as replacements.')
            (temp/'approved_pool.json').write_text(json.dumps({'topic':topic,'count':count,'pool':pool},ensure_ascii=False))
            await cls.produce_verified_pool(job_id,project_id,topic,count,settings,provider_info,temp,pool,sources,rejections,published,progress)
            progress('CLEANING',98,'Removing managed job intermediates after both finished videos pass QC')
            StorageManager.cleanup_job_temp(job_id)
            update_project(project_id,status='COMPLETED')
            completed = True
            progress('COMPLETED',100,'Two finished 1080p ranking Shorts ready')
        except Exception as exc:
            for path in published: path.unlink(missing_ok=True)
            logger.exception('Ranking production failed for %s',job_id)
            update_job(job_id,status='FAILED',current_stage='Failed',error_message=str(exc)[:1000],detailed_error=str(exc))
            update_project(project_id,status='FAILED',result_data={'rejected_sources':rejections})
            on_progress('FAILED',0,'Failed')
            raise
        finally:
            if completed:
                StorageManager.cleanup_job_temp(job_id)

    @classmethod
    async def produce_verified_pool(cls,job_id,project_id,topic,count,settings,provider_info,temp,pool,sources,rejections,published,progress,editorial_feedback=None):
        """Render from server-verified evidence, shared by normal jobs and controlled repairs."""
        if len(pool)<count or any(not m.get('topic_verified') or m.get('verified_topic')!=topic or m.get('graphic_injury') is not False for m in pool):
            raise ValueError('Production requires a complete pool of topic-verified source cuts.')
        progress('ANALYZING',53,'Comparing verified payoffs to establish genuine ranking escalation')
        sheets=[]
        for idx,moment in enumerate(pool):
            sheets.append(await run_blocking(MomentAnalyzer.contact_sheet,Path(moment['file_path']),[moment],
                temp/'frames'/f'comparative_{idx}.jpg',dense=True))
        pool=await ProductionDirector.rank_pool(*provider_info,pool,sheets,topic)
        (temp/'comparative_pool.json').write_text(json.dumps(pool,ensure_ascii=False,default=str))
        feedback,accepted = editorial_feedback,None
        for attempt in range(MAX_PRODUCTION_ATTEMPTS):
            progress('SCRIPTING',55,f'Writing distinct A/B hooks and curiosity bridges · attempt {attempt+1}')
            try:
                plans = await ProductionDirector.plan(*provider_info,pool,count,topic,settings.get('language','en'),feedback)
            except ValueError as exc:
                feedback=[str(exc)]
                (temp/'script_retry.json').write_text(json.dumps({'feedback':feedback,'response':getattr(provider_info[0],'last_production_response',None)},ensure_ascii=False))
                continue
            results,issues = {},[]
            attempt_dir=temp/'renders'/f'attempt_{attempt}'
            attempt_dir.mkdir(parents=True,exist_ok=True)
            (attempt_dir/'plans.json').write_text(json.dumps(plans,ensure_ascii=False,default=str))
            for variant in ('A','B'):
                plan = plans[variant]
                timeline,segments,cursor = [],[],0.
                for idx,moment in enumerate(plan['moments']):
                    if not moment.get('topic_verified') or moment.get('verified_topic') != topic:
                        raise ValueError('An unverified cut reached the production renderer.')
                    progress('GENERATING_VOICE',60+round((idx+(0 if variant=='A' else count))/(count*2)*18),
                             f'Short {variant} · #{moment["assigned_rank"]}: natural narration and timed captions')
                    folder = temp/'renders'/f'attempt_{attempt}'/variant
                    folder.mkdir(parents=True,exist_ok=True)
                    voice = folder/f'voice_{idx}.wav'
                    try:
                        if settings.get('voice_synthesizer'): speech=await settings['voice_synthesizer'](moment['narration_text'],voice)
                        else: speech = await TTSEngine.synthesize_timed(moment['narration_text'],voice,settings.get('default_voice'))
                    except NarrationAlignmentError as exc:
                        issues.append(f'Short {variant} #{moment["assigned_rank"]}: rewrite this narration in simpler spoken language: {moment["narration_text"]!r}. {exc}')
                        break
                    duration = moment['end']-moment['start']
                    if speech['duration'] > duration-.2:
                        moment['start'] = max(moment['verified_start'],moment['end']-speech['duration']-.25)
                        duration = moment['end']-moment['start']
                    if speech['duration'] > duration-.15:
                        issues.append(f'Short {variant} #{moment["assigned_rank"]} narration is too long. Rewrite it with fewer words.'); break
                    ProductionQC.validate_words(speech['words'],duration,speech['text'])
                    captions = list(speech['segments'])
                    for source_segment in moment['source_transcript'].get('segments',[]):
                        words = [{**w,'start':w['start']-moment['start'],'end':w['end']-moment['start']}
                            for w in source_segment.get('words',[]) if moment['start']+speech['duration']+.3 <= w['start'] and w['end']<=moment['end']]
                        if words:
                            captions.append({'start':words[0]['start'],'end':words[-1]['end'],
                                             'text':' '.join(w['word'] for w in words),'words':words})
                    caption_file = await run_blocking(CaptionRenderer.write_ass,folder/f'captions_{idx}.ass',captions,0,duration)
                    if not caption_file: raise ValueError('Narration captions are missing.')
                    overlay = await run_blocking(CaptionRenderer.render_production_card,folder/f'graphics_{idx}.png',
                        topic,moment['assigned_rank'],moment['label'],count)
                    effect = await run_blocking(ProductionQC.rank_reveal,folder/f'reveal_{idx}.wav',moment['assigned_rank']) if idx==0 or moment['assigned_rank'] in (1,3) else None
                    rendered = await run_blocking(VideoReframer.reframe_to_vertical,Path(moment['file_path']),folder/f'segment_{idx}.mp4',
                        start=moment['start'],duration=duration,overlay_png=overlay,narration_wav=voice,captions_ass=caption_file,
                        title_band=True,layout='fit',reveal_sfx=effect)
                    segments.append(rendered)
                    timeline.append({'rank':moment['assigned_rank'],'source_id':moment['source_id'],'label':moment['label'],
                        'timeline_start':round(cursor,3),'duration':round(duration,3),'source_start':moment['start'],'source_end':moment['end'],
                        'payoff_relative':moment['payoff_time']-moment['start'],'narration':moment['narration_text'],'speech':speech})
                    cursor += duration
                if len(segments) != count: continue
                progress('RENDERING',82,f'Mastering and inspecting finished Short {variant}')
                raw = await run_blocking(FFmpegCore.concatenate_clips,segments,folder/'assembled.mp4')
                master = await run_blocking(ProductionQC.master_audio,raw,folder/f'ranking_short_{variant}.mp4')
                try:
                    qc = await run_blocking(ProductionQC.inspect,master,cursor,folder/'qc',timeline)
                except ValueError as exc:
                    issues.append(f'Short {variant}: {exc}'); continue
                progress('ANALYZING',89,f'Watching the actual rendered Short {variant} for final visual/audio review')
                review = await ProductionDirector.final_review(*provider_info,qc['proxy'],qc['sheets'],topic,timeline,feedback)
                (folder/'final_review.json').write_text(json.dumps({'timeline':timeline,'review':review},ensure_ascii=False,default=str))
                if not review['passed']:
                    issues.append(f'Short {variant}: '+json.dumps(review,ensure_ascii=False)); continue
                results[variant] = {'file':master,'plan':plan,'timeline':timeline,
                    'qc':{k:v for k,v in qc.items() if k not in ('proxy','sheets','info')},'final_review':review,'info':qc['info']}
            if len(results)==2:
                accepted = results; break
            feedback = issues
            (attempt_dir/'repair_feedback.json').write_text(json.dumps(issues,ensure_ascii=False))
        if not accepted:
            raise ValueError('Production QC rejected the A/B pair after three attempts. No unapproved video was published. '+str(feedback)[-650:])
        await cls.publish_verified_results(job_id,project_id,topic,provider_info,accepted,sources,rejections,published,progress,attempt+1)

    @classmethod
    async def publish_verified_results(cls,job_id,project_id,topic,provider_info,accepted,sources,rejections,published,progress,attempts):
        if set(accepted)!= {'A','B'} or any(not r['qc'].get('passed') or not r['final_review'].get('passed') for r in accepted.values()):
            raise ValueError('Both finished Shorts must pass objective and final multimodal QC before publication.')
        progress('RENDERING',95,'Publishing the two QC-approved finished MP4s')
        staged = []
        for variant,result in accepted.items():
            clip_id = f'{project_id}_{job_id}_ranking_short_{variant}'
            final = await run_blocking(StorageManager.move_final_clip,result['file'],'ranking',clip_id)
            published.append(final)
            preview = RANKING_OUTPUT_DIR/f'{clip_id}_preview.jpg'
            await run_blocking(FFmpegCore.extract_frame,final,preview,.7)
            published.append(preview)
            staged.append((clip_id,variant,result))
        metadata = []
        for clip_id,variant,result in staged:
            create_or_update_clip(clip_id=clip_id,project_id=project_id,job_id=job_id,title=f'{topic} · Short {variant}',
                subtitle=result['plan']['intent']+' · 1080p · QC passed',duration=round(result['info']['duration'],2),status='READY',
                preview_path=f'/output/ranking/{clip_id}_preview.jpg',video_path=f'/output/ranking/{clip_id}.mp4')
            metadata.append({'name':variant,'clip_id':clip_id,'intent':result['plan']['intent'],'hook':result['plan']['hook'],
                'moments':[{k:v for k,v in m.items() if k not in ('file_path','source_transcript')} for m in result['plan']['moments']],
                'timeline':result['timeline'],'qc':result['qc'],'final_review':result['final_review']})
        warnings = list(dict.fromkeys(w for s in sources for w in s.get('discovery_warnings',[])))
        update_project(project_id,title=f'Ranking {topic}',result_data={'variants':metadata,'moments':metadata[0]['moments'],
            'sources':[{k:v for k,v in s.items() if k!='file_path'} for s in sources],
            'warnings':warnings,'rejected_sources':rejections,
            'narration_engine':next((beat['speech'].get('engine','edge-neural-word-timed') for r in accepted.values() for beat in r['timeline'] if beat.get('speech')),'source audio'),
            'voice_provenance':[beat['speech']['provenance'] for r in accepted.values() for beat in r['timeline'] if (beat.get('speech') or {}).get('provenance')],
            'verified_topic':topic,'topic_verified':True,'production_qc_passed':True,'production_attempts':attempts,
            'analysis_basis':f'{provider_info[1]} verified events + final rendered video review',
            'scores_are_estimates':True,'music':'No added background music','output_count':2})

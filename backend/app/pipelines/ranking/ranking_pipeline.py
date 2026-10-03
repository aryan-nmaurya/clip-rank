import asyncio
import json
import logging
from pathlib import Path
from app.core.config import RANKING_OUTPUT_DIR
from app.core.database import update_job, update_project, create_or_update_clip, record_job_failure
from app.core.runtime import run_blocking, run_process
from app.storage.manager import StorageManager
from app.ai.router import AIRouter
from app.ai.ranking_verifier import RankingVerifier
from app.ai.production_director import ProductionDirector
from app.sources.discovery import SourceDiscovery
from app.sources.reuse import SourceReuse, source_keys
from app.sources import verdicts
from app.pipelines.ranking import checkpoint
from app.studio.strictness import production_level
from app.core import qc
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
MAX_DISCOVERY_ROUNDS = 3   # widen the search instead of failing on the first thin batch
MAX_TOTAL_CANDIDATES = 36  # hard ceiling on downloads/AI verification per job
VERIFY_CONCURRENCY = 3     # Gemini calls overlap; local FFmpeg/OCR work already runs off-loop


def variant_count(settings):
    """One finished Short, or an A/B pair built from fully disjoint sources."""
    return 1 if (settings or {}).get('variants') in (1,'1') else 2


def key_of(source):
    """Stable identity of a source across jobs (survives re-downloads and new file paths)."""
    keys = sorted(source_keys(source))
    return keys[0] if keys else str(source.get('id') or source.get('file_path'))


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
            if not settings.get('voice_synthesizer'):
                await run_blocking(TTSEngine.validate_ready,settings.get('default_voice'))
            need = count*variant_count(settings)
            progress('INGESTING',9,'Finding raw individual videos' + (' for two distinct Shorts' if need>count else ''))
            settings = {**settings, 'project_id': project_id}
            expected = checkpoint.signature(topic, count, variant_count(settings), settings)
            saved = await run_blocking(checkpoint.load, project_id, expected)
            sources, moments, evaluated, finished = [], [], set(), set()
            round_cursor, pool = 0, None
            if saved:
                rejections.extend(saved.get('rejections', []))
                sources, moments = saved['sources'], saved['moments']
                evaluated, finished = set(saved.get('evaluated_urls', [])), set(saved.get('finished_keys', []))
                round_cursor, pool = saved.get('next_round', 0), saved.get('pool')
                waiting = sum(1 for s in sources if key_of(s) not in finished)
                progress('INGESTING',10,f'Resuming from saved progress: {len(moments)} verified, {len(finished)} already judged, {waiting} waiting')

            def persist(stage='verifying'):
                checkpoint.save(project_id, {'signature': expected, 'stage': stage, 'next_round': round_cursor, 'sources': sources,
                    'moments': moments, 'evaluated_urls': sorted(evaluated), 'finished_keys': sorted(finished),
                    'rejections': rejections, 'pool': pool})

            done = 0
            gate = asyncio.Semaphore(VERIFY_CONCURRENCY)
            async def verify_one(idx, source, total):
                nonlocal done
                async with gate:
                    progress('ANALYZING',20+round(min(done,total)/max(1,total)*30),f'Verifying source {done+1}/{total}')
                    found = await cls.verify_source(idx,source,topic,count,settings,provider_info,temp,rejections)
                    if found: moments.append(found)
                    # Recorded only when a verdict was actually reached; a quota stop leaves the source waiting.
                    finished.add(key_of(source)); done += 1
                    persist()
            async def verify_all(items):
                tasks = [asyncio.ensure_future(verify_one(idx,source,len(sources))) for idx,source in items]
                try:
                    await asyncio.gather(*tasks)
                except BaseException:
                    # A quota stop, cancellation or crash ends the whole batch; don't leave siblings spending API calls.
                    for task in tasks: task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
            have_enough = lambda: len(Deduplicator.deduplicate_moments(moments)) >= need
            if pool is None:
                waiting = [(i,s) for i,s in enumerate(sources) if key_of(s) not in finished]
                if waiting:
                    done = len(sources) - len(waiting)
                    await verify_all(waiting)
                for round_index in range(round_cursor, MAX_DISCOVERY_ROUNDS):
                    if have_enough() or len(sources) >= MAX_TOTAL_CANDIDATES: break
                    try:
                        batch = await run_blocking(SourceDiscovery.discover_candidate_videos,topic,count,temp,
                            source_urls=settings.get('source_urls'),source_files=settings.get('source_files'),
                            source_titles=settings.get('source_titles'),source_platforms=settings.get('source_platforms'),rejections=rejections,
                            min_candidates=need,used_keys=SourceReuse.used_keys(project_id),source_provenance=settings.get('source_provenance'),
                            exclude_urls=evaluated,round_index=round_index,cc_only=bool(settings.get('cc_only')),
                            allow_partial=not (settings.get('source_urls') or settings.get('source_files')))
                    except ValueError:
                        if round_index==0 and not sources: raise
                        break
                    known = {key_of(s) for s in sources}
                    batch = [b for b in batch if key_of(b) not in known]
                    if not batch: break
                    for item in batch:   # durable copies first: the job workspace may be cleaned before a retry
                        try:
                            item['file_path'] = await run_blocking(checkpoint.adopt, project_id, item['file_path'])
                        except OSError:
                            pass   # unreadable file: verification will reject it with the real reason
                    evaluated.update(filter(None,(b.get('url') for b in batch)))
                    offset=len(sources); sources.extend(batch)
                    round_cursor = round_index + 1
                    persist()
                    done = offset
                    await verify_all([(offset+i,source) for i,source in enumerate(batch)])
                    if round_index+1 < MAX_DISCOVERY_ROUNDS and not have_enough() and not (settings.get('source_urls') or settings.get('source_files')):
                        progress('INGESTING',18,f'{len(moments)} of {need} verified so far; searching further for more raw clips')
                pool = Deduplicator.deduplicate_moments(moments)
                (temp/'verified_pool.json').write_text(json.dumps({'topic':topic,'count':count,'pool':pool},ensure_ascii=False,default=str))
                if len(pool) < need:
                    round_cursor = 0   # a retry searches again from the start, keeping every verified clip and every verdict
                    pool = None
                    persist()
                    raise ValueError(f'Only {len(moments)} unused individual clips passed screening; {"two Top "+str(count)+" Shorts need" if need>count else "a Top "+str(count)+" Short needs"} {need} different videos. Excluded {len(rejections)} sources. Add fresh raw clips or choose another topic. '+str(rejections[-1].get('reason','') if rejections else '')[-500:])
                persist('pool_ready')
            else:
                progress('INGESTING',50,'Resuming with the already-approved clips; skipping discovery and verification')
            (temp/'approved_pool.json').write_text(json.dumps({'topic':topic,'count':count,'pool':pool},ensure_ascii=False,default=str))
            await cls.produce_verified_pool(job_id,project_id,topic,count,settings,provider_info,temp,pool,sources,rejections,published,progress)
            await run_blocking(checkpoint.clear, project_id)   # finished: the saved progress is no longer needed
            progress('CLEANING',98,'Removing managed job intermediates after finished video QC')
            StorageManager.cleanup_job_temp(job_id)
            update_project(project_id,status='COMPLETED')
            completed = True
            progress('COMPLETED',100,'Two finished 1080p ranking Shorts ready' if need>count else 'Finished 1080p ranking Short ready')
        except Exception as exc:
            SourceReuse.release(job_id)
            for path in published: path.unlink(missing_ok=True)
            logger.exception('Ranking production failed for %s',job_id)
            record_job_failure(job_id,exc)
            update_project(project_id,status='FAILED',result_data={'rejected_sources':rejections})
            on_progress('FAILED',0,'Failed')
            raise
        finally:
            if completed:
                StorageManager.cleanup_job_temp(job_id)

    @classmethod
    async def verify_source(cls, idx, source, topic, count, settings, provider_info, temp, rejections):
        """Screen and topic-verify one downloaded candidate.

        Returns the verified moment, or None after recording exactly why it was excluded.
        """
        bar = production_level()
        def reject(reason, judged=False):
            rejections.append({'title':source['title'],'url':source.get('url'),'reason':reason})
            if judged:   # a real verdict, not a failure to obtain one
                verdicts.remember(source_keys(source), topic, reason)
        try:
            if source_keys(source).intersection(SourceReuse.used_keys(settings.get('project_id'))):
                return reject('Another production has already used this source.')
            info = await run_blocking(FFmpegCore.get_video_info,Path(source['file_path']))
            if qc.enabled() and min(info['width'],info['height']) < 360:
                return reject('Source resolution is below the production quality floor (360 pixels on its short side).')
            screen = await run_blocking(SourceScreening.inspect,source,temp/'screening'/source['id'])
            if screen['rejected'] and qc.enabled():
                return reject(screen['reason'], judged=True)
            verified = await AIRouter.screen_ranking_source(screen['sheet'],source,settings,provider_info=provider_info,required=True,lenient=bar.lenient_source_check)
            if not verified['suitable_raw'] or verified['already_ranked'] or verified['compilation']:
                return reject(verified.get('reason') or 'Existing ranking/compilation.', judged=True)
            source['screening'] = {'method':screen['method']+' + '+verified['method'],'sample_count':screen['sample_count'],'passed':True}
            selected_window=settings.get('source_windows',{}).get(source.get('url'))
            if selected_window: candidates=[selected_window]
            else:
                candidates = await run_blocking(MomentAnalyzer.analyze,Path(source['file_path']),count=8,
                    target_duration=max(12,settings.get('segment_duration',7)),title=source['title'],coverage=True,minimum_duration=1.)
            candidates = [m for m in candidates if m['clarity'] >= .45 or not qc.enabled()]
            if not candidates:
                return reject('No visually clear candidate windows.')
            sheet = await run_blocking(MomentAnalyzer.contact_sheet,Path(source['file_path']),candidates,temp/'frames'/f'source_{idx}.jpg',dense=True)
            revised,_ = await AIRouter.evaluate_moments(candidates,sheet,settings,topic=topic,provider_info=provider_info,level=bar)
            chosen,reason = None,f'No complete, confidently verified {topic} event.'
            for proposal in revised:
                clamped = MomentAnalyzer.clamp_selections([proposal],source['duration'],1)
                if not clamped: continue
                candidate = clamped[0]
                review_sheet = await run_blocking(MomentAnalyzer.contact_sheet,Path(source['file_path']),[candidate],
                    temp/'frames'/f'review_{idx}_{candidate["start"]}.jpg',dense=True,layout='fit')
                review_args={}
                if hasattr(provider_info[0],'analyze_video'):
                    proxy=temp/'frames'/f'review_video_{idx}.mp4'
                    await run_blocking(run_process,['ffmpeg','-v','error','-y','-ss',str(candidate['start']),
                        '-i',source['file_path'],'-t',str(candidate['end']-candidate['start']),'-vf','scale=640:-2,fps=15',
                        '-c:v','libx264','-preset','veryfast','-crf','28','-c:a','aac','-b:a','64k',str(proxy)])
                    review_args['video_path']=proxy
                review = await RankingVerifier.review(*provider_info,candidate,review_sheet,topic,level=bar,**review_args)
                if review['passed']:
                    chosen = {**candidate,'final_review':review}; break
                reason = review['reason']
            if not chosen:
                return reject(reason, judged=True)
            source_transcript = {'segments':[],'text':''}
            if info['has_audio']:
                audio = await run_blocking(FFmpegCore.extract_audio,Path(source['file_path']),temp/'audio'/f'{source["id"]}.wav')
                source_transcript = await run_blocking(Transcriber.transcribe,audio)
                if not source_transcript.get('available',True):
                    raise ValueError('Source speech could not be checked for captions. Resolve transcription before production export.')
            return {**source,**chosen,'source_transcript':source_transcript}
        except InterruptedError:
            raise
        except (ValueError,RuntimeError,OSError) as exc:
            reject(str(exc)[-800:])
            logger.warning('Skipped unverified ranking source %s: %s',source.get('id'),exc)

    @classmethod
    async def produce_verified_pool(cls,job_id,project_id,topic,count,settings,provider_info,temp,pool,sources,rejections,published,progress,editorial_feedback=None):
        """Render from server-verified evidence, shared by normal jobs and controlled repairs."""
        variants=variant_count(settings)
        keys=('A','B')[:variants]
        used=SourceReuse.used_keys(project_id)
        pool=[m for m in pool if not source_keys(m).intersection(used)]
        if len(pool)<count*variants or (qc.enabled() and any(not m.get('topic_verified') or m.get('verified_topic')!=topic or m.get('graphic_injury') is not False for m in pool)):
            raise ValueError('Production requires a complete pool of topic-verified source cuts.')
        progress('ANALYZING',53,'Comparing verified payoffs to establish genuine ranking escalation')
        sheets=[]
        for idx,moment in enumerate(pool):
            sheets.append(await run_blocking(MomentAnalyzer.contact_sheet,Path(moment['file_path']),[moment],
                temp/'frames'/f'comparative_{idx}.jpg',dense=True))
        pool=await ProductionDirector.rank_pool(*provider_info,pool,sheets,topic)
        # Reserve the chosen sources before expensive rendering. Concurrent jobs
        # cannot publish the same footage while this pair is being repaired.
        SourceReuse.claim(project_id,job_id,[{'keys':sorted(source_keys(m)),'start':m['start'],'end':m['end'],'whole_source':True}
            for variant in keys for m in ProductionDirector.cuts(pool,count,variant,variants)])
        (temp/'comparative_pool.json').write_text(json.dumps(pool,ensure_ascii=False,default=str))
        feedback,accepted,failed_lines = editorial_feedback,{},[]
        for attempt in range(MAX_PRODUCTION_ATTEMPTS):
            progress('SCRIPTING',55,f'Writing distinct A/B hooks and curiosity bridges · attempt {attempt+1}')
            try:
                plan_args={'fixed_plans':{key:result['plan'] for key,result in accepted.items()}} if accepted else {}
                plans = await ProductionDirector.plan(*provider_info,pool,count,topic,settings.get('language','en'),feedback,failed_lines=failed_lines,variant_count=variants,**plan_args)
            except ValueError as exc:
                feedback=[str(exc)]
                (temp/'script_retry.json').write_text(json.dumps({'feedback':feedback,'response':getattr(provider_info[0],'last_production_response',None)},ensure_ascii=False))
                continue
            issues = []
            attempt_dir=temp/'renders'/f'attempt_{attempt}'
            attempt_dir.mkdir(parents=True,exist_ok=True)
            (attempt_dir/'plans.json').write_text(json.dumps(plans,ensure_ascii=False,default=str))
            for variant in keys:
                if variant in accepted:
                    continue
                plan = plans[variant]
                timeline,segments,cursor = [],[],0.
                for idx,moment in enumerate(plan['moments']):
                    if qc.enabled() and (not moment.get('topic_verified') or moment.get('verified_topic') != topic):
                        raise ValueError('An unverified cut reached the production renderer.')
                    progress('GENERATING_VOICE',60+round((idx+(0 if variant=='A' else count))/(count*variants)*18),
                             f'Short {variant} · #{moment["assigned_rank"]}: natural narration and timed captions')
                    folder = temp/'renders'/f'attempt_{attempt}'/variant
                    folder.mkdir(parents=True,exist_ok=True)
                    voice = folder/f'voice_{idx}.wav'
                    try:
                        if settings.get('voice_synthesizer'): speech=await settings['voice_synthesizer'](moment['narration_text'],voice)
                        else: speech = await TTSEngine.synthesize_timed(moment['narration_text'],voice,settings.get('default_voice'))
                    except NarrationAlignmentError as exc:
                        failed_lines.append({'variant':variant,'source_id':moment['source_id'],'text':moment['narration_text'],'reason':str(exc)})
                        issues.append(f'Short {variant} #{moment["assigned_rank"]}: rewrite this narration in simpler spoken language: {moment["narration_text"]!r}. {exc}')
                        break
                    duration = moment['end']-moment['start']
                    if speech['duration'] > duration-.2:
                        moment['start'] = max(moment['verified_start'],moment['end']-speech['duration']-.25)
                        duration = moment['end']-moment['start']
                    if speech['duration'] > duration-.15:
                        failed_lines.append({'variant':variant,'source_id':moment['source_id'],'text':moment['narration_text'],'reason':'Too long for this complete cut'})
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
                        title_band=True,layout=('smart' if settings.get('layout','smart')=='fit' else settings.get('layout')),reveal_sfx=effect)
                    segments.append(rendered)
                    timeline.append({'rank':moment['assigned_rank'],'source_id':moment['source_id'],'label':moment['label'],
                        'timeline_start':round(cursor,3),'duration':round(duration,3),'source_start':moment['start'],'source_end':moment['end'],
                        'payoff_relative':moment['payoff_time']-moment['start'],'narration':moment['narration_text'],'speech':speech})
                    cursor += duration
                if len(segments) != count: continue
                progress('RENDERING',82,f'Mastering and inspecting finished Short {variant}')
                raw = await run_blocking(FFmpegCore.concatenate_clips,segments,folder/'assembled.mp4')
                master = await run_blocking(ProductionQC.master_audio,raw,folder/f'ranking_short_{variant}.mp4',settings)
                try:
                    ProductionQC.require_duration(cursor)
                    ProductionQC.require_duration(FFmpegCore.get_video_info(master)['duration'])
                    qc_report = await run_blocking(ProductionQC.inspect,master,cursor,folder/'qc',timeline)
                except ValueError as exc:
                    issues.append(f'Short {variant}: {exc}'); continue
                progress('ANALYZING',89,f'Watching the actual rendered Short {variant} for final visual/audio review')
                review = await ProductionDirector.final_review(*provider_info,qc_report['proxy'],qc_report['sheets'],topic,timeline,feedback,level=production_level())
                (folder/'final_review.json').write_text(json.dumps({'timeline':timeline,'review':review},ensure_ascii=False,default=str))
                if not review['passed']:
                    issues.append(f'Short {variant}: '+json.dumps(review,ensure_ascii=False)); continue
                accepted[variant] = {'file':master,'plan':plan,'timeline':timeline,
                    'qc':{k:v for k,v in qc_report.items() if k not in ('proxy','sheets','info')},'final_review':review,'info':qc_report['info']}
            if len(accepted)==variants:
                break
            feedback = issues
            (attempt_dir/'repair_feedback.json').write_text(json.dumps(issues,ensure_ascii=False))
        if len(accepted)!=variants:
            raise ValueError(('Production QC rejected the A/B pair' if variants==2 else 'Production QC rejected the Short')+' after three attempts. No unapproved video was published. '+str(feedback)[-650:])
        await cls.publish_verified_results(job_id,project_id,topic,provider_info,accepted,sources,rejections,published,progress,attempt+1)

    @classmethod
    async def publish_verified_results(cls,job_id,project_id,topic,provider_info,accepted,sources,rejections,published,progress,attempts):
        if not accepted or set(accepted) not in ({'A'},{'A','B'}) or any(not r['qc'].get('passed') or not r['final_review'].get('passed') for r in accepted.values()):
            raise ValueError('Every finished Short must pass objective and final multimodal QC before publication.')
        progress('RENDERING',95,'Publishing the QC-approved finished MP4'+('s' if len(accepted)>1 else ''))
        for result in accepted.values():ProductionQC.require_duration(result['info']['duration'])
        SourceReuse.claim(project_id,job_id,[{'keys':sorted(source_keys(m)),'start':m['start'],'end':m['end'],'whole_source':True}
            for result in accepted.values() for m in result['plan']['moments']])
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
            create_or_update_clip(clip_id=clip_id,project_id=project_id,job_id=job_id,title=f'{topic} · Short {variant}' if len(accepted)>1 else topic,
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
            'verified_topic':topic,'topic_verified':qc.enabled(),'production_qc_passed':True,'quality_control':'on' if qc.enabled() else 'off','production_attempts':attempts,
            'variant_count':len(accepted),
            'analysis_basis':f'{provider_info[1]} verified events + final rendered video review',
            'scores_are_estimates':True,'music':'No added background music','output_count':len(accepted)})

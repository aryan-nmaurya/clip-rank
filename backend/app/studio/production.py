import json
import hashlib
from app.ai.router import AIRouter
from app.ai.ranking_verifier import parse_object
from app.core.database import get_settings, update_job, update_project, create_or_update_clip
from app.core.runtime import run_blocking
from app.media.ffmpeg_core import FFmpegCore
from app.media.production_qc import ProductionQC
from app.storage.manager import StorageManager
from app.studio import store
from app.studio.graphics import GraphicsRenderer
from app.studio.models import now
from app.studio.policy import RightsPolicyEngine,OriginalityEngine,QualityGate,MetadataAgent
from app.studio.research import ResearchEngine
from app.studio.writer import RetentionWriter,BudgetProvider
from app.tts.local_kokoro import LocalKokoro
from app.tts.pocket import PocketTTS,PROFILES as POCKET_PROFILES
from app.tts.alignment import NarrationAlignmentError
from app.tts.voice_engine import TTSEngine

EDGE_VOICES={'Curious':'en-US-AriaNeural','Energetic':'en-US-GuyNeural','Tech Curious':'en-US-AriaNeural','Tech Energetic':'en-US-GuyNeural','Documentary':'en-GB-RyanNeural','Fast Explainer':'en-US-AriaNeural'}


async def final_review(provider,name,qc,script,timeline):
    prompt=('Review the ACTUAL finished technology explainer. These are original conceptual diagrams, not product footage. '
        'Inspect opening, each beat, captions, audio when available, visual diagram clarity, payoff and ending. '
        'Reject missing or clipped captions, bad crops, unfinished slides, static dead time, misleading visuals, cutoff narration or unreadable diagram labels. '
        'Return JSON {"passed":true,"confidence":0.95,"reason":"specific observations",'
        '"observations":[{"index":0,"time":1.0,"description":"specific visible diagram and caption"}],"issues":[],"repair":"..."}. '
        'One timestamped observation for EVERY beat within its actual interval. Choose a time at least 0.25 seconds inside the beat, not its cut boundary. '
        'Use the supplied sample_time as a suitable viewing position and describe what is actually visible there.\n'+json.dumps({'script':script,'timeline':[
            {**beat,'sample_time':round(beat['timeline_start']+beat['duration']/2,2)} for beat in timeline]}))
    if name=='gemini': raw=await provider.analyze_video(qc['proxy'],prompt)
    else: raw=await provider.analyze_images(qc['sheets'],prompt)
    result=parse_object(raw)
    observations=result.get('observations',[])
    valid=(result.get('passed') is True and type(result.get('confidence')) in (int,float) and .85<=result['confidence']<=1
           and not result.get('issues') and len(result.get('reason',''))>=40 and len(observations)==len(timeline))
    if valid:
        for i,(observation,beat) in enumerate(zip(observations,timeline)):
            t=observation.get('time')
            # Review models round timestamps to centiseconds. Allow only that
            # quantization error at a cut; this is not a different beat/event.
            valid=valid and observation.get('index')==i and type(t) in (int,float) and beat['timeline_start']-.03<=t<beat['timeline_start']+beat['duration']+.03 and len(observation.get('description',''))>=25
    result['passed']=bool(valid)
    if not valid and not result.get('issues'):
        result['validation_feedback']='Review timestamps, indices or evidence were invalid. Inspect the supplied midpoint of each beat and return all required observations.'
    return result


class StudioProduction:
    @classmethod
    async def run(cls,task):
        item=store.opportunity(task['opportunity_id'])
        if item and item.get('moment') and task['format'] in ('viral_clip','commentary','ranking'):
            from app.studio.visual_production import VisualProduction
            return await VisualProduction.run(task)
        from app.studio.models import ChannelProfile
        profile=ChannelProfile.model_validate(task['checkpoint'].get('production_profile') or store.profile().model_dump())
        item=store.opportunity(task['opportunity_id'])
        if not item or item['category'] not in profile.pillars: raise ValueError('This opportunity is outside the active channel profile.')
        folder=StorageManager.get_job_temp_dir(task['id']); checkpoint=task['checkpoint']
        checkpoint['production_profile']=profile.model_dump()
        settings={**get_settings(),'ai_provider':profile.ai_mode}
        selected,name=await AIRouter.require_ranking_provider(settings)
        def usage(count):
            checkpoint['cloud_calls_used']=count
            store.update(task['id'],checkpoint=checkpoint)
        provider=BudgetProvider(selected,name,limit=10,used=checkpoint.get('cloud_calls_used',0) if name!='local' else 0,on_usage=usage)
        def stage(value,progress):
            current=store.task(task['id'])
            if not current or current['status']=='CANCELLED' or current.get('lease_owner')!=task.get('lease_owner'):
                import asyncio
                raise asyncio.CancelledError()
            store.update(task['id'],stage=value,checkpoint=checkpoint)
            update_job(task['id'],status=value,progress=progress,current_stage=value.replace('_',' ').title())
            update_project(task['project_id'],status='PROCESSING')
        if profile.tts_engine=='kokoro' and not LocalKokoro.status()['ready']:
            raise ValueError(LocalKokoro.status()['message'])
        if profile.tts_engine=='pocket' and not PocketTTS.status()['ready']:
            raise ValueError(PocketTTS.status()['message'])
        stage('RESEARCHING',10)
        if 'research' not in checkpoint:
            checkpoint['research']=await run_blocking(ResearchEngine.collect_ranking if task['format']=='ranking' else ResearchEngine.collect,item)
        research=checkpoint['research']
        if task['format']=='ranking' and 'ranked_finalists' not in research:
            from app.studio.director import RankingEngine
            research['ranked_finalists']=await RankingEngine.research_finalists(provider,research)
        stage('SCRIPTING',25)
        feedback=checkpoint.get('repair_feedback','')
        for attempt in range(3):
            try:
                if 'script' not in checkpoint:
                    checkpoint['script']=await RetentionWriter.write(provider,item,research,feedback)
                script=RetentionWriter.validate(checkpoint['script'],research)
                if 'factual_review' not in checkpoint:
                    checkpoint['factual_review']=await RetentionWriter.factual_review(provider,script,research)
                break
            except ValueError as exc:
                if hasattr(exc,'rejected_script'):
                    (folder/'transcripts'/f'rejected_script_{attempt}.json').write_text(json.dumps(exc.rejected_script,indent=2))
                feedback=str(exc); checkpoint.pop('script',None); checkpoint.pop('factual_review',None)
                if profile.ai_mode=='auto' and name=='local':
                    cloud,_,_=AIRouter.get_providers(settings)
                    if await cloud.is_available():
                        provider=BudgetProvider(cloud,'gemini',limit=10,used=checkpoint.get('cloud_calls_used',0),on_usage=usage); name='gemini'
                if attempt==2: raise
        script=checkpoint['script']
        assets=[{'source_url':'cliprank://original/'+task['id'],'source_type':'cliprank_generated','creator':'ClipRank',
                 'license':'original','rights_status':'commercial_use_permitted','fetched_at':now()}]
        rights=RightsPolicyEngine.evaluate(assets); originality=OriginalityEngine.evaluate(script,assets)
        if not rights['passed'] or not originality['passed']: raise ValueError('Rights or originality gate failed.')
        checkpoint['rights']=rights; checkpoint['originality']=originality
        stage('VOICE',40)
        timeline=[]; clips=[]; cursor=0
        for i,beat in enumerate(script['beats']):
            voice=folder/'voice'/f'voice_{i}.wav'
            saved=checkpoint.setdefault('voices',{}).get(str(i))
            if saved and voice.is_file(): speech=saved
            elif profile.tts_engine=='pocket':
                try: speech=await PocketTTS.synthesize_timed(beat['narration'],voice,POCKET_PROFILES[profile.voice_profile])
                except NarrationAlignmentError as exc:
                    checkpoint['repair_feedback']=f'Beat {i} could not be spoken and captioned accurately: {exc}. Failed narration: {beat["narration"]!r}. Replace this sentence structure with simpler spoken language. Avoid unnecessary acronyms and version numbers in narration; retain precise names in on-screen graphics. Preserve source facts and attribution.'
                    for key in ('script','factual_review','voices','segments'): checkpoint.pop(key,None)
                    store.update(task['id'],checkpoint=checkpoint)
                    raise
            elif profile.tts_engine=='kokoro': speech=await LocalKokoro.synthesize_timed(beat['narration'],voice,profile.voice_profile)
            else: speech=await TTSEngine.synthesize_timed(beat['narration'],voice,EDGE_VOICES[profile.voice_profile])
            checkpoint['voices'][str(i)]=speech
            stage('EDITING',45+i*5)
            # Graphics and speech live in the same managed directory for deterministic rendering.
            import shutil
            render_voice=folder/'renders'/f'voice_{i}.wav'; shutil.copy2(voice,render_voice)
            signature=hashlib.sha256(json.dumps({'beat':beat,'speech':speech},sort_keys=True).encode()).hexdigest()
            segment=folder/'renders'/f'segment_{i}.mp4'
            previous=checkpoint.setdefault('segments',{}).get(str(i))
            if previous and previous['signature']==signature and segment.is_file():
                duration=previous['duration']
                await run_blocking(FFmpegCore.validate_output,segment,duration)
            else:
                segment,duration=await run_blocking(GraphicsRenderer.render,beat,speech,folder/'renders',i,len(script['beats']),research['publisher'])
                checkpoint['segments'][str(i)]={'signature':signature,'duration':duration}
                store.update(task['id'],checkpoint=checkpoint)
            timeline.append({'timeline_start':cursor,'duration':duration,'speech':speech,'rank':beat.get('rank'),
                             'headline':beat['headline'],'diagram':beat['diagram']})
            cursor+=duration; clips.append(segment)
        if not 24<=cursor<=55: raise ValueError('Narration pacing is outside the 25–50 second production range. Rewrite; do not speed up speech.')
        stage('RENDERING',75)
        joined=await run_blocking(FFmpegCore.concatenate_clips,clips,folder/'renders'/'joined.mp4')
        final=await run_blocking(ProductionQC.master_audio,joined,folder/'renders'/'final.mp4')
        stage('QC',85)
        (folder/'transcripts'/'accepted_script.json').write_text(json.dumps(script,indent=2))
        checkpoint['timeline']=timeline
        store.update(task['id'],checkpoint=checkpoint)
        qc=await run_blocking(ProductionQC.inspect,final,cursor,folder/'frames',timeline)
        review=await final_review(provider,name,qc,script,timeline)
        for _ in range(2):
            if review['passed']: break
            if review.get('passed') is False and not review.get('issues'):
                # A malformed review is re-inspected with explicit intervals;
                # a reported media problem goes through actual repair instead.
                review=await final_review(provider,name,qc,{**script,'review_feedback':review},timeline)
            else: break
        checks={'technical':{'passed':qc['passed']},'rights':rights,'originality':originality,
                'factuality':{'passed':checkpoint['factual_review']['passed']},'captions':{'passed':qc['captions_checked']},
                'audio':{'passed':qc['passed']},'visual':{'passed':review['passed']}}
        gate=QualityGate.evaluate(checks,checkpoint['factual_review']['scores'],profile.quality_threshold)
        if not gate['passed']:
            # Preserve evidence for bounded retry. Failed outputs are never exposed.
            checkpoint['final_review']=review; checkpoint['gate']=gate
            checkpoint['repair_feedback']=str(review.get('repair') or gate['failed'])
            if review.get('issues'):
                for key in ('script','factual_review','voices','segments'): checkpoint.pop(key,None)
            store.update(task['id'],checkpoint=checkpoint)
            raise ValueError('Final production gate failed: '+str(gate['failed'])+' '+str(review.get('repair','')))
        clip_id=task['id']+'_final'
        stage('PUBLISH_READY',95)
        target=await run_blocking(StorageManager.move_final_clip,final,'viral',clip_id)
        await run_blocking(FFmpegCore.validate_output,target,cursor)
        preview=target.with_name(clip_id+'_preview.jpg')
        await run_blocking(FFmpegCore.extract_frame,target,preview,.6)
        qc={k:v for k,v in qc.items() if k not in ('sheets','proxy','info')}
        metadata=MetadataAgent.generate(script,research,profile)
        record={'clip_id':clip_id,'qc':qc,'final_review':review,'timeline':timeline,'quality_gate':gate}
        result={'production_qc_passed':True,'moments':[record],'script':script,'research':research,'assets':assets,
                'quality_gate':gate,'checks':checks,'editorial_scores':checkpoint['factual_review']['scores'],
                'metadata':metadata,'pillar':item['category'],'format':task['format'],
                'voice_profile':profile.voice_profile,'tts_engine':profile.tts_engine,'hook_type':'curiosity',
                'voice_provenance':[beat['speech']['provenance'] for beat in timeline if beat['speech'].get('provenance')],
                'visual_structure':'original explanatory diagrams','cta_style':'none','source_type':'cliprank_generated',
                'cloud_calls':checkpoint.get('cloud_calls_used',0)}
        update_project(task['project_id'],status='COMPLETED',result_data=result)
        create_or_update_clip(clip_id,task['project_id'],task['id'],script['title'],subtitle=f'Original technology {task["format"]} · 1080p · QC passed',
            duration=qc['duration'],status='READY',video_path='/output/viral/'+target.name,preview_path='/output/viral/'+preview.name)
        update_job(task['id'],status='COMPLETED',progress=100,current_stage='Publish ready')
        checkpoint.update(result=result)
        store.update(task['id'],status='PUBLISH_READY',stage='PUBLISH_READY',clip_id=clip_id,checkpoint=checkpoint,error=None,lease_owner=None,lease_until=None)
        # The final MP4 supports upload retries. Durable SQLite metadata retains
        # the script, provenance and measured QC; render intermediates are done.
        await run_blocking(StorageManager.cleanup_job_temp,task['id'])
        return result

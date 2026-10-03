"""Autopilot uses the real-footage production engines, not technology slides."""
from app.core.database import get_settings,get_project,update_project,update_job
from app.core.config import STORAGE_DIR
from app.core.runtime import run_blocking
from app.media.ffmpeg_core import FFmpegCore
from app.storage.manager import StorageManager
from app.studio import store
from app.studio.models import ChannelProfile
from app.studio.director import ContentDirector
from app.studio.policy import RightsPolicyEngine,OriginalityEngine,QualityGate
from app.tts.pocket import PROFILES as POCKET_PROFILES
from app.tts.local_kokoro import LocalKokoro


class VisualProduction:
    @staticmethod
    async def run(task):
        from app.pipelines.ranking.ranking_pipeline import RankingPipeline
        from app.pipelines.viral.viral_pipeline import ViralPipeline
        from app.studio.production import EDGE_VOICES
        profile=ChannelProfile.model_validate(task['checkpoint'].get('production_profile') or store.profile().model_dump())
        item=store.opportunity(task['opportunity_id'])
        if not item or item['category'] not in profile.pillars or not ContentDirector.eligible(item,profile):
            raise ValueError('The selected moment must be verified and meet the configured rights policy.')
        if task['format']=='ranking':
            pool=[other for other in ContentDirector.rank(store.opportunities()) if other['category']==item['category']
                  and other.get('verified_topic')==item['verified_topic'] and ContentDirector.qualified(other,profile)]
            selected=[];seen=set()
            for other in [item,*pool]:
                if other['source_url'] not in seen: selected.append(other);seen.add(other['source_url'])
                if len(selected)==12:break
            if len(selected)<10: raise ValueError('Two Top 5 Shorts need ten different unused verified source videos on the same topic.')
        else: selected=[item]
        assets=[asset for other in selected for asset in ContentDirector.assets(other,profile)]
        rights=RightsPolicyEngine.evaluate(assets,profile.rights_policy)
        if not rights['passed']: raise ValueError(rights['reason'])
        checkpoint={**task['checkpoint'],'production_profile':profile.model_dump(),'selected_opportunities':[other['id'] for other in selected]}
        store.update(task['id'],checkpoint=checkpoint)
        settings={**get_settings(),'ai_provider':profile.ai_mode,'source_urls':[other['source_url'] for other in selected],
            'force_commentary':True,'contextual_commentary':task['format']=='commentary',
            'source_windows':{other['source_url']:other['moment'] for other in selected},
            'default_voice':'pocket:'+POCKET_PROFILES[profile.voice_profile] if profile.tts_engine=='pocket' else EDGE_VOICES[profile.voice_profile],
            'target_duration':15,'selected_moment':item['moment'],'verified_topic':item['verified_topic'],
            # Standalone Shorts use the whole discovered video, not just the ~10 s window found inside it.
            'whole_video':bool(profile.use_whole_video) and task['format']!='ranking'}
        if profile.tts_engine=='kokoro':
            async def local_speech(text,path): return await LocalKokoro.synthesize_timed(text,path,profile.voice_profile)
            settings['voice_synthesizer']=local_speech
        def progress(stage,value,description):
            current=store.task(task['id'])
            if not current or current['status']=='CANCELLED' or current.get('lease_owner')!=task.get('lease_owner'):
                import asyncio
                raise asyncio.CancelledError()
            store.update(task['id'],stage=stage)
        # Ranking independently verifies footage again and returns TWO distinct
        # A/B productions. Standalone modes stay inside the discovered moment.
        if task['format']=='ranking':
            await RankingPipeline.run(task['id'],task['project_id'],item['verified_topic'],5,settings,progress)
        else:
            await ViralPipeline.run(task['id'],task['project_id'],item['source_url'],1,settings,progress)
        project=get_project(task['project_id']);result=project['result_data']
        records=result.get('variants') or result.get('moments',[])
        originality=OriginalityEngine.evaluate_visual(records)
        scores={'hook':min(other['dimensions']['hook_strength'] for other in selected),
            'clarity':min(other['dimensions']['clarity'] for other in selected),
            'pacing':min(other['dimensions']['retention_potential'] for other in selected),
            'original_contribution':min(other['dimensions']['commentary_potential'] for other in selected),
            'audience_fit':min(other['dimensions']['audience_fit'] for other in selected)}
        checks={'technical':{'passed':result.get('production_qc_passed') is True},'rights':rights,'originality':originality,
            'factuality':{'passed':all(record.get('final_review',{}).get('narration_grounded') is True for record in records)},
            'captions':{'passed':all(record.get('qc',{}).get('captions_checked') is True for record in records)},
            'audio':{'passed':all(record.get('qc',{}).get('passed') is True for record in records)},
            'visual':{'passed':all(record.get('final_review',{}).get('passed') is True for record in records)}}
        gate=QualityGate.evaluate(checks,scores,profile.quality_threshold)
        result.update(quality_gate=gate,checks=checks,editorial_scores=scores,assets=assets,pillar=item['category'],
            format=task['format'],tts_engine=profile.tts_engine,visual_structure='real verified footage',rights_policy=profile.rights_policy)
        if not gate['passed']:
            # Publication is blocked and cards are hidden if the autonomous
            # editorial/originality gate rejects an otherwise valid export.
            result['production_qc_passed']=False
            from app.core.database import get_connection
            with get_connection() as c:c.execute("UPDATE clips SET status='REJECTED' WHERE project_id=?",(task['project_id'],))
            update_project(task['project_id'],status='FAILED',result_data=result)
            raise ValueError('Autonomous quality gate rejected this production: '+str(gate['failed']))
        clips=project['clips']
        for clip in clips:
            path=STORAGE_DIR/clip['video_path'].lstrip('/')
            await run_blocking(FFmpegCore.validate_output,path,clip['duration'])
        best=max(records,key=lambda r:r.get('final_review',{}).get('confidence',0))
        clip_id=best['clip_id']
        title=next(clip['title'] for clip in clips if clip['id']==clip_id)
        from app.publishing.youtube import description_preview
        result['metadata']={**description_preview(clip_id,item['reason']+'\n\n#Shorts'),'title':title[:100],
            'category_id':'17' if item['category'] in ('insane_sports','parkour_freerunning','epic_saves','trick_shots') else '24',
            'privacy':profile.privacy,'made_for_kids':profile.made_for_kids}
        update_project(task['project_id'],status='COMPLETED',result_data=result)
        checkpoint.update(result=result)
        store.update(task['id'],status='PUBLISH_READY',stage='PUBLISH_READY',clip_id=clip_id,checkpoint=checkpoint,
            error=None,lease_owner=None,lease_until=None)
        update_job(task['id'],status='COMPLETED',progress=100,current_stage='Awaiting copyright check')
        return result

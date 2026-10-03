from typing import Literal
from pydantic import BaseModel,ConfigDict
from fastapi import APIRouter,Depends,HTTPException
from app.api.youtube_routes import local_access,local_mutation
from app.core.runtime import run_blocking
from app.studio import store
from app.studio.models import ChannelProfile,DiscoveryRequest,ProduceRequest
from app.studio.research import OpportunityDiscovery,ResearchEngine
from app.studio.director import ContentDirector
from app.studio.worker import dashboard
from app.studio.analytics import AnalyticsCollector
from app.studio.visual_discovery import VisualDiscovery, DiscoveryRun

router=APIRouter(prefix='/studio',tags=['Studio'],dependencies=[Depends(local_access)])

@router.get('/dashboard')
def status(): return dashboard()

@router.get('/opportunities')
def opportunities(): return {'opportunities':ContentDirector.rank(store.opportunities())}

@router.post('/discover',dependencies=[Depends(local_mutation)])
async def discover(payload:DiscoveryRequest):
    """Starts discovery in the background and returns at once; poll GET /discover/status for progress and results."""
    return DiscoveryRun.start(payload.limit)


class StrictnessRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    level: Literal['relaxed','balanced','strict']
    scope: Literal['discovery','production','both']='both'


class WholeVideoRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: bool


@router.get('/strictness')
def get_strictness():
    """How demanding the AI's judgment is. Objective file checks (valid MP4, black/frozen, audio, injury) never relax."""
    from app.studio.strictness import LEVELS
    profile=store.profile()
    return {'discovery':profile.discovery_strictness,'production':profile.production_strictness,
            'levels':{k:{'min_confidence':v.min_confidence,'second_review':v.second_review,'final_critical_only':v.final_critical_only}
                      for k,v in LEVELS.items()},
            'use_whole_video':profile.use_whole_video,
            'always_enforced':['valid 1080x1920 H.264/AAC file','no black or frozen sections','clean audio level','narration present','no graphic injury']}


@router.post('/strictness',dependencies=[Depends(local_mutation)])
def set_strictness(payload:StrictnessRequest):
    changes={}
    if payload.scope in ('discovery','both'): changes['discovery_strictness']=payload.level
    if payload.scope in ('production','both'): changes['production_strictness']=payload.level
    store.save_profile(store.profile().model_copy(update=changes))
    return get_strictness()


@router.post('/whole-video',dependencies=[Depends(local_mutation)])
def set_whole_video(payload:WholeVideoRequest):
    store.save_profile(store.profile().model_copy(update={'use_whole_video':payload.enabled}))
    return get_strictness()


@router.get('/discover/status')
def discover_status(): return DiscoveryRun.status()

@router.post('/profile',dependencies=[Depends(local_mutation)])
def configure(payload:ChannelProfile):
    return store.save_profile(payload).model_dump()

@router.post('/produce',dependencies=[Depends(local_mutation)])
def produce(payload:ProduceRequest):
    item=store.opportunity(payload.opportunity_id)
    if not item: raise HTTPException(404,'Opportunity not found.')
    if item.get('moment') and not ContentDirector.eligible(item): raise HTTPException(400,'The moment does not meet the configured rights policy.')
    from app.studio.strictness import level as strictness_level
    if item.get('moment') and not ContentDirector.qualified(item,level=strictness_level()):
        raise HTTPException(400,'This moment is below the production quality threshold. Choose a stronger verified opportunity.')
    task=store.enqueue(payload.opportunity_id,payload.format)
    return {'project_id':task['project_id'],'job_id':task['id']}

@router.post('/plan',dependencies=[Depends(local_mutation)])
async def plan():
    result=await VisualDiscovery.discover(30)
    if not result['opportunities']: return {'candidates':[],'reason':'No verified visual opportunities; nothing was queued.','warnings':result['warnings']}
    return ContentDirector.plan(result['opportunities'])

@router.post('/tasks/{identifier}/retry',dependencies=[Depends(local_mutation)])
def retry(identifier:str):
    task=store.task(identifier)
    if not task: raise HTTPException(404,'Production not found.')
    if task['status']!='FAILED': raise HTTPException(409,'Only a failed production can be retried.')
    with store.get_connection() as c:
        c.execute("UPDATE studio_tasks SET attempts=0,status='CREATED',error=NULL,lease_until=NULL,lease_owner=NULL WHERE id=? AND status='FAILED'",(identifier,))
    return store.task(identifier)

@router.post('/analytics',dependencies=[Depends(local_mutation)])
async def analytics(): return await run_blocking(AnalyticsCollector.collect)

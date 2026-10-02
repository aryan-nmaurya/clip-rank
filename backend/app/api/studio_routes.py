from fastapi import APIRouter,Depends,HTTPException
from app.api.youtube_routes import local_access,local_mutation
from app.core.runtime import run_blocking
from app.studio import store
from app.studio.models import ChannelProfile,DiscoveryRequest,ProduceRequest
from app.studio.research import OpportunityDiscovery,ResearchEngine
from app.studio.director import ContentDirector
from app.studio.worker import dashboard
from app.studio.analytics import AnalyticsCollector
from app.studio.visual_discovery import VisualDiscovery

router=APIRouter(prefix='/studio',tags=['Studio'],dependencies=[Depends(local_access)])

@router.get('/dashboard')
def status(): return dashboard()

@router.get('/opportunities')
def opportunities(): return {'opportunities':ContentDirector.rank(store.opportunities())}

@router.post('/discover',dependencies=[Depends(local_mutation)])
async def discover(payload:DiscoveryRequest):
    return await VisualDiscovery.discover(payload.limit)

@router.post('/profile',dependencies=[Depends(local_mutation)])
def configure(payload:ChannelProfile):
    return store.save_profile(payload).model_dump()

@router.post('/produce',dependencies=[Depends(local_mutation)])
def produce(payload:ProduceRequest):
    item=store.opportunity(payload.opportunity_id)
    if not item: raise HTTPException(404,'Opportunity not found.')
    if item.get('moment') and not ContentDirector.eligible(item): raise HTTPException(400,'The moment does not meet the configured rights policy.')
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

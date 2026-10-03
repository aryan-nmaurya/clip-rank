"""Durable local worker. Cloud connectivity is outward-only; no public listener."""
import asyncio
import json
import logging
import shutil
import os
import platform
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.core.database import get_connection, get_project, update_job, update_project
from app.core.config import STORAGE_DIR
from app.core.runtime import run_blocking
from app.studio import store
from app.studio.analytics import AnalyticsCollector,dashboard_performance
from app.studio.director import ContentDirector,PerformanceAnalyzer
from app.studio.models import now
from app.studio.production import StudioProduction
from app.studio.research import OpportunityDiscovery
from app.studio.visual_discovery import VisualDiscovery
from app.storage.manager import StorageManager
from app.tts.local_kokoro import LocalKokoro
from app.tts.pocket import PocketTTS
from app.publishing import youtube
from app.publishing import copyright

logger=logging.getLogger('cliprank.worker')
active_productions={}


async def cancel_task(identifier):
    task=store.task(identifier)
    if not task: return
    if task['status'] in ('UPLOADING','ANALYTICS_PENDING','COMPLETE'):
        raise ValueError('An upload or published production cannot be cancelled as a generation.')
    store.update(identifier,status='CANCELLED',stage='CANCELLED')
    active=active_productions.get(identifier)
    if active:
        active.cancel(); await asyncio.gather(active,return_exceptions=True)
    update_job(identifier,status='CANCELLED',current_stage='Cancelled')
    update_project(task['project_id'],status='CANCELLED')


class StudioWorker:
    def __init__(self,identifier='local-studio'):
        self.id=identifier+'-'+uuid.uuid4().hex[:8]; self.current=None; self.running=None; self.last_analytics=None; self.last_plan_attempt=None

    async def run(self):
        store.init_studio()
        pulse=asyncio.create_task(self.pulse())
        try:
            while True:
                try: await self.tick()
                except Exception as exc: logger.warning('Worker cycle retained for retry: %s',type(exc).__name__)
                await asyncio.sleep(30)
        finally:
            pulse.cancel();await asyncio.gather(pulse,return_exceptions=True)
            if self.running:
                self.running.cancel()
                await asyncio.gather(self.running,return_exceptions=True)

    async def pulse(self):
        while True:
            try:
                store.heartbeat(self.id,{'current_job':self.current,'paused':not store.profile().enabled,
                    'queue':sum(t['status']=='CREATED' for t in store.tasks()),'cpu_load':round(os.getloadavg()[0],2) if hasattr(os,'getloadavg') else None,
                    'process_peak_ram_mb':memory_usage_mb(),
                    'disk_free_gb':round(shutil.disk_usage(STORAGE_DIR).free/1024**3,1)},self.current)
                if hasattr(self,'cloud'): await run_blocking(self.cloud.heartbeat)
            except Exception as exc: logger.debug('Heartbeat deferred: %s',type(exc).__name__)
            await asyncio.sleep(30)

    async def tick(self):
        from app.studio.cloud import CloudBridge
        if not hasattr(self,'cloud'): self.cloud=CloudBridge()
        try:
            cloud=await run_blocking(self.cloud.poll)
            if self.cloud.current:
                discovered=await VisualDiscovery.discover(30)
                if discovered['opportunities']: ContentDirector.plan(discovered['opportunities'])
                await run_blocking(self.cloud.heartbeat,'COMPLETE')
            elif cloud: await run_blocking(self.cloud.heartbeat)
        except (ValueError,KeyError,OSError):
            # No new autonomous production/publication while a paired controller is unreachable.
            cloud=None
            if CloudBridge.configuration():
                saved=store.profile();saved.enabled=False;store.save_profile(saved)
        profile=store.profile()
        # Manually enqueued discovery productions can run while Autopilot is paused.
        if self.running and self.running.done():
            await asyncio.gather(self.running,return_exceptions=True); self.running=None; self.current=None
        if profile.enabled and not cloud and (not self.last_plan_attempt or datetime.now(timezone.utc)-self.last_plan_attempt>timedelta(hours=1)):
            self.last_plan_attempt=datetime.now(timezone.utc)
            local_day=datetime.now(ZoneInfo(profile.timezone)).date().isoformat()
            with get_connection() as c:
                planned=c.execute('SELECT day FROM daily_plans WHERE day=?',(local_day,)).fetchone()
            discovered=await VisualDiscovery.discover(30)
            if not planned and discovered['opportunities']: ContentDirector.plan(discovered['opportunities'])
        if not self.running:
            task=store.claim(self.id,allow_daily=profile.enabled)
            if task:
                self.current=task['id']; self.running=asyncio.create_task(self.process(task))
                active_productions[task['id']]=self.running
        await run_blocking(self.publish_due)
        if profile.enabled and (self.last_analytics is None or datetime.now(timezone.utc)-self.last_analytics>timedelta(hours=12)):
            await run_blocking(AnalyticsCollector.collect); self.last_analytics=datetime.now(timezone.utc)

    async def process(self,task):
        try:
            await StudioProduction.run(task)
        except asyncio.CancelledError:
            # Checkpoints and managed media remain resumable after a worker restart.
            raise
        except Exception as exc:
            if store.task(task['id']) and store.task(task['id'])['status']=='CANCELLED': return
            current=store.task(task['id'])
            if not current: return
            repair=current['checkpoint'].get('repair_feedback')
            if repair and task['attempts']<3:
                store.update(task['id'],status='CREATED',stage='QC_REPAIR',error=str(exc)[:800],lease_owner=None,lease_until=None)
                return
            store.update(task['id'],status='FAILED',stage='FAILED',error=str(exc)[:800],lease_owner=None,lease_until=None)
            update_job(task['id'],status='FAILED',current_stage='Failed',error_message=str(exc)[:800])
            update_project(task['project_id'],status='FAILED')
        finally:
            active_productions.pop(task['id'],None)

    def publish_due(self):
        copyright.monitor_uploads()
        profile=store.profile()
        all_tasks=store.tasks()
        # Resolve in-flight uploads first, even when a newer day's plan exists.
        for task in all_tasks:
            if task['clip_id'] and task['status'] in ('UPLOADING','PUBLISH_READY','WAITING_TO_PUBLISH','COPYRIGHT_CHECK','COPYRIGHT_REVIEW'):
                upload=youtube.get_upload(task['clip_id'])
                if upload and upload['status']=='UPLOADED':
                    try: check=copyright.refresh(task['clip_id'])
                    except (youtube.YouTubeError,OSError): continue
                    if check['state']=='PUBLISHED':
                        store.update(task['id'],status='ANALYTICS_PENDING',stage='ANALYTICS_PENDING',video_id=upload['video_id'])
                        StorageManager.cleanup_job_temp(task['id'])
                    elif check['state']=='BLOCKED':store.update(task['id'],status='COPYRIGHT_BLOCKED',stage='COPYRIGHT_BLOCKED',error=check['message'])
                    else:
                        state='WAITING_TO_PUBLISH' if check['state']=='PASSED' else 'COPYRIGHT_REVIEW' if check['state']=='PENDING_REVIEW' else 'COPYRIGHT_CHECK'
                        store.update(task['id'],status=state,stage=state,error=None)
                elif upload and upload['status']=='FAILED':
                    store.update(task['id'],status='WAITING_TO_PUBLISH',error=upload.get('error'))
                elif not upload and task['status']=='UPLOADING':
                    last=task['checkpoint'].get('last_upload_attempt')
                    if not last or datetime.now(timezone.utc)-datetime.fromisoformat(last)>timedelta(minutes=15):
                        store.update(task['id'],status='WAITING_TO_PUBLISH',error='Interrupted upload reservation recovered.')
        if not profile.enabled or not profile.auto_publish or not youtube.connection_status()['connected']: return
        for day in sorted({t['day'] for t in all_tasks if t['day']}):
            today=[store.task(t['id']) for t in all_tasks if t['day']==day]
            if any(t['status'] in ('CREATED','PROCESSING') for t in today): continue
            approved=[t for t in today if t['status'] in ('PUBLISH_READY','WAITING_TO_PUBLISH','UPLOADING','COPYRIGHT_CHECK','COPYRIGHT_REVIEW','ANALYTICS_PENDING','COMPLETE')
                      and (store.opportunity(t['opportunity_id']) or {}).get('category') in profile.pillars]
            approved.sort(key=lambda t:(-t['checkpoint'].get('result',{}).get('quality_gate',{}).get('score',0),t['id']))
            for index,task in enumerate(approved):
                if index>=profile.publication_limit: continue
                result=task['checkpoint'].get('result',{})
                if task['day']!=datetime.now(ZoneInfo(profile.timezone)).date().isoformat(): continue
                if index==2 and result.get('quality_gate',{}).get('score',0)<profile.exceptional_threshold: continue
                if task['status'] not in ('PUBLISH_READY','WAITING_TO_PUBLISH') or not task['publish_at'] or task['publish_at']>now(): continue
                project=get_project(task['project_id']) or {}
                # Re-read authoritative gate immediately before publication, not LLM checkpoint state alone.
                if project.get('result_data',{}).get('quality_gate',{}).get('passed') is not True: continue
                upload=youtube.get_upload(task['clip_id'])
                if upload and upload['status']=='UPLOADED':
                    if copyright.read(task['clip_id']).get('state')!='PASSED':continue
                    published=[copyright.read(t['clip_id']).get('published_at') for t in all_tasks if t.get('clip_id')]
                    if any(t and datetime.now(timezone.utc).timestamp()-t<6*3600 for t in published):continue
                    try:
                        copyright.release(task['clip_id'],autonomous=True)
                        store.update(task['id'],status='ANALYTICS_PENDING',stage='ANALYTICS_PENDING',video_id=upload['video_id'])
                    except (youtube.YouTubeError,OSError):store.update(task['id'],error='Copyright clearance or YouTube publication is unavailable. Retry after resolving the check.')
                    continue
                if not store.reserve_upload(task['id'],profile,staging=True): continue
                try: youtube.queue_upload(task['clip_id'],{**result['metadata'],'privacy':profile.privacy,'made_for_kids':profile.made_for_kids})
                except (youtube.YouTubeError,ValueError): store.update(task['id'],status='WAITING_TO_PUBLISH',error='Upload is unavailable; retry after 15 minutes.')


def dashboard():
    from app.ai.router import AIRouter
    profile=store.profile()
    tasks=[task for task in store.tasks() if (store.opportunity(task['opportunity_id']) or {}).get('category') in profile.pillars]
    return {'profile':profile.model_dump(),'worker':store.worker_status(),'tasks':tasks,
            'youtube':youtube.connection_status(),'local_voice':LocalKokoro.status(),
            'pocket_voice':PocketTTS.status(),
            'performance':dashboard_performance(),'intelligence':PerformanceAnalyzer.intelligence(),
            'hosting_notice':'Vercel Hobby is for personal non-commercial use. Commercial hosting requires an eligible plan or another provider.'}


def memory_usage_mb():
    try:
        import resource
        value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(value/(1024**2 if platform.system()=='Darwin' else 1024),1)
    except ImportError: return None


if __name__=='__main__':
    from app.core.database import init_db
    init_db()
    asyncio.run(StudioWorker('standalone-worker').run())

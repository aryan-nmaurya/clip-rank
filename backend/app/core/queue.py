import asyncio
import logging
from app.core.config import MAX_CONCURRENT_JOBS
from app.core.database import get_job, get_project, get_settings, record_job_failure, update_job, update_project
from app.core.events import EventBroadcaster
from app.storage.manager import StorageManager
from app.pipelines.ranking.ranking_pipeline import RankingPipeline
from app.pipelines.viral.viral_pipeline import ViralPipeline
from app.pipelines.movie.movie_pipeline import MoviePipeline

logger = logging.getLogger("ai_shorts.queue")


class JobEngine:
    _instance = None

    def __init__(self):
        self.active_tasks = {}
        self.broadcaster = EventBroadcaster.get_instance()
        self.capacity = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def submit_job(self, job_id):
        task = asyncio.get_running_loop().create_task(self._process_job(job_id))
        self.active_tasks[job_id] = task
        task.add_done_callback(lambda _: self.active_tasks.pop(job_id, None))

    async def cancel_job(self, job_id):
        job = get_job(job_id)
        if not job or job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return False
        task = self.active_tasks.get(job_id)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        StorageManager.cleanup_job_temp(job_id)
        update_job(job_id, status="CANCELLED", current_stage="Cancelled")
        update_project(job["project_id"], status="CANCELLED")
        await self.broadcaster.broadcast(job_id, {"status": "CANCELLED", "stage": "Cancelled", "progress": job["progress"]})
        return True

    async def _process_job(self, job_id):
        job = get_job(job_id)
        project = get_project(job["project_id"]) if job else None
        if not project:
            return
        data = project["input_data"]
        settings = {**get_settings(), **data}

        def on_progress(status, progress, stage):
            asyncio.create_task(self.broadcaster.broadcast(job_id, {"status": status, "progress": progress, "stage": stage}))

        try:
            async with self.capacity:
                update_project(project["id"], status="PROCESSING", result_data=project.get('result_data',{}) if project['mode']=='movie' else {})
                if project['mode']=='movie':
                    await MoviePipeline.run(job_id,project['id'],data.get('video_source') or data.get('video_url'),
                                            data.get('count',3),settings,on_progress)
                elif project["mode"] == "ranking":
                    await RankingPipeline.run(job_id, project["id"], data.get("topic") or project["title"],
                                              data.get("count", 5), settings, on_progress)
                else:
                    await ViralPipeline.run(job_id, project["id"], data.get("video_source") or data.get("video_url"),
                                            data.get("count", 3), settings, on_progress)
        except asyncio.CancelledError:
            StorageManager.cleanup_job_temp(job_id)
            update_job(job_id, status="CANCELLED", current_stage="Cancelled")
            update_project(project["id"], status="CANCELLED")
            raise
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            record_job_failure(job_id, exc)
            update_project(project["id"], status="FAILED")
            on_progress("FAILED", 0, "Failed")
        finally:
            try:
                from app.storage import usage
                await asyncio.to_thread(usage.maintenance)
            except Exception:
                logger.exception("Storage maintenance after job %s failed", job_id)

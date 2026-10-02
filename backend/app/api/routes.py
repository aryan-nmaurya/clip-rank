import os
import uuid
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from app.core.config import (
    VIRAL_OUTPUT_DIR,
    RANKING_OUTPUT_DIR,
    TEMP_STORAGE_DIR,
    PROJECTS_STORAGE_DIR,
    HW_ACCEL,
)
from app.core.database import (
    get_settings,
    update_settings,
    create_project,
    get_project,
    list_projects,
    delete_project,
    create_job,
    get_job,
    get_clip,
)
from app.models.schemas import (
    ViralCreateRequest,
    RankingCreateRequest,
    ProjectResponse,
    JobResponse,
    AIStatusResponse,
    DiagnosticsResponse,
)
from app.ai.router import AIRouter
from app.core.queue import JobEngine
from app.core.events import EventBroadcaster
from app.pipelines.ranking.topic_parser import TopicParser
from app.storage.manager import StorageManager
from app.sources.ingestion import SourceIngestion
from app.core.runtime import run_blocking
from app.media.ffmpeg_core import FFmpegCore

router = APIRouter()
job_engine = JobEngine.get_instance()
broadcaster = EventBroadcaster.get_instance()

# -------------------------------------------------------------------
# Status & Diagnostics
# -------------------------------------------------------------------

@router.get("/status", response_model=AIStatusResponse)
async def get_ai_status():
    settings = get_settings()
    res = await AIRouter.get_status(settings)
    return AIStatusResponse(
        status_text=res["status_text"],
        provider=res["provider"],
        is_ready=res["is_ready"],
        local_available=res["local_available"],
        gemini_configured=res["gemini_configured"],
        openai_configured=res["openai_configured"],
        active_model=res["active_model"],
    )

def get_ffmpeg_version():
    import subprocess
    import shutil
    if not shutil.which("ffmpeg"):
        return "Unavailable"
    return subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5).stdout.splitlines()[0]


@router.get("/diagnostics", response_model=DiagnosticsResponse)
async def get_diagnostics():
    import shutil
    settings = get_settings()
    ai_status = await AIRouter.get_status(settings)
    all_projects = list_projects()
    completed = [p for p in all_projects if p.get("status") == "COMPLETED"]

    return DiagnosticsResponse(
        ai_provider=settings.get("ai_provider", "auto"),
        ffmpeg_available=bool(shutil.which("ffmpeg")),
        ffmpeg_version=await run_blocking(get_ffmpeg_version),
        hw_accel=HW_ACCEL,
        gemini_configured=ai_status["gemini_configured"],
        openai_configured=ai_status["openai_configured"],
        local_endpoint_reachable=ai_status["local_available"],
        total_projects=len(all_projects),
        completed_projects=len(completed),
        system_load="Optimal"
    )

# -------------------------------------------------------------------
# Settings
# -------------------------------------------------------------------

@router.get("/settings")
def api_get_settings():
    return get_settings()

@router.post("/settings")
def api_update_settings(updates: Dict[str, Any]):
    return update_settings(updates)

# -------------------------------------------------------------------
# Project Creation: Workflow A (Viral Clips) & Workflow B (Ranking)
# -------------------------------------------------------------------

async def save_upload(upload: UploadFile, project_id: str, index=0):
    folder = PROJECTS_STORAGE_DIR / project_id / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "source.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
        raise HTTPException(400, "Upload an MP4, MOV, MKV, WebM, AVI, or M4V video.")
    path = folder / f"source_{index}{suffix}"
    try:
        size = 0
        with path.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > 2 * 1024**3:
                    raise HTTPException(413, "Video uploads must be smaller than 2 GB.")
                destination.write(chunk)
        await run_blocking(SourceIngestion.verify, path)
        return str(path)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Invalid video upload: " + str(exc)[-400:]) from exc
    finally:
        await upload.close()


def validate_provider(provider):
    if provider not in {"auto", "local", "openai", "gemini"}:
        raise HTTPException(400, "Unknown AI provider.")


def validate_urls(urls):
    try:
        return [SourceIngestion.validate_url(url) for url in urls]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def enqueue_project(project_id, mode, title, data):
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    create_project(project_id, mode, title, input_data=data)
    create_job(job_id, project_id)
    job_engine.submit_job(job_id)
    return {"project_id": project_id, "job_id": job_id, "status": "QUEUED"}


@router.post("/projects/viral")
async def create_viral_project(video_url: Optional[str] = Form(None), count: int = Form(3),
                               ai_provider: str = Form("auto"), video_file: Optional[UploadFile] = File(None),
                               target_duration: float = Form(25), layout: str = Form("fit"),
                               captions: bool = Form(True)):
    if not 1 <= count <= 10 or not 6 <= target_duration <= 60:
        raise HTTPException(400, "Choose 1–10 clips with a target duration of 6–60 seconds.")
    validate_provider(ai_provider)
    if layout not in {"fill", "fit"}:
        raise HTTPException(400, "Unknown framing layout.")
    video_url = video_url.strip() if video_url else None
    has_upload = bool(video_file and video_file.filename)
    if has_upload == bool(video_url):
        raise HTTPException(400, "Provide either one video URL or one uploaded file.")
    project_id = f"proj_v_{uuid.uuid4().hex[:12]}"
    source = validate_urls([video_url])[0] if video_url else await save_upload(video_file, project_id)
    source_title = Path(video_file.filename).stem if has_upload else "Video highlights"
    return enqueue_project(project_id, "viral", f"Clips · {source_title}", {
        "video_source": source, "video_url": video_url, "source_title": source_title,
        "count": count, "ai_provider": ai_provider, "target_duration": target_duration,
        "layout": layout, "captions": captions})


@router.post("/projects/ranking")
async def create_ranking_project(req: RankingCreateRequest):
    topic, inferred = TopicParser.parse_topic(req.topic.strip(), req.count or 5)
    if not req.topic.strip():
        raise HTTPException(400, "Topic is required.")
    count = req.count or inferred
    urls = validate_urls(req.source_urls)
    if urls and len(set(urls)) < count:
        raise HTTPException(400, f"Top {count} needs at least {count} distinct source links.")
    project_id = f"proj_r_{uuid.uuid4().hex[:12]}"
    return enqueue_project(project_id, "ranking", f"Ranking {topic}", {
        **req.model_dump(), "topic": topic, "count": count, "source_urls": urls,
        "default_voice": req.voice})


@router.post("/projects/ranking/upload")
async def create_ranking_upload(topic: str = Form(...), count: int = Form(5),
                                ai_provider: str = Form("auto"), narration: bool = Form(False),
                                layout: str = Form("fill"), segment_duration: float = Form(7),
                                video_files: List[UploadFile] = File(...)):
    if not topic.strip() or len(topic) > 180 or not 3 <= count <= 10 or not 3 <= segment_duration <= 12:
        raise HTTPException(400, "Provide a topic, 3–10 ranks, and 3–12 seconds per clip.")
    validate_provider(ai_provider)
    if layout not in {"fill", "fit"} or not count <= len(video_files) <= 20:
        raise HTTPException(400, f"Upload between {count} and 20 distinct videos and choose a valid layout.")
    project_id = f"proj_r_{uuid.uuid4().hex[:12]}"
    sources = []
    try:
        for idx, upload in enumerate(video_files):
            sources.append(await save_upload(upload, project_id, idx))
        parsed, _ = TopicParser.parse_topic(topic, count)
        return enqueue_project(project_id, "ranking", f"Ranking {parsed}", {
            "topic": parsed, "count": count, "source_files": sources,
            "ai_provider": ai_provider, "narration": narration, "layout": layout,
            "segment_duration": segment_duration,
            "source_titles": [Path(v.filename or "Highlight").stem for v in video_files]})
    except Exception:
        import shutil
        shutil.rmtree(PROJECTS_STORAGE_DIR / project_id, ignore_errors=True)
        raise

# -------------------------------------------------------------------
# Project Details & Management
# -------------------------------------------------------------------

@router.get("/projects", response_model=List[ProjectResponse])
def api_list_projects(
    mode: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    return list_projects(mode=mode, search=search, status=status)

@router.get("/projects/{project_id}", response_model=ProjectResponse)
def api_get_project(project_id: str):
    p = get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p

@router.get("/projects/{project_id}/result")
def api_get_project_result(project_id: str):
    p = get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"clips": p.get("clips", []), "status": p.get("status")}

@router.post("/projects/{project_id}/regenerate")
async def api_regenerate_project(project_id: str):
    p = get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    if p.get("status") in {"PROCESSING", "CREATED"}:
        raise HTTPException(409, "This project already has an active job.")
    from app.core.database import update_project
    update_project(project_id, status="CREATED", result_data={})
    new_job_id = f"job_regen_{uuid.uuid4().hex[:8]}"
    create_job(job_id=new_job_id, project_id=project_id)
    job_engine.submit_job(new_job_id)
    return {"project_id": project_id, "job_id": new_job_id, "status": "QUEUED"}

@router.delete("/projects/{project_id}")
async def api_delete_project(project_id: str):
    p = get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if p.get("job") and p["job"]["id"] in job_engine.active_tasks:
        await job_engine.cancel_job(p["job"]["id"])
    import shutil
    shutil.rmtree(PROJECTS_STORAGE_DIR / project_id, ignore_errors=True)
    delete_project(project_id)
    return {"status": "DELETED"}

# -------------------------------------------------------------------
# Jobs & Real-Time SSE Updates
# -------------------------------------------------------------------

@router.get("/jobs/{job_id}", response_model=JobResponse)
def api_get_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.post("/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    cancelled = await job_engine.cancel_job(job_id)
    return {"status": "CANCELLED" if cancelled else job["status"]}

@router.get("/jobs/{job_id}/events")
async def api_job_events(job_id: str, request: Request):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    queue = asyncio.Queue()
    broadcaster.subscribe(job_id, queue)

    async def event_generator():
        # Yield current initial state immediately
        current_job = get_job(job_id)
        if current_job:
            yield {
                "event": "message",
                "data": json.dumps({
                    "status": current_job["status"],
                    "progress": current_job["progress"],
                    "stage": current_job["current_stage"]
                })
            }

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield {"event": "message", "data": json.dumps(data)}
                    if data.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                        break
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "heartbeat"}
        finally:
            broadcaster.unsubscribe(job_id, queue)

    return EventSourceResponse(event_generator())

# -------------------------------------------------------------------
# Media Delivery & Downloads
# -------------------------------------------------------------------

@router.get("/clips/{clip_id}/download")
def api_download_clip(clip_id: str):
    c = get_clip(clip_id)
    if not c or not c.get("video_path"):
        raise HTTPException(status_code=404, detail="Clip not found")

    raw_path = c["video_path"].lstrip("/")
    # e.g., output/ranking/clip.mp4 or output/viral/clip.mp4
    from app.core.config import STORAGE_DIR
    actual_file = STORAGE_DIR / raw_path
    if not actual_file.exists():
        raise HTTPException(status_code=404, detail="Video file on disk not found")

    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in c["title"])
    return FileResponse(
        path=str(actual_file),
        media_type="video/mp4",
        filename=f"{safe_name}.mp4"
    )

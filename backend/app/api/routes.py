import os
import uuid
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form, Depends
from app.api.youtube_routes import local_access,local_mutation
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
    clip_passed_production_qc,
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
from app.tts.voice_engine import TTSEngine

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
        ranking_ready=res["ranking_ready"],
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

@router.get("/settings",dependencies=[Depends(local_access)])
def api_get_settings():
    return public_settings(get_settings())

def public_settings(settings):
    result=dict(settings)
    for name in ('gemini_api_key','openai_api_key'):
        result[name+'_configured']=bool(result.get(name))
        result.pop(name,None)
    return result

@router.post("/settings",dependencies=[Depends(local_access),Depends(local_mutation)])
def api_update_settings(updates: Dict[str, Any]):
    updates={key:value for key,value in updates.items() if key not in ('gemini_api_key','openai_api_key') or value is not None}
    if "default_voice" in updates:
        try:
            updates["default_voice"] = TTSEngine.resolve_voice(updates["default_voice"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    try: return public_settings(update_settings(updates))
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc


@router.get("/vision/connections")
async def vision_connections():
    settings = get_settings()
    gemini, _, local = AIRouter.get_providers(settings)
    return {"gemini": {"configured": await gemini.is_available(), "model": gemini.model,
                        "message": "Key configured; use Test vision to check images and authentication." if await gemini.is_available() else "Add your Gemini API key when ready."},
            "local": await local.connection_status()}


@router.post("/vision/test")
async def test_vision_connection(payload: Dict[str, Any]):
    selected = payload.get("provider")
    if selected not in {"gemini", "local"}:
        raise HTTPException(400, "Select Gemini or local vision.")
    settings = {**get_settings(), **{key: value for key, value in payload.items() if value is not None
                                   if key in {"gemini_api_key", "gemini_model", "local_endpoint", "local_model"}}}
    gemini, _, local = AIRouter.get_providers(settings)
    provider = gemini if selected == "gemini" else local
    if not await provider.is_available():
        message = "Enter a Gemini API key to test vision." if selected == "gemini" else (await local.connection_status())["message"]
        return {"passed": False, "message": message}
    from PIL import Image, ImageDraw
    from app.ai.ranking_verifier import parse_object
    path = TEMP_STORAGE_DIR / "vision-tests" / f"{uuid.uuid4().hex}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = Image.new("RGB", (500, 300), "white")
    draw = ImageDraw.Draw(sample)
    draw.ellipse((35, 75, 185, 225), fill="red")
    draw.rectangle((235, 35, 310, 110), fill="blue")
    draw.rectangle((360, 190, 435, 265), fill="blue")
    sample.save(path)
    try:
        raw = await provider.analyze_images([path], 'Inspect the image. Return ONLY JSON: {"red_circles": <integer count>, "blue_squares": <integer count>}.')
        try:
            result = parse_object(raw)
            passed = type(result.get("red_circles")) is int and type(result.get("blue_squares")) is int and result["red_circles"] == 1 and result["blue_squares"] == 2
        except (ValueError, TypeError):
            passed = False
        return {"passed": passed, "message": "Vision test passed: model read the image correctly." if passed else "Vision test failed. Check the API key, endpoint, model name and image support."}
    finally:
        path.unlink(missing_ok=True)


async def validate_ranking_vision(provider):
    try:
        await AIRouter.require_ranking_provider({**get_settings(), "ai_provider": provider})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def local_speech_access(request: Request):
    local_access(request)
    origin=request.headers.get('origin')
    if origin and origin not in {f'http://{host}:{port}' for host in ('localhost','127.0.0.1') for port in (8000,5173)}:
        raise HTTPException(403,'Speech is only available from the local app.')
    if request.headers.get('sec-fetch-site')=='cross-site':
        raise HTTPException(403,'Speech is only available from the local app.')


@router.get('/speech/status',dependencies=[Depends(local_speech_access)])
def speech_status():
    from app.tts.pocket import PocketTTS
    return PocketTTS.status()


@router.get("/speech/preview/{voice}",dependencies=[Depends(local_speech_access)])
async def preview_neural_voice(voice: str):
    try:
        voice = TTSEngine.resolve_voice(voice)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Each request has its own file so simultaneous previews cannot overwrite one another.
    from starlette.background import BackgroundTask
    path = TEMP_STORAGE_DIR / "voice-previews" / f"{uuid.uuid4().hex}.wav"
    def cleanup_preview():
        # The server creates this path, never the caller or a generated script.
        for suffix in ('.wav','.json','.alignment.json','.raw.wav'):
            path.with_suffix(suffix).unlink(missing_ok=True)
    try:
        await TTSEngine.synthesize("Watch his left foot. The landing looks simple, until you see what happens next.", path, voice)
    except ValueError as exc:
        cleanup_preview()
        raise HTTPException(503, str(exc)) from exc
    return FileResponse(path, media_type="audio/wav", background=BackgroundTask(cleanup_preview))

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
    if not captions:
        raise HTTPException(400, 'Production speech captions are mandatory.')
    video_url = video_url.strip() if video_url else None
    has_upload = bool(video_file and video_file.filename)
    if has_upload == bool(video_url):
        raise HTTPException(400, "Provide either one video URL or one uploaded file.")
    await validate_ranking_vision(ai_provider)
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
    overrides = req.model_dump()
    if req.voice:
        try:
            overrides["default_voice"] = TTSEngine.resolve_voice(req.voice)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    await validate_ranking_vision(req.ai_provider)
    return enqueue_project(project_id, "ranking", f"Ranking {topic}", {
        **overrides, "topic": topic, "count": count, "source_urls": urls})


@router.post("/projects/ranking/upload")
async def create_ranking_upload(topic: str = Form(...), count: int = Form(5),
                                ai_provider: str = Form("auto"), narration: bool = Form(True),
                                layout: str = Form("fit"), segment_duration: float = Form(7),
                                voice: Optional[str] = Form(None),
                                video_files: List[UploadFile] = File(...)):
    if not topic.strip() or len(topic) > 180 or not 3 <= count <= 10 or not 3 <= segment_duration <= 12:
        raise HTTPException(400, "Provide a topic, 3–10 ranks, and 3–12 seconds per clip.")
    validate_provider(ai_provider)
    if not narration:
        raise HTTPException(400, 'Production ranking requires original narration and timed captions.')
    if layout not in {"fill", "fit"} or not count <= len(video_files) <= 20:
        raise HTTPException(400, f"Upload between {count} and 20 distinct videos and choose a valid layout.")
    project_id = f"proj_r_{uuid.uuid4().hex[:12]}"
    voice_settings = {}
    if voice:
        try:
            voice_settings["default_voice"] = TTSEngine.resolve_voice(voice)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    await validate_ranking_vision(ai_provider)
    sources = []
    try:
        for idx, upload in enumerate(video_files):
            sources.append(await save_upload(upload, project_id, idx))
        parsed, _ = TopicParser.parse_topic(topic, count)
        return enqueue_project(project_id, "ranking", f"Ranking {parsed}", {
            "topic": parsed, "count": count, "source_files": sources,
            "ai_provider": ai_provider, "narration": narration, "layout": layout,
            "segment_duration": segment_duration,
            **voice_settings,
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
    projects = list_projects(mode=mode, search=search, status=status)
    for project in projects:
        project['clips'] = [c for c in project['clips'] if clip_passed_production_qc(c)]
    return projects

@router.get("/projects/{project_id}", response_model=ProjectResponse)
def api_get_project(project_id: str):
    p = get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    p['clips'] = [c for c in p['clips'] if clip_passed_production_qc(c)]
    return p

@router.get("/projects/{project_id}/result")
def api_get_project_result(project_id: str):
    p = get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"clips": [c for c in p.get('clips',[]) if clip_passed_production_qc(c)], "status": p.get("status")}

@router.post("/projects/{project_id}/regenerate")
async def api_regenerate_project(project_id: str):
    from app.core.database import update_project, get_connection
    p = get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if p['mode']=='discovery':
        from app.studio import store
        previous=store.task(p['input_data'].get('studio_task_id',''))
        if not previous or previous['status'] not in ('FAILED','CANCELLED'):
            raise HTTPException(409,'Create another version from Viral Discovery; active or published productions cannot be replaced.')
        store.update(previous['id'],status='CREATED',error=None,lease_owner=None,lease_until=None)
        with get_connection() as conn:
            conn.execute('UPDATE studio_tasks SET attempts=0 WHERE id=?',(previous['id'],))
        update_project(project_id,status='CREATED')
        return {'project_id':project_id,'job_id':previous['id'],'status':'QUEUED'}

    if p.get("status") in {"PROCESSING", "CREATED"}:
        raise HTTPException(409, "This project already has an active job.")
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
    if p['mode']=='discovery':
        from app.studio.worker import cancel_task
        from app.studio import store
        for task in store.tasks():
            if task['project_id']==project_id:
                try: await cancel_task(task['id'])
                except ValueError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
        from app.core.database import get_connection
        with get_connection() as c:
            c.execute('DELETE FROM studio_tasks WHERE project_id=?',(project_id,))
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
    from app.studio import store
    if store.task(job_id):
        from app.studio.worker import cancel_task
        try: await cancel_task(job_id)
        except ValueError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
        return {'status':'CANCELLED'}
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
    if not clip_passed_production_qc(c):
        raise HTTPException(409, 'This export predates production QC or did not pass it. Regenerate before downloading or publishing.')

    raw_path = c["video_path"].lstrip("/")
    # e.g., output/ranking/clip.mp4 or output/viral/clip.mp4
    from app.core.config import STORAGE_DIR, OUTPUT_STORAGE_DIR
    actual_file = (STORAGE_DIR / raw_path).resolve()
    if not actual_file.is_relative_to(OUTPUT_STORAGE_DIR.resolve()) or not actual_file.is_file() or actual_file.suffix.lower()!='.mp4':
        raise HTTPException(status_code=404, detail="Video file on disk not found")

    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in c["title"])
    return FileResponse(
        path=str(actual_file),
        media_type="video/mp4",
        filename=f"{safe_name}.mp4"
    )

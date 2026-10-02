from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import OUTPUT_STORAGE_DIR, PROJECT_ROOT
from app.core.database import init_db, get_connection, get_clip, clip_passed_production_qc
from app.storage.manager import StorageManager
from app.api.routes import router
from app.api.youtube_routes import router as youtube_router
from app.api.studio_routes import router as studio_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and purge stale temporary directories
    init_db()
    from app.studio.store import init_studio
    from app.studio.worker import StudioWorker
    import asyncio
    init_studio()
    from app.publishing.youtube import recover_interrupted
    recover_interrupted()
    StorageManager.startup_cleanup()
    with get_connection() as conn:
        conn.execute("UPDATE jobs SET status='FAILED', current_stage='Interrupted', error_message='Generation was interrupted by a server restart. Retry this project.' WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED') AND id NOT IN (SELECT id FROM studio_tasks)")
        conn.execute("UPDATE projects SET status='FAILED' WHERE status IN ('PROCESSING','CREATED') AND id NOT IN (SELECT project_id FROM studio_tasks)")
    worker_task=asyncio.create_task(StudioWorker().run())
    yield
    worker_task.cancel()
    await asyncio.gather(worker_task,return_exceptions=True)
    from app.core.queue import JobEngine
    engine = JobEngine.get_instance()
    for job_id in list(engine.active_tasks):
        await engine.cancel_job(job_id)

app = FastAPI(title="ClipRank", version="2.0.0", lifespan=lifespan)

# Allow CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(router, prefix="/api")
app.include_router(youtube_router, prefix="/api")
app.include_router(studio_router, prefix="/api")

# Final media is served only after QC, including direct/guessed URLs.
@app.get('/output/{mode}/{filename}')
def production_media(mode: str, filename: str):
    from app.storage.manager import VIRAL_OUTPUT_DIR, RANKING_OUTPUT_DIR
    if mode not in ('viral','ranking') or '/' in filename or '\\' in filename:
        raise HTTPException(404,'Media not found')
    preview=filename.endswith('_preview.jpg')
    clip_id=filename[:-12] if preview else filename[:-4] if filename.endswith('.mp4') else ''
    clip=get_clip(clip_id)
    if not clip_passed_production_qc(clip): raise HTTPException(404,'Approved media not found')
    field='preview_path' if preview else 'video_path'
    if clip.get(field)!=f'/output/{mode}/{filename}': raise HTTPException(404,'Media not found')
    directory=VIRAL_OUTPUT_DIR if mode=='viral' else RANKING_OUTPUT_DIR
    path=(directory/filename).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file(): raise HTTPException(404,'Media not found')
    return FileResponse(path,media_type='image/jpeg' if preview else 'video/mp4')

# Mount frontend production build
frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend_spa(full_path: str):
        file_path = (frontend_dist / full_path).resolve()
        if file_path.is_relative_to(frontend_dist.resolve()) and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(frontend_dist / "index.html")

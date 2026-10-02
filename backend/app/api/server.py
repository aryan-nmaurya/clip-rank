from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import OUTPUT_STORAGE_DIR, PROJECT_ROOT
from app.core.database import init_db, get_connection
from app.storage.manager import StorageManager
from app.api.routes import router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and purge stale temporary directories
    init_db()
    StorageManager.startup_cleanup()
    with get_connection() as conn:
        conn.execute("UPDATE jobs SET status='FAILED', current_stage='Interrupted', error_message='Generation was interrupted by a server restart. Retry this project.' WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')")
        conn.execute("UPDATE projects SET status='FAILED' WHERE status IN ('PROCESSING','CREATED')")
    yield
    from app.core.queue import JobEngine
    engine = JobEngine.get_instance()
    for job_id in list(engine.active_tasks):
        await engine.cancel_job(job_id)

app = FastAPI(title="AI Shorts Creator", version="1.0.0", lifespan=lifespan)

# Allow CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(router, prefix="/api")

# Static mounting for rendered outputs
app.mount("/output", StaticFiles(directory=str(OUTPUT_STORAGE_DIR)), name="output")

# Mount frontend production build
frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend_spa(full_path: str):
        file_path = frontend_dist / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(frontend_dist / "index.html")

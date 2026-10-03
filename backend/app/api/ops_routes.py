"""Operational endpoints: pre-flight health, storage, and the real production test."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.youtube_routes import local_access, local_mutation
from app.core.health import run_health_check
from app.core.production_test import OUTPUT_NAME, ProductionTest, output_dir
from app.core.runtime import run_blocking
from app.storage import usage

router = APIRouter(tags=["Operations"], dependencies=[Depends(local_access)])


@router.get("/health")
async def health():
    """READY, or the specific problem and its fix. Never discovered mid-render."""
    return await run_blocking(run_health_check)


@router.get("/storage")
async def storage_usage():
    return await run_blocking(usage.report)


@router.post("/storage/cleanup", dependencies=[Depends(local_mutation)])
async def storage_cleanup():
    return await run_blocking(usage.maintenance)


@router.post("/diagnostics/production-test", dependencies=[Depends(local_mutation)])
async def start_production_test():
    return ProductionTest.start()


@router.get("/diagnostics/production-test")
async def production_test_status():
    return ProductionTest.status()


@router.get("/diagnostics/production-test/video")
async def production_test_video():
    path = output_dir() / OUTPUT_NAME
    if not path.is_file():
        raise HTTPException(404, "Run the production test first.")
    return FileResponse(path, media_type="video/mp4", filename=OUTPUT_NAME)

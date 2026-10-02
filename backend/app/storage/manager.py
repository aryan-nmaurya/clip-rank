import os
import time
import shutil
import logging
from pathlib import Path
from typing import Optional
from app.core.config import (
    TEMP_STORAGE_DIR,
    OUTPUT_STORAGE_DIR,
    VIRAL_OUTPUT_DIR,
    RANKING_OUTPUT_DIR,
    PROJECTS_STORAGE_DIR,
    TEMP_RETENTION_HOURS,
)

logger = logging.getLogger("ai_shorts.storage")

class StorageManager:
    @staticmethod
    def get_job_temp_dir(job_id: str) -> Path:
        """Returns the isolated workspace directory for a job."""
        job_dir = (TEMP_STORAGE_DIR / job_id).resolve()
        for sub in ["downloads", "audio", "frames", "scenes", "transcripts", "voice", "renders"]:
            (job_dir / sub).mkdir(parents=True, exist_ok=True)
        return job_dir

    @staticmethod
    def cleanup_job_temp(job_id: str) -> bool:
        """
        Safely purges the temporary workspace for a completed, failed, or cancelled job.
        Strict filesystem containment enforced.
        """
        job_dir = (TEMP_STORAGE_DIR / job_id).resolve()
        # Security check: must reside inside TEMP_STORAGE_DIR
        if not job_dir.is_relative_to(TEMP_STORAGE_DIR.resolve()) or job_dir == TEMP_STORAGE_DIR.resolve():
            logger.error(f"Security boundary check failed for cleanup: {job_dir}")
            return False

        if job_dir.exists() and job_dir.is_dir():
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.info(f"Cleaned temporary workspace for job {job_id}")
                return True
            except Exception as e:
                logger.warning(f"Error cleaning temp directory for {job_id}: {e}")
                return False
        return True

    @staticmethod
    def startup_cleanup(retention_hours: int = TEMP_RETENTION_HOURS):
        """Scans and removes abandoned temporary directories older than retention_hours."""
        if not TEMP_STORAGE_DIR.exists():
            return
        cutoff = time.time() - (retention_hours * 3600)
        for item in TEMP_STORAGE_DIR.iterdir():
            if item.is_dir():
                try:
                    if item.stat().st_mtime < cutoff:
                        shutil.rmtree(item, ignore_errors=True)
                        logger.info(f"Startup cleanup purged stale temp folder: {item.name}")
                except Exception as e:
                    logger.warning(f"Failed to clean stale folder {item.name}: {e}")

    @staticmethod
    def move_final_clip(source_path: Path, mode: str, clip_id: str, extension: str = ".mp4") -> Path:
        """Moves a rendered short to permanent storage."""
        target_dir = VIRAL_OUTPUT_DIR if mode == "viral" else RANKING_OUTPUT_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_path = target_dir / f"{clip_id}{extension}"
        shutil.copy2(source_path, dest_path)
        return dest_path

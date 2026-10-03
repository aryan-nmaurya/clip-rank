"""What ClipRank stores, how much it may keep, and how it cleans up after itself.

Everything here is confined to ClipRank-controlled directories. Paths come from
configuration or the database, never from model output.
"""
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Dict, List

from app.core import config

logger = logging.getLogger("ai_shorts.storage.usage")
GB = 1024 ** 3
# Defaults suit a laptop; override with environment variables on a machine with more room.
TEMP_BUDGET_BYTES = int(float(os.environ.get("CLIPRANK_TEMP_BUDGET_GB", "20")) * GB)
MIN_FREE_BYTES = int(float(os.environ.get("CLIPRANK_MIN_FREE_GB", "5")) * GB)
ORPHAN_GRACE_SECONDS = 3600


def folder_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in files:
            try:
                full = os.path.join(root, name)
                if not os.path.islink(full):
                    total += os.path.getsize(full)
            except OSError:
                continue
    return total


def report() -> Dict:
    parts = {
        "temp": config.TEMP_STORAGE_DIR, "projects": config.PROJECTS_STORAGE_DIR,
        "output": config.OUTPUT_STORAGE_DIR, "models": config.STORAGE_DIR / "models",
    }
    sizes = {name: folder_size(path) for name, path in parts.items()}
    sizes["other"] = max(0, folder_size(config.STORAGE_DIR) - sum(sizes.values()))
    free = shutil.disk_usage(config.STORAGE_DIR).free
    cache = sizes["temp"] + sizes["projects"]
    return {"bytes": sizes, "total_bytes": sum(sizes.values()), "cache_bytes": cache,
            "cache_budget_bytes": TEMP_BUDGET_BYTES, "cache_over_budget": cache > TEMP_BUDGET_BYTES,
            "disk_free_bytes": free, "min_free_bytes": MIN_FREE_BYTES, "disk_low": free < MIN_FREE_BYTES,
            "temp_job_dirs": sum(1 for p in config.TEMP_STORAGE_DIR.iterdir() if p.is_dir()) if config.TEMP_STORAGE_DIR.exists() else 0}


def _protected_ids() -> set:
    """Jobs that may still be reading their workspace: running jobs and unfinished studio tasks."""
    from app.core.database import get_connection
    protected = set()
    try:
        with get_connection() as conn:
            protected |= {r["id"] for r in conn.execute("SELECT id FROM jobs WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')")}
            try:
                protected |= {r["id"] for r in conn.execute(
                    "SELECT id FROM studio_tasks WHERE status NOT IN ('COMPLETE','ANALYTICS_PENDING','CANCELLED')")}
            except sqlite3.OperationalError:
                pass
    except sqlite3.OperationalError:
        pass
    return protected


def _job_dirs() -> List[Path]:
    if not config.TEMP_STORAGE_DIR.exists():
        return []
    return [p for p in config.TEMP_STORAGE_DIR.iterdir() if p.is_dir() and not p.is_symlink()]


def _remove(path: Path) -> int:
    """Delete one directory only if it resolves inside managed temp storage."""
    root = config.TEMP_STORAGE_DIR.resolve()
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        logger.error("Refusing to delete outside managed temp storage: %s", resolved)
        return 0
    size = folder_size(resolved)
    shutil.rmtree(resolved, ignore_errors=True)
    return size


def cleanup_orphans(now: float = None) -> Dict:
    """Remove workspaces that no live job owns and nothing has touched for an hour."""
    now = now or time.time()
    protected = _protected_ids()
    removed, freed = [], 0
    for path in _job_dirs():
        if path.name in protected or now - path.stat().st_mtime < ORPHAN_GRACE_SECONDS:
            continue
        # A failed job keeps its workspace for diagnosis until the retention window ends.
        if now - path.stat().st_mtime < config.TEMP_RETENTION_HOURS * 3600 and _is_failed(path.name):
            continue
        freed += _remove(path)
        removed.append(path.name)
    return {"removed": removed, "freed_bytes": freed}


def _is_failed(job_id: str) -> bool:
    from app.core.database import get_connection
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row["status"] == "FAILED")
    except sqlite3.OperationalError:
        return False


def enforce_budget() -> Dict:
    """Over budget or low on disk: drop the oldest unprotected workspaces first, newest are kept."""
    protected = _protected_ids()
    removed, freed = [], 0
    for path in sorted(_job_dirs(), key=lambda p: p.stat().st_mtime):
        state = report()
        if not state["cache_over_budget"] and not state["disk_low"]:
            break
        if path.name in protected:
            continue
        freed += _remove(path)
        removed.append(path.name)
    return {"removed": removed, "freed_bytes": freed}


def cleanup_stale_checkpoints(now: float = None, max_age_days: int = 7) -> List[str]:
    """Saved Ranking progress older than a week can never be resumed (it expires on load); free its files."""
    now = now or time.time()
    removed = []
    if not config.PROJECTS_STORAGE_DIR.exists():
        return removed
    root = config.PROJECTS_STORAGE_DIR.resolve()
    for project in config.PROJECTS_STORAGE_DIR.iterdir():
        resume = project / "resume"
        if project.is_symlink() or not resume.is_dir() or resume.is_symlink():
            continue
        state = resume / "state.json"
        newest = state.stat().st_mtime if state.is_file() else resume.stat().st_mtime
        if now - newest > max_age_days * 86400 and resume.resolve().is_relative_to(root):
            shutil.rmtree(resume, ignore_errors=True)
            removed.append(project.name)
    return removed


def maintenance() -> Dict:
    stale = cleanup_stale_checkpoints()
    orphans = cleanup_orphans()
    budget = enforce_budget()
    return {"orphans": orphans, "budget": budget, "stale_checkpoints": stale, "after": report()}


def assert_job_room(workspace: Path, quota_bytes: int = 6 * GB) -> None:
    """Per-job guard called between stages so one runaway job cannot fill the disk."""
    if folder_size(workspace) > quota_bytes:
        raise OSError(f"disk quota: this job's workspace exceeded {quota_bytes // GB} GB of temporary media")
    if shutil.disk_usage(workspace).free < 512 * 1024 ** 2:
        raise OSError("disk full: less than 512 MB free, free disk space (Settings → Storage) and retry")

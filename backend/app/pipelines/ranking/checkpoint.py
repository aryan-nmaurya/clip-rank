"""Durable progress for Ranking jobs, so a retry after a quota stop (or crash) resumes instead of restarting.

The job's temporary workspace is for scratch work and is cleaned on a timer that can be shorter than a
daily quota reset. Anything worth keeping lives here instead, under the project: the sources that were
downloaded (hard-linked, so no extra disk), which of them were already judged, the clips that verified,
and - once enough have verified - the whole approved pool. A checkpoint is only reused for the same
topic/count/variants/mode, and expires after a week.
"""
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.core import config

VERSION = 1
MAX_AGE_SECONDS = 7 * 86400


def directory(project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", project_id or ""):
        raise ValueError("Invalid project identifier.")
    root = config.PROJECTS_STORAGE_DIR.resolve()
    path = (root / project_id / "resume").resolve()
    if not path.is_relative_to(root):
        raise ValueError("Checkpoint path escapes managed storage.")
    return path


def signature(topic: str, count: int, variants: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    return {"topic": " ".join(re.findall(r"\w+", topic.lower())), "count": count, "variants": variants,
            "cc_only": bool(settings.get("cc_only")),
            "supplied": bool(settings.get("source_urls") or settings.get("source_files"))}


def adopt(project_id: str, path: str) -> str:
    """Give a downloaded file a durable home (hard link when possible) and return the new path."""
    source = Path(path)
    media = directory(project_id) / "media"
    media.mkdir(parents=True, exist_ok=True)
    if source.resolve().is_relative_to(media.resolve()):
        return str(source)
    target = media / (hashlib.sha1(str(source).encode()).hexdigest()[:12] + "_" + source.name)
    if not target.exists():
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    return str(target)


def save(project_id: str, state: Dict[str, Any]) -> None:
    folder = directory(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {**state, "version": VERSION, "saved_at": time.time()}
    temporary = folder / "state.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, default=str))
    temporary.replace(folder / "state.json")


def load(project_id: str, expected: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The saved state if it belongs to this exact request and its files are intact; otherwise None."""
    try:
        state = json.loads((directory(project_id) / "state.json").read_text())
    except (OSError, ValueError):
        return None
    if state.get("version") != VERSION or state.get("signature") != expected:
        return None
    if time.time() - state.get("saved_at", 0) > MAX_AGE_SECONDS:
        clear(project_id)
        return None
    # A file that vanished invalidates only the item that needs it.
    def intact(item): return Path(item.get("file_path", "")).is_file()
    state["sources"] = [s for s in state.get("sources", []) if intact(s)]
    state["moments"] = [m for m in state.get("moments", []) if intact(m)]
    if state.get("pool") is not None:
        state["pool"] = state["pool"] if all(intact(m) for m in state["pool"]) else None
        if state["pool"] is None:
            state["stage"] = "verifying"
    return state


def summary(project_id: str) -> Dict[str, Any]:
    try:
        state = json.loads((directory(project_id) / "state.json").read_text())
    except (OSError, ValueError):
        return {"exists": False}
    return {"exists": True, "stage": state.get("stage"), "verified": len(state.get("moments", [])),
            "judged": len(state.get("finished_keys", [])), "sources": len(state.get("sources", [])),
            "pool_ready": state.get("pool") is not None, "saved_at": state.get("saved_at"),
            "topic": state.get("signature", {}).get("topic")}


def clear(project_id: str) -> None:
    try:
        folder = directory(project_id)
    except ValueError:
        return
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)

"""Pre-flight health check: find out in a second, not after 20 minutes of processing.

Every check returns what is wrong in plain words and how to fix it. READY means a
real Short can be produced on this machine right now.
"""
import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from app.core import config

Result = Tuple[bool, str, str]  # ok, detail, fix
REQUIRED_MODULES = {
    "cv2": "opencv-python-headless", "numpy": "numpy", "PIL": "pillow", "faster_whisper": "faster-whisper",
    "yt_dlp": "yt-dlp", "requests": "requests", "fastapi": "fastapi", "soundfile": "soundfile",
}
REQUIRED_ENCODERS = ("libx264", "aac")
REQUIRED_FILTERS = ("loudnorm", "alimiter", "overlay", "boxblur", "amix")


def _run(cmd: List[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout


def check_ffmpeg() -> Result:
    if not shutil.which("ffmpeg"):
        return False, "FFmpeg missing.", "Install FFmpeg (macOS: brew install ffmpeg) and restart ClipRank."
    return True, _run(["ffmpeg", "-version"]).splitlines()[0], ""


def check_ffprobe() -> Result:
    if not shutil.which("ffprobe"):
        return False, "FFprobe missing.", "Install FFmpeg, which includes FFprobe."
    return True, _run(["ffprobe", "-version"]).splitlines()[0], ""


def check_codecs() -> Result:
    if not shutil.which("ffmpeg"):
        return False, "FFmpeg missing.", "Install FFmpeg."
    encoders, filters = _run(["ffmpeg", "-hide_banner", "-encoders"]), _run(["ffmpeg", "-hide_banner", "-filters"])
    missing = [e for e in REQUIRED_ENCODERS if f" {e} " not in encoders] + [f for f in REQUIRED_FILTERS if f" {f} " not in filters]
    if missing:
        return False, "This FFmpeg build lacks: " + ", ".join(missing), "Install a full FFmpeg build with libx264 and the standard audio filters."
    return True, "libx264, AAC and the audio/video filters ClipRank uses are present.", ""


def check_python_deps() -> Result:
    missing = [package for module, package in REQUIRED_MODULES.items() if importlib.util.find_spec(module) is None]
    if missing:
        return False, "Missing Python packages: " + ", ".join(missing), "Run: .venv/bin/python -m pip install -r backend/requirements.txt"
    return True, "All required Python packages are installed.", ""


def check_models() -> Result:
    from app.tts.alignment import snapshot
    if snapshot() is None:
        return False, "Whisper base model is not cached (needed for captions).", "Run ClipRank once with internet access, or copy the model into storage/models."
    return True, "Whisper speech model cached.", ""


def check_voice() -> Result:
    from app.core.database import get_settings
    from app.tts.voice_engine import TTSEngine
    try:
        voice = TTSEngine.validate_ready(get_settings().get("default_voice"))
        return True, f"Narration voice ready ({voice}).", ""
    except ValueError as exc:
        return False, str(exc), "Run: PYTHONPATH=backend .venv/bin/python backend/setup_pocket_tts.py, or choose an online voice in Settings."


def check_disk() -> Result:
    from app.storage.usage import MIN_FREE_BYTES
    free = shutil.disk_usage(config.STORAGE_DIR).free
    if free < MIN_FREE_BYTES:
        return False, f"Only {free / 1024 ** 3:.1f} GB free.", "Free disk space or lower CLIPRANK_MIN_FREE_GB; production needs headroom for source video."
    return True, f"{free / 1024 ** 3:.0f} GB free.", ""


def check_database() -> Result:
    from app.core.database import get_connection
    try:
        conn = get_connection()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
    except Exception as exc:
        return False, f"Database unreachable: {str(exc)[-160:]}", "Check backend/data permissions."
    missing = {"projects", "jobs", "clips", "settings"} - tables
    return (not missing), ("Database connected." if not missing else f"Database is missing tables: {', '.join(sorted(missing))}"), \
        ("" if not missing else "Restart ClipRank so migrations can run.")


def check_directories() -> Result:
    for folder in (config.TEMP_STORAGE_DIR, config.VIRAL_OUTPUT_DIR, config.RANKING_OUTPUT_DIR, config.MOVIE_OUTPUT_DIR, config.DATA_DIR):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=folder):
                pass
        except OSError as exc:
            return False, f"Cannot write to {folder.name}: {exc.strerror}", f"Fix permissions on {folder}."
    return True, "Output and temporary directories are writable.", ""


def check_ai_credentials() -> Result:
    from app.core.database import get_settings
    settings = get_settings()
    configured = [name for name, key in (("Google AI Studio", "gemini_api_key"), ("Groq", "groq_api_key"),
                                         ("NVIDIA NIM", "nvidia_api_key"), ("OpenAI", "openai_api_key")) if settings.get(key)]
    if configured:
        keys = sum(1 for k in ("gemini_api_key", "gemini_api_key_2") if settings.get(k))
        suffix = f" ({keys} keys, automatic failover)" if keys > 1 else ""
        return True, "AI provider configured: " + ", ".join(configured) + suffix + ". Use Test vision in Settings to verify it answers.", ""
    return False, "No AI provider configured.", "Add a Gemini API key or connect a local Ollama vision model in Settings."


def check_youtube() -> Result:
    try:
        from app.publishing import youtube
        status = youtube.connection_status()
        if status.get("connected"):
            return True, f"Connected to {status.get('channel_title') or 'your channel'}.", ""
    except Exception as exc:
        return False, f"YouTube status unavailable: {str(exc)[-120:]}", "Reconnect YouTube in Settings."
    return False, "YouTube is not connected (optional; videos can still be produced).", "Connect YouTube in Settings to publish."


CHECKS: List[Tuple[str, str, bool, Callable[[], Result]]] = [
    ("ffmpeg", "FFmpeg", True, check_ffmpeg),
    ("ffprobe", "FFprobe", True, check_ffprobe),
    ("codecs", "H.264 / AAC encoders and filters", True, check_codecs),
    ("python", "Python dependencies", True, check_python_deps),
    ("models", "Speech model", True, check_models),
    ("voice", "Narration voice", True, check_voice),
    ("disk", "Free disk space", True, check_disk),
    ("database", "Database", True, check_database),
    ("directories", "Output directories", True, check_directories),
    ("ai", "AI credentials", True, check_ai_credentials),
    ("youtube", "YouTube connection", False, check_youtube),
]


def run_health_check() -> Dict:
    results = []
    for identifier, label, required, check in CHECKS:
        try:
            ok, detail, fix = check()
        except Exception as exc:  # a broken check is itself a finding, never a crash
            ok, detail, fix = False, f"Check failed to run: {type(exc).__name__}: {str(exc)[-160:]}", "See the server log."
        results.append({"id": identifier, "label": label, "required": required, "ok": ok, "detail": detail, "fix": fix})
    problems = [r for r in results if r["required"] and not r["ok"]]
    return {"ready": not problems, "status": "READY" if not problems else problems[0]["detail"],
            "problems": [{"id": p["id"], "detail": p["detail"], "fix": p["fix"]} for p in problems], "checks": results}

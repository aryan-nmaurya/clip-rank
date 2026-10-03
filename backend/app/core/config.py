import os
import shutil
from pathlib import Path

# Base Paths
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
STORAGE_DIR = PROJECT_ROOT / "storage"

TEMP_STORAGE_DIR = STORAGE_DIR / "temp"
PROJECTS_STORAGE_DIR = STORAGE_DIR / "projects"
OUTPUT_STORAGE_DIR = STORAGE_DIR / "output"
VIRAL_OUTPUT_DIR = OUTPUT_STORAGE_DIR / "viral"
RANKING_OUTPUT_DIR = OUTPUT_STORAGE_DIR / "ranking"
MOVIE_OUTPUT_DIR = OUTPUT_STORAGE_DIR / "movie"

DATA_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

# Ensure all critical storage directories exist
for directory in [
    STORAGE_DIR,
    TEMP_STORAGE_DIR,
    PROJECTS_STORAGE_DIR,
    OUTPUT_STORAGE_DIR,
    VIRAL_OUTPUT_DIR,
    RANKING_OUTPUT_DIR,
    MOVIE_OUTPUT_DIR,
    DATA_DIR,
]:
    directory.mkdir(parents=True, exist_ok=True)

# Hardware Acceleration Detection
def detect_hardware_acceleration() -> str:
    """Detects available FFmpeg hardware acceleration."""
    if shutil.which("ffmpeg"):
        try:
            import subprocess
            res = subprocess.run(["ffmpeg", "-hwaccels"], capture_output=True, text=True)
            if "videotoolbox" in res.stdout:
                return "videotoolbox"
            if "cuda" in res.stdout:
                return "cuda"
        except Exception:
            pass
    return "cpu"

HW_ACCEL = detect_hardware_acceleration()

# Video Rendering Specifications
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
AUDIO_SAMPLE_RATE = 48000

# Defaults
DEFAULT_AI_PROVIDER = "auto"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_LOCAL_ENDPOINT = "http://localhost:11434"
DEFAULT_LOCAL_MODEL = "qwen3-vl:4b"
DEFAULT_VOICE = "pocket:alba"
DEFAULT_LANGUAGE = "en"
TEMP_RETENTION_HOURS = 12
MAX_CONCURRENT_JOBS = 2
MAX_SOURCE_VIDEOS = 20
MAX_SEARCH_RESULTS = 40

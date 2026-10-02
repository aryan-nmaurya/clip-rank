import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    from app.core import database
    from app.storage import manager
    from app.pipelines.ranking import ranking_pipeline
    from app.pipelines.viral import viral_pipeline
    from app.api import routes
    outputs = tmp_path / "output"
    viral = outputs / "viral"
    ranking = outputs / "ranking"
    temp = tmp_path / "temp"
    projects = tmp_path / "projects"
    for folder in (viral, ranking, temp, projects):
        folder.mkdir(parents=True)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(manager, "TEMP_STORAGE_DIR", temp)
    monkeypatch.setattr(manager, "VIRAL_OUTPUT_DIR", viral)
    monkeypatch.setattr(manager, "RANKING_OUTPUT_DIR", ranking)
    monkeypatch.setattr(ranking_pipeline, "RANKING_OUTPUT_DIR", ranking)
    monkeypatch.setattr(viral_pipeline, "VIRAL_OUTPUT_DIR", viral)
    monkeypatch.setattr(routes, "PROJECTS_STORAGE_DIR", projects)
    database.init_db()
    return {"root": tmp_path, "viral": viral, "ranking": ranking, "temp": temp, "projects": projects}


@pytest.fixture
def footage(tmp_path):
    """Explicit test-only moving video with audible source tone."""
    from app.core.runtime import run_process
    paths = []
    for i in range(3):
        path = tmp_path / f"source_{i}.mp4"
        run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                     f"testsrc2=size={320 + i * 16}x180:rate=15:duration=6", "-f", "lavfi", "-i",
                     f"sine=frequency={300 + i * 100}:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
        paths.append(path)
    return paths

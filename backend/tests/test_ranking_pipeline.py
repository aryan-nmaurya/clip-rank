import asyncio
import numpy as np
from PIL import Image
from app.core.database import create_project, create_job, get_project
from app.pipelines.ranking.ranking_pipeline import RankingPipeline
from app.pipelines.ranking.scorer import RankingScorer
from app.media.ffmpeg_core import FFmpegCore


def test_best_moment_is_number_one():
    result = RankingScorer.score_and_order([{"score": 95, "id": "best"}, {"score": 40, "id": "last"}, {"score": 70, "id": "middle"}], 3)
    assert [m["assigned_rank"] for m in result] == [3, 2, 1]
    assert result[-1]["id"] == "best"


def test_ranking(isolated_app, footage, monkeypatch):
    from app.ai.router import AIRouter
    async def no_ai(*args, **kwargs): return None, "fallback"
    monkeypatch.setattr(AIRouter, "get_active_provider", no_ai)
    create_project("ranking_test", "ranking", "Test countdown", {"count": 3})
    create_job("ranking_job", "ranking_test")
    asyncio.run(RankingPipeline.run("ranking_job", "ranking_test", "Test moments", 3,
                {"ai_provider": "auto", "source_files": [str(p) for p in footage], "segment_duration": 3}, lambda *a: None))
    project = get_project("ranking_test")
    assert project["status"] == "COMPLETED"
    assert len(project["clips"]) == 1
    assert [m["assigned_rank"] for m in project["result_data"]["moments"]] == [3, 2, 1]
    assert project["result_data"]["warnings"]
    clip = project["clips"][0]
    path = isolated_app["ranking"] / (clip["id"] + ".mp4")
    info = FFmpegCore.validate_output(path, 9)
    assert info["has_audio"]
    preview = isolated_app["ranking"] / (clip["id"] + "_preview.jpg")
    with Image.open(preview) as frame:
        pixels = np.array(frame)
        assert pixels[400:900, 200:600].std() > 30  # visible footage, not black canvas
    assert not (isolated_app["temp"] / "ranking_job").exists()

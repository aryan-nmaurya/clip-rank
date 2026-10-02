import asyncio
import sys
from app.core.database import create_project, create_job, get_project, get_job
from app.pipelines.viral.viral_pipeline import ViralPipeline
from app.media.moments import MomentAnalyzer
from app.transcription.transcriber import Transcriber


def test_invalid_ai_timestamps_are_rejected():
    moments = [{"start": 20, "end": 50}, {"start": -2, "end": 3}, {"start": 2, "end": 3}, {"start": float('nan'), "end": 3}]
    result = MomentAnalyzer.clamp_selections(moments, 6, 3)
    assert len(result) == 1
    assert result[0]["start"] == 0 and result[0]["end"] == 3


def test_transcription_failure_never_invents_speech(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    result = Transcriber.transcribe(tmp_path / "no-audio.wav")
    assert result["segments"] == [] and result["text"] == ""
    assert result["available"] is False


def test_viral(isolated_app, footage, ranking_vision):
    create_project("viral_test", "viral", "Real footage", {"count": 3})
    create_job("viral_job", "viral_test")
    asyncio.run(ViralPipeline.run("viral_job", "viral_test", str(footage[0]), 3,
                                 {"ai_provider": "auto", "target_duration": 25}, lambda *a: None))
    project = get_project("viral_test")
    assert project["status"] == "COMPLETED"
    assert len(project["clips"]) == 1  # a 6-second source cannot produce three complete distinct clips
    assert 5.9 <= project["clips"][0]["duration"] <= 6.2
    assert project["result_data"]["generated_count"] == 1
    assert project["result_data"]["warnings"]
    assert project['result_data']['production_qc_passed'] is True
    assert project['result_data']['moments'][0]['final_review']['passed'] is True


def test_missing_source_fails_without_placeholder(isolated_app):
    create_project("missing_test", "viral", "Missing", {})
    create_job("missing_job", "missing_test")
    try:
        asyncio.run(ViralPipeline.run("missing_job", "missing_test", None, 3, {}, lambda *a: None))
    except ValueError:
        pass
    assert get_job("missing_job")["status"] == "FAILED"
    assert get_project("missing_test")["clips"] == []

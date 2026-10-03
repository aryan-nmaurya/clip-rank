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


def test_viral(isolated_app, long_footage, ranking_vision, rendered_watermarks):
    create_project("viral_test", "viral", "Real footage", {"count": 3})
    create_job("viral_job", "viral_test")
    asyncio.run(ViralPipeline.run("viral_job", "viral_test", str(long_footage[0]), 3,
                                 {"ai_provider": "auto", "target_duration": 25,
                                  "watermark_enabled":True,"watermark_text":"@ViralBrand"}, lambda *a: None))
    project = get_project("viral_test")
    assert project["status"] == "COMPLETED"
    assert rendered_watermarks == ['@ViralBrand'] * len(project['clips'])
    assert 1 <= len(project["clips"]) <= 2  # Only complete, distinct moments are exported
    assert project["clips"][0]["duration"] > 10
    assert project["result_data"]["generated_count"] == len(project["clips"])
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


def test_source_caption_groups_preserve_zero_duration_and_overlapping_tokens():
    from app.media.production_qc import ProductionQC
    words=[{'word':'お','start':.05,'end':.19},
           {'word':'す','start':.19,'end':.19},
           {'word':'す','start':.19,'end':.35},
           {'word':'め','start':.34,'end':.45},
           {'word':'です','start':.45,'end':.93}]
    grouped=Transcriber.normalize_words(words)
    assert ' '.join(w['word'] for w in grouped)=='お す す め です'
    assert grouped[0]['start']==.05 and grouped[-1]['end']==.93
    assert ProductionQC.validate_words(grouped,1)


def test_caption_grouping_never_makes_up_an_unmeasured_interval():
    import pytest
    with pytest.raises(ValueError,match='duration'):
        Transcriber.normalize_words([{'word':'hello','start':1,'end':1}])
    with pytest.raises(ValueError,match='boundaries'):
        Transcriber.normalize_words([{'word':'hello','start':float('nan'),'end':1}])


def test_force_commentary_exports_with_collapsed_source_syllables(isolated_app,long_footage,ranking_vision,monkeypatch):
    words=Transcriber.normalize_words([
        {'word':'Look','start':.2,'end':.4},
        {'word':'at','start':.4,'end':.4},
        {'word':'this','start':.4,'end':.8},
        {'word':'again','start':4,'end':4.5}])
    monkeypatch.setattr(Transcriber,'transcribe',lambda _: {'available':True,'text':'Look at this again',
        'segments':[{'start':.2,'end':4.5,'text':'Look at this again','words':words}]})
    create_project('source_speech','viral','Real source speech',{})
    create_job('source_speech_job','source_speech')
    asyncio.run(ViralPipeline.run('source_speech_job','source_speech',str(long_footage[0]),1,
        {'ai_provider':'auto','force_commentary':True},lambda *a:None))
    project=get_project('source_speech')
    assert project['status']=='COMPLETED' and project['result_data']['production_qc_passed']
    timeline=project['result_data']['moments'][0]['timeline'][0]
    assert timeline['speech'] and timeline['original_speech'][-1]['words'][-1]['word']=='again'


def test_cut_tolerance_does_not_admit_words_outside_the_actual_video():
    from app.media.production_qc import ProductionQC
    words=[{'word':'before','start':1.98,'end':1.99},
           {'word':'inside','start':2.2,'end':2.6},
           {'word':'after','start':3.01,'end':3.02}]
    selected=Transcriber.words_in_window(words,2,3)
    assert [w['word'] for w in selected]==['inside']
    assert ProductionQC.validate_words(selected,1)


def test_one_rejected_story_does_not_discard_an_approved_short(isolated_app,long_footage,ranking_vision,monkeypatch):
    from app.ai.production_director import ProductionDirector
    original=ProductionDirector.plan_viral
    async def plan(provider,name,moment,*args,**kwargs):
        if moment['start']==12:
            raise ValueError('This candidate lacks a complete payoff')
        return await original(provider,name,moment,*args,**kwargs)
    monkeypatch.setattr(ProductionDirector,'plan_viral',plan)
    monkeypatch.setattr(MomentAnalyzer,'analyze',lambda *args,**kwargs:[
        {'start':0,'end':12,'score':80}, {'start':12,'end':24,'score':70}])
    create_project('partial_viral','viral','Partial source',{})
    create_job('partial_viral_job','partial_viral')
    asyncio.run(ViralPipeline.run('partial_viral_job','partial_viral',str(long_footage[0]),2,
        {'ai_provider':'auto'},lambda *args:None))
    project=get_project('partial_viral')
    assert project['status']=='COMPLETED' and len(project['clips'])==1
    assert project['result_data']['production_qc_passed'] is True
    assert project['result_data']['generated_count']==1
    assert project['result_data']['rejected_moments'][0]['start']==12
    assert 'complete payoff' in project['result_data']['rejected_moments'][0]['reason']
    from app.media.ffmpeg_core import FFmpegCore
    FFmpegCore.validate_output(isolated_app['viral']/(project['clips'][0]['id']+'.mp4'),12)

"""A Short made from a discovered moment uses the whole video, not just the ~10 s window found inside it."""
import asyncio
import pytest
from app.core.database import create_job, create_project, get_project
from app.pipelines.viral import viral_pipeline
from app.pipelines.viral.viral_pipeline import ViralPipeline

WINDOW = {'start': 6.0, 'end': 16.5, 'score': 80}      # what Discovery points at: ~10 s of a 24 s video


def produce(name, footage, **settings):
    create_project(name, 'viral', 'Whole video', {})
    create_job(name + '_job', name)
    asyncio.run(ViralPipeline.run(name + '_job', name, str(footage[0]), 1,
                {'ai_provider': 'auto', 'target_duration': 15, 'selected_moment': dict(WINDOW), **settings}, lambda *a: None))
    return get_project(name)


def test_whole_video_is_used_when_requested(isolated_app, long_footage, ranking_vision):
    project = produce('whole_on', long_footage, whole_video=True)
    assert project['status'] == 'COMPLETED' and project['clips'][0]['duration'] > 23.0          # the entire 24 s video
    record = project['result_data']['moments'][0]
    assert record['timeline'][0]['source_start'] == 0 and record['timeline'][0]['source_end'] >= 23.9
    assert record['qc']['passed'] and record['final_review']['passed']


def test_without_the_option_only_the_discovered_window_is_used(isolated_app, long_footage, ranking_vision):
    project = produce('whole_off', long_footage)
    assert 10.1 <= project['clips'][0]['duration'] < 12


def test_a_source_longer_than_a_short_is_capped_and_says_so(isolated_app, long_footage, ranking_vision, monkeypatch):
    monkeypatch.setattr(viral_pipeline, 'MAX_SHORT_SECONDS', 15.0)
    project = produce('whole_cap', long_footage, whole_video=True)
    assert 14.5 < project['clips'][0]['duration'] <= 15.2
    notes = ' '.join(r['reason'] for r in project['result_data']['rejected_moments'])
    assert 'a Short holds at most 15s' in notes and 'first 15s were used' in notes


def test_ranking_is_unaffected_and_the_profile_option_defaults_on(isolated_app):
    from app.studio import store
    store.init_studio()
    assert store.profile().use_whole_video is True
    from fastapi.testclient import TestClient
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 5000))
    body = client.post('/api/studio/whole-video', json={'enabled': False}, headers={'content-type': 'application/json'}).json()
    assert body['use_whole_video'] is False and store.profile().use_whole_video is False


# --- every engine except Ranking keeps the video whole --------------------------------------------------
def produce_clips_page(name, footage, **settings):
    """Viral Clips with no discovered window: the user's own upload."""
    create_project(name, 'viral', 'Own upload', {})
    create_job(name + '_job', name)
    asyncio.run(ViralPipeline.run(name + '_job', name, str(footage[0]), 1, {'ai_provider': 'auto', 'target_duration': 15, **settings}, lambda *a: None))
    return get_project(name)


def test_viral_clips_page_uses_the_whole_upload(isolated_app, long_footage, ranking_vision):
    project = produce_clips_page('own_whole', long_footage, whole_video=True)
    assert project['status'] == 'COMPLETED' and len(project['clips']) == 1 and project['clips'][0]['duration'] > 23.0
    assert project['result_data']['moments'][0]['timeline'][0]['source_start'] == 0


def test_viral_clips_page_can_still_pick_highlights_when_asked(isolated_app, long_footage, ranking_vision):
    project = produce_clips_page('own_cut', long_footage, whole_video=False)
    assert project['status'] == 'COMPLETED' and project['clips'][0]['duration'] < 23.0


def test_api_accepts_and_stores_the_whole_video_flag(isolated_app, long_footage, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import routes
    from app.api.server import app
    from app.ai.router import AIRouter
    async def ready(provider): return None
    monkeypatch.setattr(routes, 'validate_ranking_vision', ready)
    monkeypatch.setattr(routes.job_engine, 'submit_job', lambda job_id: None)
    client = TestClient(app, client=('127.0.0.1', 5000))
    def post(**form):
        return client.post('/api/projects/viral', data={'video_url': 'https://example.org/v', **form})
    first = post()
    assert first.status_code == 200 and get_project(first.json()['project_id'])['input_data']['whole_video'] is True      # the default
    second = post(whole_video='false')
    assert get_project(second.json()['project_id'])['input_data']['whole_video'] is False


# --- the model's own cut suggestion must not veto a whole-video edit ---------------------------------------
def story(cuts):
    import json
    return json.dumps({'complete_story': True, 'confidence': .95, 'title': 'Roof leap', 'hook': 'A daring jump', 'narration': 'He takes a breath before the leap',
                       'observed_action': 'A man jumps from a roof', 'cuts': cuts})


class Says:
    def __init__(self, text): self.text = text
    async def analyze_images(self, *a, **k): return self.text


SPEECH = [{'start': 0, 'end': 6, 'text': 'hello there', 'words': [{'word': 'hello', 'start': 2.0, 'end': 2.6}, {'word': 'there', 'start': 2.6, 'end': 3.2}]}]


def plan(cuts, **kw):
    from app.ai.production_director import ProductionDirector
    moment = {'start': 0.0, 'end': 24.0}
    return asyncio.run(ProductionDirector.plan_viral(Says(story(cuts)), 'fx', moment, 'sheet.jpg', SPEECH, **kw))


def test_a_cut_through_a_spoken_word_is_rejected_normally_but_ignored_for_a_whole_video():
    through_a_word = [{'start': 0.0, 'end': 2.3}]               # ends in the middle of "hello"
    with pytest.raises(ValueError, match='midword'):
        plan(through_a_word)
    whole = plan(through_a_word, whole=True)
    assert whole['cuts'] == [{'start': 0.0, 'end': 24.0}]       # the entire video, whatever the model suggested
    assert plan([], whole=True)['cuts'] == [{'start': 0.0, 'end': 24.0}]       # even no suggestion at all

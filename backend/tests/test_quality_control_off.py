"""With quality control OFF nothing is rejected for quality; with it ON every check still works."""
import asyncio
import json
import pytest
from app.core import database, qc
from app.core.runtime import run_process
from app.media.ffmpeg_core import FFmpegCore
from app.media.production_qc import ProductionQC


@pytest.fixture
def qc_off(monkeypatch):
    monkeypatch.setattr(qc, 'enabled', lambda: False)


def make(path, *, size='1080x1920', seconds=4, color=None, audio=True):
    src = f'color=c={color}:s={size}:r=30:d={seconds}' if color else f'testsrc2=size={size}:rate=30:duration={seconds}'
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', src]
    if audio:
        cmd += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}:sample_rate=48000']
    cmd += ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p'] + (['-c:a', 'aac', '-ar', '48000', '-ac', '2'] if audio else []) + [str(path)]
    run_process(cmd)
    return path


def test_default_setting_is_off_and_round_trips(isolated_app):
    assert database.get_settings()['quality_control'] is False
    database.update_settings({'quality_control': True})
    assert database.get_settings()['quality_control'] is True


def test_unreadable_settings_fall_back_to_checks_on(tmp_path, monkeypatch):
    monkeypatch.undo()
    qc.reset_cache()
    from app.core import database as db
    import app.core.qc as q
    monkeypatch.setattr(db, 'get_settings', lambda: (_ for _ in ()).throw(RuntimeError('no db')))
    q.reset_cache()
    assert q.enabled() is True


# --- the file checks ---------------------------------------------------------------------------------------
def test_a_one_second_black_silent_render_is_stored_when_off_and_refused_when_on(isolated_app, tmp_path, monkeypatch):
    from app.storage.manager import StorageManager
    bad = make(tmp_path / 'bad.mp4', size='640x360', seconds=1, color='black', audio=False)
    with pytest.raises(ValueError):
        StorageManager.move_final_clip(bad, 'ranking', 'clip_checked')              # ON (the suite default)
    monkeypatch.setattr(qc, 'enabled', lambda: False)
    stored = StorageManager.move_final_clip(bad, 'ranking', 'clip_unchecked')
    assert stored.is_file() and stored.stat().st_size > 0
    empty = tmp_path / 'empty.mp4'
    empty.write_bytes(b'')
    with pytest.raises(ValueError, match='no file'):
        StorageManager.move_final_clip(empty, 'ranking', 'clip_empty')              # even OFF: a render must exist


def test_inspection_is_skipped_when_off_but_forced_for_the_production_test(tmp_path, monkeypatch):
    bad = make(tmp_path / 'black.mp4', seconds=2, color='black')
    with pytest.raises(ValueError, match='black section'):
        ProductionQC.inspect(bad, 2, tmp_path / 'qc', [], False)
    monkeypatch.setattr(qc, 'enabled', lambda: False)
    report = ProductionQC.inspect(bad, 2, tmp_path / 'qc2', [], False)
    assert report['passed'] and report['skipped'] and report['proxy'] is None and report['info']['width'] == 1080
    with pytest.raises(ValueError, match='black section'):
        ProductionQC.inspect(bad, 2, tmp_path / 'qc3', [], False, force=True)      # Diagnostics always runs real checks


def test_validate_output_and_minimum_length(tmp_path, monkeypatch):
    short = make(tmp_path / 'short.mp4', size='640x360', seconds=2)
    with pytest.raises(ValueError):
        FFmpegCore.validate_output(short, 2)
    with pytest.raises(ValueError, match='longer than 10 seconds'):
        ProductionQC.require_duration(3)
    monkeypatch.setattr(qc, 'enabled', lambda: False)
    assert FFmpegCore.validate_output(short, 2)['width'] == 640
    assert ProductionQC.require_duration(3) is True
    assert ProductionQC.min_duration() == .5
    with pytest.raises(ValueError):
        ProductionQC.require_duration(float('nan'))                                 # still needs a real number


def test_publish_gate_for_the_file_is_open_when_off(isolated_app, monkeypatch):
    from app.publishing import gates
    assert not gates.technical_gate({'video_path': '/output/ranking/nothing.mp4'})['passed']
    monkeypatch.setattr(qc, 'enabled', lambda: False)
    assert gates.technical_gate({'video_path': '/output/ranking/nothing.mp4'})['passed']


# --- the AI checks -----------------------------------------------------------------------------------------
class Boom:
    """Any call to the AI fails the test: with quality control off the checks must not spend a single request."""
    async def analyze_images(self, *a, **k): raise AssertionError('the AI was called')
    async def analyze_video(self, *a, **k): raise AssertionError('the AI was called')
    async def generate_text(self, *a, **k): raise AssertionError('the AI was called')


def test_no_ai_calls_are_spent_on_reviews_or_source_screening_when_off(qc_off, tmp_path):
    from app.ai.production_director import ProductionDirector
    from app.ai.ranking_verifier import RankingVerifier
    from app.ai.router import AIRouter
    review = asyncio.run(ProductionDirector.final_review(Boom(), 'fx', None, [], 'parkour', [{'rank': 1, 'timeline_start': 0, 'duration': 5}]))
    assert review['passed'] and review['skipped']
    blind = asyncio.run(RankingVerifier.review(Boom(), 'fx', {'label': 'x', 'commentary': 'y'}, 'sheet.jpg', 'parkour'))
    assert blind['passed'] and blind['skipped']
    screen = asyncio.run(AIRouter.screen_ranking_source(tmp_path / 's.jpg', {'title': 't'}, {}, provider_info=(Boom(), 'fx'), required=True))
    assert screen['suitable_raw'] and not screen['compilation']


class Describes:
    def __init__(self, payload): self.payload = payload
    async def analyze_images(self, *a, **k): return json.dumps(self.payload)


def test_every_candidate_window_passes_when_off_and_nothing_claims_it_was_verified():
    from app.ai.ranking_verifier import RankingVerifier
    windows = [{'start': 0.0, 'end': 10.0, 'score': 55}, {'start': 10.0, 'end': 20.0}, {'start': 20.0, 'end': 30.0}]
    reply = {'moments': [{'id': 0, 'matches_topic': False, 'complete_action': False, 'confidence': .1, 'graphic_injury': True, 'score': 90,
                          'label': 'Wall jump recovery', 'commentary': 'x ' * 30, 'event_start': -5, 'payoff_time': 99, 'event_end': 100}]}
    on = asyncio.run(RankingVerifier.evaluate(Describes(reply), 'fx', windows, 'sheet.jpg', 'parkour'))
    assert on == []                                                                  # ON: that verdict is a rejection
    import app.core.qc as q
    original = q.enabled
    q.enabled = lambda: False
    try:
        off = asyncio.run(RankingVerifier.evaluate(Describes(reply), 'fx', windows, 'sheet.jpg', 'parkour'))
    finally:
        q.enabled = original
    assert len(off) == 3 and off[0]['label'] == 'Wall jump recovery' and off[0]['score'] == 90
    for item in off:
        assert item['topic_verified'] is False and item['graphic_injury'] is None and item['qc_skipped'] is True
        assert item['start'] <= item['event_start'] < item['payoff_time'] <= item['event_end'] <= item['end']
        assert len(item['commentary'].split()) <= 12 and len(item['label'].split()) >= 2


def test_settings_api_toggles_quality_control(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 5000))
    headers = {'content-type': 'application/json'}
    assert client.get('/api/settings').json()['quality_control'] is False
    assert client.post('/api/settings', json={'quality_control': True}, headers=headers).json()['quality_control'] is True


# --- end to end: an AI that rejects everything ------------------------------------------------------------
def hostile(provider):
    """Keep the fixture's script writer and ranking, but make every verification and review say no."""
    original = provider.analyze_images
    async def analyze(images, prompt, **kw):
        if prompt.startswith('COMPARATIVE RANKING'):
            return await original(images, prompt, **kw)
        if prompt.startswith('FINAL RENDER'):
            return json.dumps({'topic_matches': False, 'confidence': .1, 'reason': 'No.', 'issues': ['bad'], 'observations': []})
        if prompt.startswith('BLIND SOURCE EVENT REVIEW'):
            return json.dumps({'observed_action': 'x', 'outcome': 'uncertain', 'complete_action': False, 'confidence': .1, 'reason': 'No'})
        if '\n' not in prompt:
            return json.dumps({'suitable_raw': False, 'already_ranked': True, 'compilation': True, 'reason': 'Looks like a ranking.'})
        return json.dumps({'moments': [{'id': i, 'matches_topic': False, 'complete_action': False, 'already_ranked': True, 'graphic_injury': True,
                                        'topic_relevance': .1, 'confidence': .1} for i in range(8)]})
    provider.analyze_images = analyze
    return provider


def run_ranking(name, footage, count=3):
    from app.core.database import create_job, create_project, get_project
    from app.pipelines.ranking.ranking_pipeline import RankingPipeline
    create_project(name, 'ranking', 'Countdown', {'count': count})
    create_job(name + '_job', name)
    asyncio.run(RankingPipeline.run(name + '_job', name, 'Test moments', count,
        {'ai_provider': 'auto', 'source_files': [str(p) for p in footage], 'segment_duration': 3}, lambda *a: None))
    return get_project(name)


def test_ranking_with_quality_control_on_rejects_what_the_ai_rejects(isolated_app, ranking_footage, ranking_vision):
    hostile(ranking_vision)
    with pytest.raises(ValueError, match='Only 0'):
        run_ranking('qc_on', ranking_footage)


def test_ranking_with_quality_control_off_still_produces_the_video(isolated_app, ranking_footage, ranking_vision, qc_off):
    hostile(ranking_vision)
    project = run_ranking('qc_off', ranking_footage)
    assert project['status'] == 'COMPLETED' and len(project['clips']) >= 1
    result = project['result_data']
    assert result['quality_control'] == 'off' and result['topic_verified'] is False                       # honest about what was skipped
    assert all(v['final_review'].get('skipped') and v['qc'].get('skipped') for v in result['variants'])
    path = isolated_app['ranking'] / (project['clips'][0]['id'] + '.mp4')
    assert FFmpegCore.get_video_info(path)['has_video']
    assert database.clip_passed_production_qc(project['clips'][0])                                         # visible in the Library and downloadable

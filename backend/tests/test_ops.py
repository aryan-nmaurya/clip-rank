"""Health check, storage hygiene and the production test report real component failures."""
import asyncio
import os
import time
import pytest
from app.core import config, database, health
from app.core.runtime import run_process
from app.storage import usage


@pytest.fixture
def storage(isolated_app, monkeypatch):
    monkeypatch.setattr(config, 'TEMP_STORAGE_DIR', isolated_app['temp'])
    monkeypatch.setattr(config, 'PROJECTS_STORAGE_DIR', isolated_app['projects'])
    monkeypatch.setattr(config, 'STORAGE_DIR', isolated_app['root'])
    return isolated_app


def make_dir(root, name, megabytes=0, age_hours=0):
    folder = root / name
    folder.mkdir()
    (folder / 'blob.bin').write_bytes(b'0' * megabytes * 1024 * 1024)
    stamp = time.time() - age_hours * 3600
    os.utime(folder, (stamp, stamp))
    return folder


# --- health -----------------------------------------------------------------
def test_health_reports_the_specific_missing_component(monkeypatch):
    real = health.shutil.which
    monkeypatch.setattr(health.shutil, 'which', lambda name: None if name == 'ffmpeg' else real(name))
    report = health.run_health_check()
    assert not report['ready'] and report['status'] == 'FFmpeg missing.'
    assert any(p['id'] == 'ffmpeg' and 'brew install ffmpeg' in p['fix'] for p in report['problems'])


def test_a_crashing_check_is_a_finding_not_a_crash(monkeypatch):
    def boom():
        raise RuntimeError('probe exploded')
    monkeypatch.setattr(health, 'CHECKS', [('x', 'X', True, boom)])
    report = health.run_health_check()
    assert not report['ready'] and 'probe exploded' in report['checks'][0]['detail']


def test_optional_checks_do_not_block_ready(monkeypatch):
    monkeypatch.setattr(health, 'CHECKS', [('a', 'A', True, lambda: (True, 'fine', '')), ('b', 'B', False, lambda: (False, 'optional', 'x'))])
    assert health.run_health_check()['ready'] and health.run_health_check()['status'] == 'READY'


def test_health_endpoint_is_local_only(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    assert 'checks' in TestClient(app, client=('127.0.0.1', 1)).get('/api/health').json()
    assert TestClient(app, client=('198.51.100.7', 1)).get('/api/health').status_code == 403


# --- storage ----------------------------------------------------------------
def test_orphans_are_removed_but_live_and_recent_jobs_are_kept(storage):
    temp = storage['temp']
    old_orphan = make_dir(temp, 'job_orphan', 1, age_hours=5)
    recent = make_dir(temp, 'job_recent', 1, age_hours=0)
    running = make_dir(temp, 'job_running', 1, age_hours=5)
    database.create_project('p', 'ranking', 't', {})
    database.create_job('job_running', 'p')
    result = usage.cleanup_orphans()
    assert result['removed'] == ['job_orphan'] and result['freed_bytes'] >= 1024 * 1024
    assert not old_orphan.exists() and recent.exists() and running.exists()


def test_failed_job_keeps_its_workspace_for_diagnosis_until_retention(storage):
    failed = make_dir(storage['temp'], 'job_failed', 1, age_hours=2)
    database.create_project('p', 'ranking', 't', {})
    database.create_job('job_failed', 'p')
    database.update_job('job_failed', status='FAILED')
    assert usage.cleanup_orphans()['removed'] == [] and failed.exists()
    old = make_dir(storage['temp'], 'job_failed_old', 1, age_hours=config.TEMP_RETENTION_HOURS + 2)
    database.create_job('job_failed_old', 'p')
    database.update_job('job_failed_old', status='FAILED')
    assert usage.cleanup_orphans()['removed'] == ['job_failed_old'] and not old.exists()


def test_budget_removes_oldest_first_and_never_a_running_job(storage, monkeypatch):
    temp = storage['temp']
    oldest = make_dir(temp, 'job_a', 3, age_hours=30)
    running = make_dir(temp, 'job_b', 3, age_hours=20)
    newest = make_dir(temp, 'job_c', 3, age_hours=10)
    database.create_project('p', 'ranking', 't', {})
    database.create_job('job_b', 'p')
    monkeypatch.setattr(usage, 'TEMP_BUDGET_BYTES', 5 * 1024 * 1024)
    usage.enforce_budget()
    assert not oldest.exists() and running.exists() and not newest.exists()


def test_cleanup_never_follows_a_symlink_out_of_managed_storage(storage, tmp_path):
    outside = tmp_path / 'precious'
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep')
    (storage['temp'] / 'job_link').symlink_to(outside, target_is_directory=True)
    usage.cleanup_orphans(now=time.time() + 10 * 3600)
    assert (outside / 'keep.txt').read_text() == 'keep'
    assert usage._remove.__name__  # direct call is contained too
    assert usage._remove(outside) == 0 and outside.exists()


def test_job_quota_stops_a_runaway_workspace(storage):
    folder = make_dir(storage['temp'], 'job_big', 2)
    usage.assert_job_room(folder, quota_bytes=10 * 1024 * 1024)
    with pytest.raises(OSError, match='disk quota'):
        usage.assert_job_room(folder, quota_bytes=1024 * 1024)


# --- production test --------------------------------------------------------
@pytest.fixture
def fake_stack(storage, monkeypatch):
    from app.ai.router import AIRouter
    from app.core import production_test as pt
    from app.transcription.transcriber import Transcriber
    from app.tts.voice_engine import TTSEngine
    monkeypatch.setattr(config, 'OUTPUT_STORAGE_DIR', storage['root'] / 'output')
    async def fake_tts(text, path, voice=None):
        words = [{'word': w, 'start': i * .3, 'end': (i + 1) * .3} for i, w in enumerate(text.split())]
        duration = words[-1]['end'] + .2
        path.parent.mkdir(parents=True, exist_ok=True)
        run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=500:duration={duration}:sample_rate=48000', str(path)])
        return {'duration': duration, 'text': text, 'words': words, 'segments': [{'start': 0, 'end': words[-1]['end'], 'text': text, 'words': words}]}
    monkeypatch.setattr(TTSEngine, 'synthesize_timed', fake_tts)
    monkeypatch.setattr(TTSEngine, 'validate_ready', TTSEngine.resolve_voice)
    words = [{'word': w, 'start': i * .3, 'end': (i + 1) * .3} for i, w in enumerate(pt.SPOKEN.split())]
    monkeypatch.setattr(Transcriber, 'transcribe', lambda _: {'available': True, 'text': pt.SPOKEN, 'segments': [
        {'start': 0, 'end': words[-1]['end'], 'text': pt.SPOKEN, 'words': words}]})
    class Provider:
        async def analyze_images(self, images, prompt, **kwargs):
            return '{"red_circles": 1, "blue_squares": 2}'
    async def connected(*a, **k): return Provider(), 'fixture'
    monkeypatch.setattr(AIRouter, 'get_active_provider', connected)
    return pt


def test_production_test_makes_and_verifies_a_real_mp4(fake_stack, storage):
    state = asyncio.run(fake_stack.ProductionTest.run_to_completion())
    assert state['ready'] and state['verdict'] == 'SYSTEM READY', state['steps']
    assert [s['status'] for s in state['steps']] == ['passed'] * 7
    from app.media.verify import verify_mp4
    report = verify_mp4(fake_stack.output_dir() / fake_stack.OUTPUT_NAME)
    assert (report['width'], report['height']) == (1080, 1920) and report['duration'] > 10.1
    assert not list(storage['temp'].iterdir()), 'the test workspace must be removed'


def test_production_test_names_the_failing_component(fake_stack, monkeypatch):
    from app.transcription.transcriber import Transcriber
    monkeypatch.setattr(Transcriber, 'transcribe', lambda _: {'available': False, 'warning': 'Whisper model missing', 'segments': [], 'text': ''})
    state = asyncio.run(fake_stack.ProductionTest.run_to_completion())
    assert not state['ready'] and state['failed_component'] == 'Transcription'
    assert state['verdict'] == 'NOT READY — Transcription failed'
    statuses = {s['name']: s['status'] for s in state['steps']}
    assert statuses['Source ingestion'] == 'passed' and statuses['FFmpeg render'] == 'skipped'
    assert 'Whisper model missing' in next(s for s in state['steps'] if s['status'] == 'failed')['detail']


def test_production_test_fails_when_no_ai_provider_is_connected(fake_stack, monkeypatch):
    from app.ai.router import AIRouter
    async def none(*a, **k): return None, 'fallback'
    monkeypatch.setattr(AIRouter, 'get_active_provider', none)
    state = asyncio.run(fake_stack.ProductionTest.run_to_completion())
    assert state['failed_component'] == 'AI analysis' and 'No AI vision provider' in next(s for s in state['steps'] if s['status'] == 'failed')['detail']

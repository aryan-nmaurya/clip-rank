"""After a quota stop, Retry continues from the saved checkpoint instead of starting over."""
import asyncio
import json
import time
import pytest
from app.ai.errors import AIQuotaExceeded
from app.ai.router import AIRouter
from app.core import database
from app.pipelines.ranking import checkpoint, ranking_pipeline as rp
from app.pipelines.ranking.ranking_pipeline import RankingPipeline

SETTINGS = {'voice_synthesizer': object(), 'variants': 1}


@pytest.fixture
def world(isolated_app, monkeypatch):
    """Fake discovery (real files), counted verification, recorded production."""
    state = {'discover_calls': [], 'verified': [], 'produced': [], 'quota_after': None, 'produce_fails': False, 'accept_mod': 2}
    root = isolated_app['root']

    def discover(topic, count, temp, **kw):
        state['discover_calls'].append((kw['round_index'], sorted(kw['exclude_urls'])))
        out = []
        for i in range(4):
            n = kw['round_index'] * 4 + i
            if f"https://v/{n}" in kw['exclude_urls']:
                continue
            path = temp / 'downloads' / f'clip_{n}.mp4'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'video-%d' % n)
            out.append({'id': f'source_{n}', 'title': f't{n}', 'url': f'https://v/{n}', 'source_id': f'yt:{n}',
                        'source_sha256': f'sha{n}', 'file_path': str(path), 'duration': 30})
        return out

    async def verify(cls, idx, source, topic, count, settings, provider_info, temp, rejections):
        if state['quota_after'] is not None and len(state['verified']) >= state['quota_after']:
            raise AIQuotaExceeded('gemini', 40000, 'spent')
        await asyncio.sleep(0)
        state['verified'].append(source['url'])
        assert open(source['file_path'], 'rb').read().startswith(b'video-')    # the durable copy is readable
        if int(source['url'].rsplit('/', 1)[1]) % state['accept_mod'] == 0:
            return {**source, 'source_id': source['source_id'], 'score': 70, 'source_transcript': {'segments': []}}
        rejections.append({'title': source['title'], 'url': source['url'], 'reason': 'not an event'})

    async def produce(cls, job_id, project_id, topic, count, settings, provider_info, temp, pool, *a, **k):
        if state['produce_fails']:
            raise AIQuotaExceeded('gemini', 40000, 'spent while scripting')
        state['produced'].append(sorted(m['url'] for m in pool))

    async def provider(*a, **k): return object(), 'fixture'
    monkeypatch.setattr(AIRouter, 'require_ranking_provider', provider)
    monkeypatch.setattr(rp.SourceDiscovery, 'discover_candidate_videos', staticmethod(discover))
    monkeypatch.setattr(RankingPipeline, 'verify_source', classmethod(verify))
    monkeypatch.setattr(RankingPipeline, 'produce_verified_pool', classmethod(produce))
    monkeypatch.setattr(rp, 'VERIFY_CONCURRENCY', 1)
    database.create_project('proj', 'ranking', 't', {})
    state['n'] = 0

    def run(topic='Parkour saves', count=3, extra=None):
        state['n'] += 1
        job = f"job{state['n']}"
        database.create_job(job, 'proj')
        asyncio.run(RankingPipeline.run(job, 'proj', topic, count, {**SETTINGS, **(extra or {})}, lambda *a: None))
        return job
    state['run'] = run
    return state


def test_retry_after_quota_does_not_re_judge_sources_already_verified(world):
    world['quota_after'] = 5
    with pytest.raises(AIQuotaExceeded):
        world['run']()
    first_pass = list(world['verified'])
    assert len(first_pass) == 5
    summary = checkpoint.summary('proj')
    assert summary['exists'] and summary['judged'] == 5 and summary['verified'] == 3 and summary['stage'] == 'verifying'

    world['quota_after'] = None                 # the quota reset
    world['run']()
    second_pass = world['verified'][5:]
    assert not set(second_pass) & set(first_pass)                 # nothing judged twice
    # Clips verified before the stop are in the final pool, alongside those verified after it.
    assert world['produced'] and {'https://v/0', 'https://v/2', 'https://v/4'} <= set(world['produced'][0])
    assert not checkpoint.summary('proj')['exists']                # finished: checkpoint removed
    assert not (checkpoint.directory('proj')).exists()


def test_rejections_from_the_first_attempt_are_kept(world):
    world['quota_after'] = 3
    with pytest.raises(AIQuotaExceeded):
        world['run']()
    saved = json.loads((checkpoint.directory('proj') / 'state.json').read_text())
    assert {r['url'] for r in saved['rejections'] if r.get('reason') == 'not an event'} == {'https://v/1'}
    world['quota_after'] = None
    world['run']()
    assert 'https://v/1' not in world['verified'][3:]


def test_downloaded_files_survive_the_job_workspace_being_deleted(world):
    from app.storage.manager import StorageManager
    world['quota_after'] = 2
    with pytest.raises(AIQuotaExceeded):
        world['run']()
    StorageManager.cleanup_job_temp('job1')                       # e.g. the 12 h retention sweep before quota reset
    world['quota_after'] = None
    world['run']()                                                # the verify stub reads each file's bytes
    assert world['produced']


def test_failure_after_the_pool_is_approved_resumes_at_production(world):
    world['produce_fails'] = True
    with pytest.raises(AIQuotaExceeded):
        world['run']()
    assert checkpoint.summary('proj')['stage'] == 'pool_ready' and checkpoint.summary('proj')['pool_ready']
    discovered, verified = len(world['discover_calls']), len(world['verified'])
    world['produce_fails'] = False
    world['run']()
    assert len(world['discover_calls']) == discovered and len(world['verified']) == verified    # no new searching or AI judging
    assert len(world['produced']) == 1 and len(world['produced'][0]) >= 3


def test_a_different_request_never_reuses_someone_elses_progress(world):
    world['quota_after'] = 4
    with pytest.raises(AIQuotaExceeded):
        world['run']()
    for kwargs in ({'topic': 'Football saves'}, {'count': 4}, {'extra': {'cc_only': True}}, {'extra': {'variants': 2}}):
        world['verified'].clear()
        world['quota_after'] = None
        world['run'](**kwargs)
        assert world['verified'], f'{kwargs} must start fresh'
        checkpoint.clear('proj')
        world['quota_after'] = 4
        with pytest.raises(AIQuotaExceeded):
            world['run']()


def test_stale_or_damaged_checkpoints_are_ignored(world, tmp_path):
    expected = checkpoint.signature('Parkour saves', 3, 1, SETTINGS)
    checkpoint.save('proj', {'signature': expected, 'stage': 'verifying', 'sources': [], 'moments': []})
    path = checkpoint.directory('proj') / 'state.json'
    assert checkpoint.load('proj', expected) is not None
    data = json.loads(path.read_text()); data['saved_at'] = time.time() - 8 * 86400
    path.write_text(json.dumps(data))
    assert checkpoint.load('proj', expected) is None and not path.exists()
    checkpoint.save('proj', {'signature': expected})
    path.write_text('{ not json')
    assert checkpoint.load('proj', expected) is None


def test_missing_files_drop_only_the_items_that_need_them(world):
    expected = checkpoint.signature('Parkour saves', 3, 1, SETTINGS)
    good = checkpoint.directory('proj') / 'media' / 'good.mp4'
    good.parent.mkdir(parents=True)
    good.write_bytes(b'x')
    checkpoint.save('proj', {'signature': expected, 'stage': 'pool_ready', 'sources': [{'file_path': str(good)}, {'file_path': '/gone.mp4'}],
                             'moments': [{'file_path': '/gone.mp4'}], 'pool': [{'file_path': '/gone.mp4'}]})
    state = checkpoint.load('proj', expected)
    assert len(state['sources']) == 1 and state['moments'] == [] and state['pool'] is None and state['stage'] == 'verifying'


def test_checkpoint_paths_cannot_escape_project_storage():
    for bad in ('../escape', 'a/b', '', 'x' * 200):
        with pytest.raises(ValueError):
            checkpoint.directory(bad)


def test_thin_pool_failure_keeps_verified_clips_and_restarts_the_search(world):
    world['accept_mod'] = 100        # only source 0 ever verifies, so the search budget runs out short of 3
    with pytest.raises(ValueError, match='Only'):
        world['run'](count=3, extra={'source_platforms': ['youtube']})
    summary = checkpoint.summary('proj')
    assert summary['exists'] and summary['verified'] == 1
    saved = json.loads((checkpoint.directory('proj') / 'state.json').read_text())
    assert saved['next_round'] == 0


# --- API and housekeeping ---------------------------------------------------
def test_checkpoint_endpoint_and_start_over(isolated_app, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.server import app
    from app.core.queue import JobEngine
    monkeypatch.setattr(JobEngine, 'submit_job', lambda self, job_id: None)
    database.create_project('proj', 'ranking', 't', {'topic': 'x'})
    database.create_job('j0', 'proj')
    database.update_job('j0', status='FAILED')
    database.update_project('proj', status='FAILED')
    client = TestClient(app)
    assert client.get('/api/projects/proj/checkpoint').json() == {'exists': False}
    expected = checkpoint.signature('x', 3, 1, {})
    checkpoint.save('proj', {'signature': expected, 'stage': 'verifying', 'sources': [], 'moments': [{'file_path': 'a'}], 'finished_keys': ['k1', 'k2']})
    info = client.get('/api/projects/proj/checkpoint').json()
    assert info['exists'] and info['verified'] == 1 and info['judged'] == 2
    assert client.get('/api/projects/missing/checkpoint').status_code == 404
    assert client.post('/api/projects/proj/regenerate').status_code == 200                 # plain retry keeps progress
    assert client.get('/api/projects/proj/checkpoint').json()['exists']
    database.update_project('proj', status='FAILED')
    assert client.post('/api/projects/proj/regenerate?fresh=true').status_code == 200      # start over discards it
    assert client.get('/api/projects/proj/checkpoint').json() == {'exists': False}


def test_old_checkpoints_are_swept_but_fresh_ones_and_other_files_are_kept(isolated_app):
    import os
    from app.storage import usage
    for name, age in (('old', 9), ('recent', 1)):
        checkpoint.save(name, {'signature': {}})
        stamp = time.time() - age * 86400
        os.utime(checkpoint.directory(name) / 'state.json', (stamp, stamp))
    keep = isolated_app['projects'] / 'old' / 'sources'
    keep.mkdir(parents=True)
    (keep / 'upload.mp4').write_bytes(b'user upload')
    assert usage.cleanup_stale_checkpoints() == ['old']
    assert checkpoint.directory('recent').exists() and not checkpoint.directory('old').exists()
    assert (keep / 'upload.mp4').exists()          # only the resume folder is ever removed

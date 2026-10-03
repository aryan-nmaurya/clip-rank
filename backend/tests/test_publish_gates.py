"""RIGHTS fails closed, ORIGINALITY rejects crop-and-caption reposts, TECHNICAL reads the real file."""
import pytest
from app.core import database
from app.core.runtime import run_process
from app.publishing import gates, youtube

CC = 'Creative Commons Attribution license (reuse allowed)'


def make_clip(isolated_app, *, enforce=True, mode='ranking', sources=None, narrated=True, seconds=11, size='1080x1920', attestation=None, clip='clip_one'):
    database.update_settings({'enforce_publish_gates': enforce})
    project_id = 'proj_gate'
    database.create_project(project_id, mode, 'Gate test', {})
    out = isolated_app['ranking']
    run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=30:duration={seconds}',
                 '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}:sample_rate=48000', '-c:v', 'libx264',
                 '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-ac', '2', str(out / f'{clip}.mp4')])
    sources = sources if sources is not None else [
        {'source_id': 'Youtube:a1', 'url': 'https://www.youtube.com/watch?v=a1', 'title': 'Save one', 'creator': 'Alice', 'license': 'unknown'}]
    speech = {'words': [{'word': 'watch', 'start': 0, 'end': .3}], 'text': 'watch closely'} if narrated else None
    timeline = [{'timeline_start': 0, 'duration': 6, 'narration': 'Watch closely at the landing spot', 'speech': speech}]
    record = {'clip_id': clip, 'hook': 'Parkour saves', 'timeline': timeline, 'qc': {'passed': True, 'captions_checked': True},
              'final_review': {'passed': True}, 'moments': [{'source_id': s.get('source_id') or s.get('id')} for s in sources]}
    result = {'production_qc_passed': True, 'sources': sources, 'variants': [record], 'moments': [record]}
    if attestation:
        result['rights_attestation'] = attestation
    database.update_project(project_id, result_data=result)
    database.create_job('job_gate', project_id)
    database.create_or_update_clip(clip, project_id, 'job_gate', 'Gate', duration=seconds, status='READY',
                                   video_path=f'/output/ranking/{clip}.mp4')
    return project_id, clip


def test_unverified_download_is_not_publishable(isolated_app):
    _, clip = make_clip(isolated_app)
    verdict = gates.evaluate(clip)
    assert not verdict['passed']
    assert verdict['gates']['rights']['status'] == 'REVIEW_REQUIRED'
    assert 'no documented licence' in verdict['gates']['rights']['reason']
    assert verdict['gates']['technical']['passed'] and verdict['gates']['originality']['passed']
    with pytest.raises(youtube.YouTubeError, match='RIGHTS'):
        gates.require_publishable(clip)


def test_creative_commons_attribution_with_source_url_passes_and_credits(isolated_app):
    sources = [{'source_id': 'Youtube:a1', 'url': 'https://www.youtube.com/watch?v=a1', 'title': 'Save', 'creator': 'Alice', 'license': CC}]
    _, clip = make_clip(isolated_app, sources=sources)
    verdict = gates.evaluate(clip)
    assert verdict['passed'] and verdict['gates']['rights']['status'] == 'PASS' and verdict['gates']['rights']['verified']
    assert 'Alice' in verdict['gates']['rights']['assets'][0]['attribution']


@pytest.mark.parametrize('license_text', ['Creative Commons Attribution-NonCommercial', 'CC BY-ND 4.0', 'unknown', ''])
def test_restricted_or_missing_licences_never_pass(isolated_app, license_text):
    sources = [{'source_id': 'Youtube:a1', 'url': 'https://www.youtube.com/watch?v=a1', 'title': 'Save', 'creator': 'Alice', 'license': license_text}]
    _, clip = make_clip(isolated_app, sources=sources)
    assert gates.evaluate(clip)['gates']['rights']['status'] == 'REVIEW_REQUIRED'


def test_owner_attestation_unlocks_only_the_sources_it_names(isolated_app):
    project_id, clip = make_clip(isolated_app)
    record = gates.record_attestation(project_id, 'written_permission', 'Email permission from Alice, saved in Drive/permissions', 'https://example.com/proof')
    assert record['source_keys']
    verdict = gates.evaluate(clip)
    assert verdict['passed'] and verdict['gates']['rights']['status'] == 'ATTESTED' and not verdict['gates']['rights']['verified']
    # A regenerated project that swaps in different footage is NOT covered by the old statement.
    project = database.get_project(project_id)
    result = project['result_data']
    result['sources'] = [{'source_id': 'Youtube:zzz', 'url': 'https://www.youtube.com/watch?v=zzz', 'title': 'Other', 'creator': 'Bob', 'license': 'unknown'}]
    result['variants'][0]['moments'] = [{'source_id': 'Youtube:zzz'}]
    database.update_project(project_id, result_data=result)
    assert gates.evaluate(clip)['gates']['rights']['status'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('basis,note,evidence', [('trust_me', 'long enough note here', None), ('owned', 'short', None),
                                                 ('owned', 'long enough note here', 'javascript:alert(1)')])
def test_attestation_requires_real_basis_note_and_http_evidence(isolated_app, basis, note, evidence):
    project_id, _ = make_clip(isolated_app)
    with pytest.raises(gates.AttestationError):
        gates.record_attestation(project_id, basis, note, evidence)


def test_caption_and_crop_without_commentary_fails_originality(isolated_app):
    _, clip = make_clip(isolated_app, mode='viral', narrated=False)
    verdict = gates.evaluate(clip)
    assert not verdict['gates']['originality']['passed']
    assert 'original commentary' in verdict['gates']['originality']['reason']


def test_technical_gate_reads_the_real_file(isolated_app):
    _, clip = make_clip(isolated_app, size='720x1280')
    technical = gates.evaluate(clip)['gates']['technical']
    assert not technical['passed'] and '720x1280' in technical['reason']


def test_rights_record_and_gate_endpoints_are_local_only(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    project_id, clip = make_clip(isolated_app)
    client = TestClient(app, client=('127.0.0.1', 5000))
    assert client.get(f'/api/clips/{clip}/gates').json()['gates']['rights']['status'] == 'REVIEW_REQUIRED'
    headers = {'content-type': 'application/json'}
    bad = client.post(f'/api/projects/{project_id}/rights', json={'basis': 'owned', 'note': 'x'}, headers=headers)
    assert bad.status_code == 422
    ok = client.post(f'/api/projects/{project_id}/rights', json={'basis': 'owned', 'note': 'I filmed this myself in 2025'}, headers=headers)
    assert ok.status_code == 200 and client.get(f'/api/clips/{clip}/gates').json()['gates']['rights']['status'] == 'ATTESTED'
    remote = TestClient(app, client=('203.0.113.9', 5000))
    assert remote.post(f'/api/projects/{project_id}/rights', json={'basis': 'owned', 'note': 'I filmed this myself'}, headers=headers).status_code == 403


def test_upload_is_refused_until_every_gate_passes(isolated_app, monkeypatch):
    root = isolated_app['root']
    monkeypatch.setattr(youtube, 'STORAGE_DIR', root)
    monkeypatch.setattr(youtube, 'OUTPUT_STORAGE_DIR', root / 'output')
    project_id, clip = make_clip(isolated_app)
    with pytest.raises(youtube.YouTubeError, match='Publishing blocked.*RIGHTS'):
        youtube.queue_upload(clip, {'privacy': 'private'})
    gates.record_attestation(project_id, 'owned', 'I filmed this footage myself on 2025-03-01')
    # Gates now pass, so the next obstacle is the (unconfigured) channel - not rights.
    with pytest.raises(youtube.YouTubeError, match='Connect your YouTube channel'):
        youtube.queue_upload(clip, {'privacy': 'private'})


def test_rights_and_originality_do_not_block_by_default_but_a_broken_file_still_does(isolated_app, monkeypatch):
    root = isolated_app['root']
    monkeypatch.setattr(youtube, 'STORAGE_DIR', root)
    monkeypatch.setattr(youtube, 'OUTPUT_STORAGE_DIR', root / 'output')
    project_id, clip = make_clip(isolated_app, enforce=False, mode='viral', narrated=False)   # unverified rights AND a caption-and-crop edit
    verdict = gates.evaluate(clip)
    assert verdict['passed'] and verdict['enforced'] is False and verdict['blockers'] == []
    assert not verdict['gates']['rights']['passed']                    # still reported, just not blocking
    gates.require_publishable(clip)                                    # no exception
    with pytest.raises(youtube.YouTubeError, match='Connect your YouTube channel'):
        youtube.queue_upload(clip, {'privacy': 'private'})             # next obstacle is the channel, not rights
    bad_project, bad_clip = None, None
    database.update_settings({'enforce_publish_gates': False})
    _, broken = make_clip(isolated_app, enforce=False, size='720x1280', clip='clip_broken')
    assert not gates.evaluate(broken)['passed'] and 'TECHNICAL' in gates.evaluate(broken)['blockers'][0]


def test_the_setting_round_trips_and_defaults_off(isolated_app):
    assert database.get_settings()['enforce_publish_gates'] is False
    database.update_settings({'enforce_publish_gates': True})
    assert gates.enforced() is True

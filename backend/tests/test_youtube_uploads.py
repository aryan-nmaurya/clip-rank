import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from app.core import database
from app.publishing import youtube

CLIENT = {'installed': {'client_id': 'example.apps.googleusercontent.com', 'client_secret': 'test-secret'}}
SESSION = 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture'


def response(status=200, body=None, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = body or {}
    return result


@pytest.fixture
def publishing(isolated_app, monkeypatch):
    from app.core.secrets import SecretVault
    secrets={}
    monkeypatch.setattr(SecretVault,'ready',lambda:True)
    monkeypatch.setattr(SecretVault,'get',lambda name:secrets.get(name))
    monkeypatch.setattr(SecretVault,'set',lambda name,value:secrets.update({name:value}))
    root = isolated_app['root']
    monkeypatch.setattr(youtube, 'AUTH_FILE', root / 'data' / 'youtube_connection.json')
    monkeypatch.setattr(youtube, 'STORAGE_DIR', root)
    monkeypatch.setattr(youtube, 'OUTPUT_STORAGE_DIR', root / 'output')
    # These tests exercise upload mechanics on a stub MP4; the publish gates have their own tests
    # (test_publish_gates.py), including that queue_upload is blocked without them.
    from app.publishing import gates
    monkeypatch.setattr(gates, 'require_publishable', lambda clip_id: None)
    executor = Mock()
    monkeypatch.setattr(youtube, '_executor', executor)
    youtube._active.clear()
    database.create_project('project', 'ranking', 'Ranking cats')
    database.create_job('job', 'project')
    path = isolated_app['ranking'] / 'finished.mp4'
    path.write_bytes(b'1234567890')
    database.create_or_update_clip('clip', 'project', 'job', 'Cat jumps over sofa', status='READY', video_path='/output/ranking/finished.mp4')
    database.update_project('project', result_data={'production_qc_passed':True,'moments': [{'clip_id':'clip','qc':{'passed':True},
        'final_review':{'passed':True},'creator': 'Creator', 'url': 'https://www.youtube.com/watch?v=raw'}]})
    yield {'path': path, 'executor': executor}
    youtube._active.clear()


def connected():
    youtube.configure(CLIENT)
    auth = youtube._read_auth()
    auth.update(refresh_token='test-refresh', access_token='test-access', expires_at=time.time() + 3600,
                channel={'id': 'channel', 'title': 'Test channel'})
    youtube._save_auth(auth)


def staged_for_copyright(monkeypatch):
    connected();youtube.queue_upload('clip',{'privacy':'public','made_for_kids':False})
    youtube._update('clip',status='UPLOADED',video_id='video_test',actual_privacy='private')
    youtube._active.clear()
    get=Mock(return_value=response(body={'items':[{'id':'video_test','status':{'uploadStatus':'processed','privacyStatus':'private'},
        'processingDetails':{'processingStatus':'succeeded'}}]}))
    monkeypatch.setattr(youtube.requests,'get',get)
    return get


def test_processing_success_is_not_copyright_clearance(publishing,monkeypatch):
    from app.publishing import copyright
    staged_for_copyright(monkeypatch)
    put=Mock();monkeypatch.setattr(youtube.requests,'put',put)
    assert copyright.refresh('clip')['state']=='PENDING_REVIEW'
    with pytest.raises(youtube.YouTubeError,match='copyright check'):copyright.release('clip')
    put.assert_not_called()


def test_copyright_review_binds_file_and_releases_once(publishing,monkeypatch):
    from app.publishing import copyright
    staged_for_copyright(monkeypatch)
    put=Mock(return_value=response(body={'id':'video_test','status':{'privacyStatus':'public'}}))
    monkeypatch.setattr(youtube.requests,'put',put)
    assert copyright.record_review('clip','passed','Explicit test owner reviewed checks: no unresolved issues.')['state']=='PASSED'
    assert copyright.release('clip')['actual_privacy']=='public'
    copyright.release('clip');assert put.call_count==1
    publishing['path'].write_bytes(b'a different MP4')
    with pytest.raises(youtube.YouTubeError,match='changed'):copyright.release('clip')


def test_youtube_copyright_rejection_cannot_be_overridden(publishing,monkeypatch):
    from app.publishing import copyright
    get=staged_for_copyright(monkeypatch)
    get.return_value=response(body={'items':[{'id':'video_test','status':{'uploadStatus':'rejected','rejectionReason':'copyright'}}]})
    assert copyright.refresh('clip')['state']=='BLOCKED'
    with pytest.raises(youtube.YouTubeError,match='blocked'):copyright.record_review('clip','passed','Explicit test attempt to override a rejected video.')


def test_missing_uploaded_video_never_receives_a_copyright_pass(publishing):
    from app.publishing import copyright
    with pytest.raises(youtube.YouTubeError,match='private upload'):copyright.record_review('clip','passed','No actual upload was made in this test.')


def test_oauth_pkce_state_tokens_not_exposed(publishing, monkeypatch):
    youtube.configure(CLIENT)
    url = youtube.start_authorization('http://127.0.0.1:8000/api/youtube/callback')
    query = parse_qs(urlparse(url).query)
    pending = youtube._read_auth()['pending']
    expected = base64.urlsafe_b64encode(hashlib.sha256(pending['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert query['code_challenge'] == [expected]
    assert query['scope'] == [youtube.SCOPES]
    post = Mock(return_value=response(body={'refresh_token': 'test-refresh', 'access_token': 'test-access', 'scope': youtube.SCOPES}))
    monkeypatch.setattr(youtube.requests, 'post', post)
    monkeypatch.setattr(youtube.requests, 'get', Mock(return_value=response(body={'items': [{'id': 'channel', 'snippet': {'title': 'Test channel'}}]})))
    with pytest.raises(youtube.YouTubeError, match='invalid'):
        youtube.finish_authorization('wrong-state', 'code')
    post.assert_not_called()
    status = youtube.finish_authorization(pending['state'], 'code')
    assert status['connected'] and status['channel_title'] == 'Test channel'
    assert 'secret' not in json.dumps(status) and 'token' not in json.dumps(status)
    assert youtube.AUTH_FILE.stat().st_mode & 0o777 == 0o600
    with pytest.raises(youtube.YouTubeError):
        youtube.finish_authorization(pending['state'], 'code')


def test_expired_denied_and_wrong_client(publishing):
    with pytest.raises(youtube.YouTubeError, match='Desktop'):
        youtube.configure({'web': CLIENT['installed']})
    youtube.configure(CLIENT)
    youtube.start_authorization('http://127.0.0.1:8000/api/youtube/callback')
    auth = youtube._read_auth()
    state = auth['pending']['state']
    auth['pending']['expires'] = 0
    youtube._save_auth(auth)
    with pytest.raises(youtube.YouTubeError, match='expired'):
        youtube.finish_authorization(state, 'code')
    youtube.start_authorization('http://127.0.0.1:8000/api/youtube/callback')
    state = youtube._read_auth()['pending']['state']
    with pytest.raises(youtube.YouTubeError, match='cancelled'):
        youtube.finish_authorization(state, denied=True)
    assert 'pending' not in youtube._read_auth()


def test_disconnected_rejected_before_upload(publishing):
    with pytest.raises(youtube.YouTubeError, match='Connect'):
        youtube.queue_upload('clip', {})
    assert youtube.get_upload('clip') is None
    publishing['executor'].submit.assert_not_called()


def test_direct_upload_metadata_progress_duplicate_click(publishing, monkeypatch):
    connected()
    monkeypatch.setattr(youtube, 'CHUNK_SIZE', 8)
    post = Mock(return_value=response(headers={'Location': SESSION}))
    put = Mock(side_effect=[response(308, headers={'Range': 'bytes=0-7'}), response(201, {'id': 'video_test', 'status': {'privacyStatus': 'private'}})])
    monkeypatch.setattr(youtube.requests, 'post', post)
    monkeypatch.setattr(youtube.requests, 'put', put)
    first = youtube.queue_upload('clip', {'privacy': 'public', 'made_for_kids': False, 'description': 'A funny cat.'})
    second = youtube.queue_upload('clip', {})
    assert first['clip_id'] == second['clip_id']
    assert publishing['executor'].submit.call_count == 1
    assert 'session_uri' not in first
    with pytest.raises(youtube.YouTubeError, match='active upload'):
        youtube.disconnect()
    youtube._run_upload('clip')
    payload = post.call_args.kwargs['json']
    assert payload['snippet']['title'] == 'Cat jumps over sofa'
    assert payload['snippet']['description'] == '\n'.join(['.']*13)+'\n\nCredits:\nCreator\n\nAbout this Short:\nCat jumps over sofa.\n\nA funny cat.'
    assert len(payload['snippet']['tags'])==32 and payload['snippet']['tags']==first['metadata']['tags']
    assert payload['status'] == {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False}
    assert first['metadata']['privacy']=='public' and first['metadata']['copyright_gate'] is True
    assert put.call_args_list[0].kwargs['headers']['Content-Range'] == 'bytes 0-7/10'
    assert put.call_args_list[1].kwargs['headers']['Content-Range'] == 'bytes 8-9/10'
    assert put.call_args_list[1].kwargs['data'] == b'90'
    result = youtube.get_upload('clip')
    assert result['status'] == 'UPLOADED' and result['progress'] == 100
    assert result['actual_privacy'] == 'private'  # Shows actual Google result, not requested public.
    youtube.queue_upload('clip', {})
    assert publishing['executor'].submit.call_count == 1


def test_interrupted_upload_resumes_server_offset_without_new_insert(publishing, monkeypatch):
    connected()
    monkeypatch.setattr(youtube, 'CHUNK_SIZE', 8)
    post = Mock(return_value=response(headers={'Location': SESSION}))
    put = Mock(side_effect=[response(308, headers={'Range': 'bytes=0-7'}), requests.ConnectionError('secret url')])
    monkeypatch.setattr(youtube.requests, 'post', post)
    monkeypatch.setattr(youtube.requests, 'put', put)
    youtube.queue_upload('clip', {})
    youtube._run_upload('clip')
    assert youtube.get_upload('clip')['status'] == 'FAILED'
    assert youtube.get_upload('clip')['progress'] == 80
    assert 'secret url' not in youtube.get_upload('clip')['error']
    put.side_effect = [response(308, headers={'Range': 'bytes=0-7'}), response(201, {'id': 'video_test'})]
    youtube.queue_upload('clip', {'title': 'Ignored changed title'})
    youtube._run_upload('clip')
    assert post.call_count == 1
    assert put.call_args_list[2].kwargs['headers']['Content-Range'] == 'bytes */10'
    assert put.call_args_list[3].kwargs['data'] == b'90'
    assert youtube.get_upload('clip')['status'] == 'UPLOADED'


def test_lost_completion_response_checked_without_reupload(publishing, monkeypatch):
    connected()
    post = Mock(return_value=response(headers={'Location': SESSION}))
    put = Mock(side_effect=requests.ConnectionError())
    monkeypatch.setattr(youtube.requests, 'post', post)
    monkeypatch.setattr(youtube.requests, 'put', put)
    youtube.queue_upload('clip', {})
    youtube._run_upload('clip')
    put.side_effect = [response(201, {'id': 'already_uploaded'})]
    youtube.queue_upload('clip', {})
    youtube._run_upload('clip')
    assert post.call_count == 1 and put.call_count == 2
    assert youtube.get_upload('clip')['video_id'] == 'already_uploaded'


def test_file_boundary_and_changed_file(publishing, monkeypatch):
    connected()
    conn = database.get_connection()
    with conn:
        conn.execute("UPDATE clips SET video_path='../private.mp4' WHERE id='clip'")
    conn.close()
    with pytest.raises(youtube.YouTubeError, match='missing'):
        youtube.queue_upload('clip', {})
    conn = database.get_connection()
    with conn:
        conn.execute("UPDATE clips SET video_path='/output/ranking/finished.mp4' WHERE id='clip'")
    conn.close()
    youtube.queue_upload('clip', {})
    publishing['path'].write_bytes(b'changed video')
    post = Mock()
    monkeypatch.setattr(youtube.requests, 'post', post)
    youtube._run_upload('clip')
    post.assert_not_called()
    assert 'changed' in youtube.get_upload('clip')['error']


def test_refresh_restart_and_disconnect(publishing, monkeypatch):
    connected()
    auth = youtube._read_auth()
    auth['expires_at'] = 0
    youtube._save_auth(auth)
    post = Mock(return_value=response(body={'access_token': 'new-token', 'expires_in': 3600}))
    monkeypatch.setattr(youtube.requests, 'post', post)
    assert youtube._access_token('channel') == 'new-token'
    assert post.call_args.kwargs['data']['grant_type'] == 'refresh_token'
    youtube.queue_upload('clip', {})
    youtube._active.clear()  # Simulates the old process ending.
    youtube.recover_interrupted()
    assert youtube.get_upload('clip')['status'] == 'FAILED'
    assert not youtube.disconnect()['connected']
    assert 'refresh_token' not in youtube._read_auth()


def test_upload_api_local_origin_and_validation(publishing):
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 50000))
    connected()
    assert client.post('/api/youtube/uploads/clip', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/youtube/uploads/clip', content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert TestClient(app, client=('192.168.1.5', 50000)).post('/api/youtube/uploads/clip', json={}).status_code == 403
    assert client.post('/api/youtube/uploads/clip', json={'privacy': 'invalid'}).status_code == 422
    assert client.post('/api/youtube/uploads/clip', json={'title': 'bad <title>'}).status_code == 400
    assert client.post('/api/youtube/uploads/clip', json={'description': '猫' * 2000}).status_code == 400
    result = client.post('/api/youtube/uploads/clip', json={}, headers={'Origin': 'http://localhost:8000'})
    assert result.status_code == 200 and result.json()['status'] == 'QUEUED'
    assert 'token' not in json.dumps(client.get('/api/youtube/connection').json())


def test_callback_html_escapes_channel(publishing, monkeypatch):
    from app.api.server import app
    monkeypatch.setattr(youtube, 'finish_authorization', lambda *args, **kwargs: {'channel_title': '<script>test</script>'})
    result = TestClient(app, client=('127.0.0.1', 50000)).get('/api/youtube/callback?state=test&code=test')
    assert result.status_code == 200
    assert '<script>' not in result.text and '&lt;script&gt;' in result.text
    assert result.headers['referrer-policy'] == 'no-referrer'


def test_credits_follow_the_uploaded_variant(publishing):
    database.update_project('project',result_data={'moments':[{'creator':'A creator','url':'https://example.org/A'}],
        'variants':[{'clip_id':'clip','moments':[{'creator':'B creator','url':'https://example.org/B'}]}]})
    credits=youtube._credits(database.get_clip('clip'))
    assert credits == 'B creator'


def test_unreviewed_export_cannot_start_upload(publishing):
    database.update_project('project',result_data={})
    with pytest.raises(youtube.YouTubeError,match='production QC'):
        youtube.queue_upload('clip',{})
    publishing['executor'].submit.assert_not_called()


def test_description_preview_removes_links_and_keeps_creator_voice_and_license_credits(publishing):
    database.update_project('project',result_data={'sources':[{'creator':'[The Creator](https://example.org/raw)',
        'license':'CC BY 4.0','url':'https://example.org/raw'}],
        'voice_provenance':[{'engine':'pocket-tts','voice':'alba'}]})
    text=youtube.description_preview('clip','Watch this! https://example.org/raw\nwww.example.com\nyoutu.be/raw')['description']
    assert text.startswith('\n'.join(['.']*13)+'\n\nCredits:\nThe Creator (CC BY 4.0)')
    assert 'Kyutai Pocket TTS (CC BY 4.0)' in text and 'Alba MacKenna voice, CC BY 4.0' in text
    assert text.endswith('Watch this!') and 'https:' not in text and 'example.com' not in text and 'youtu.be' not in text
    assert youtube.description_preview('clip',text)['description']==text
    from app.api.server import app
    result=TestClient(app,client=('127.0.0.1',50000)).post('/api/youtube/uploads/clip/preview',json={'description':'Watch this!'})
    assert result.status_code==200 and result.json()['description']==text


def test_copyright_review_api_publishes_cleared_video_without_second_click(publishing,monkeypatch):
    from app.api.server import app
    from app.publishing import copyright
    staged_for_copyright(monkeypatch)
    put=Mock(return_value=response(body={'id':'video_test','status':{'privacyStatus':'public'}}))
    monkeypatch.setattr(youtube.requests,'put',put)
    client=TestClient(app,client=('127.0.0.1',50000))
    result=client.post('/api/youtube/copyright/clip/review',json={'verdict':'passed','note':'Studio checks completed with no copyright issues.'})
    assert result.status_code==200 and result.json()['state']=='PUBLISHED'
    assert youtube.get_upload('clip')['actual_privacy']=='public'
    assert put.call_args.kwargs['json']['status']['privacyStatus']=='public'
    copyright.monitor_uploads()
    assert put.call_count==1


def test_background_monitor_does_not_treat_processing_as_copyright_clearance(publishing,monkeypatch):
    from app.publishing import copyright
    from app.studio.worker import StudioWorker
    from app.studio import store
    get=staged_for_copyright(monkeypatch)
    store.init_studio()
    profile=store.profile();profile.enabled=False;store.save_profile(profile)
    put=Mock();monkeypatch.setattr(youtube.requests,'put',put)
    StudioWorker().publish_due()
    assert copyright.read('clip')['state']=='PENDING_REVIEW'
    put.assert_not_called()
    get.return_value=response(body={'items':[{'id':'video_test','status':{'uploadStatus':'rejected','rejectionReason':'copyright'}}]})
    copyright.refresh('clip',force=True)
    copyright.monitor_uploads()
    assert copyright.read('clip')['state']=='BLOCKED'
    put.assert_not_called()


def test_automatic_release_retries_connection_failure_without_losing_review(publishing,monkeypatch):
    from app.publishing import copyright
    staged_for_copyright(monkeypatch)
    put=Mock(side_effect=[requests.ConnectionError(),response(body={'status':{'privacyStatus':'public'}})])
    monkeypatch.setattr(youtube.requests,'put',put)
    copyright.record_review('clip','passed','Studio copyright checks passed with no issues.')
    copyright.auto_release('clip')
    assert copyright.read('clip')['state']=='PASSED' and youtube.get_upload('clip')['actual_privacy']=='private'
    assert 'retry' in copyright.read('clip')['message']
    copyright.monitor_uploads();assert put.call_count==1
    saved=copyright.read('clip');saved['release_attempt_at']=0;copyright.save('clip',saved)
    copyright.monitor_uploads()
    assert copyright.read('clip')['state']=='PUBLISHED' and put.call_count==2


def test_automatic_release_keeps_api_restricted_video_private(publishing,monkeypatch):
    from app.publishing import copyright
    staged_for_copyright(monkeypatch)
    monkeypatch.setattr(youtube.requests,'put',Mock(return_value=response(body={'status':{'privacyStatus':'private'}})))
    copyright.record_review('clip','passed','Studio copyright checks passed with no issues.')
    copyright.auto_release('clip')
    assert copyright.read('clip')['state']=='PASSED'
    assert 'kept the video private' in copyright.read('clip')['message']
    assert youtube.get_upload('clip')['actual_privacy']=='private'


def test_automatic_release_respects_daily_studio_schedule(publishing,monkeypatch):
    from app.publishing import copyright
    from app.studio import store
    staged_for_copyright(monkeypatch)
    store.init_studio()
    with database.get_connection() as c:
        c.execute("INSERT INTO studio_tasks (id,opportunity_id,project_id,day,format,clip_id,created_at,updated_at) VALUES ('daily','o','project','2026-10-03','viral_clip','clip','now','now')")
    put=Mock();monkeypatch.setattr(youtube.requests,'put',put)
    copyright.record_review('clip','passed','Studio copyright checks passed with no issues.')
    copyright.auto_release('clip',force=True)
    copyright.monitor_uploads();put.assert_not_called()
    assert copyright.read('clip')['state']=='PASSED'


def test_existing_private_upload_description_update_keeps_video_metadata_and_visibility(publishing,monkeypatch):
    staged_for_copyright(monkeypatch)
    upload=youtube.get_upload('clip')
    meta={**upload['metadata'],'description':'Original summary\n\nSource credits:\nCreator: https://example.org/raw'}
    with database.get_connection() as c:
        c.execute('UPDATE youtube_uploads SET metadata=? WHERE clip_id=?',(json.dumps(meta),'clip'))
    monkeypatch.setattr(youtube.requests,'get',Mock(return_value=response(body={'items':[{'id':'video_test','snippet':{
        'channelId':'channel','title':'Existing title','categoryId':'17','tags':['parkour'],'defaultLanguage':'en'}}]})))
    def sorted_reply(*args,**kwargs):
        body=json.loads(json.dumps(kwargs['json']))
        body['snippet']['tags']=sorted(body['snippet']['tags'])
        return response(body=body)
    put=Mock(side_effect=sorted_reply)
    monkeypatch.setattr(youtube.requests,'put',put)
    result=youtube.update_uploaded_description('clip')
    assert result['actual_privacy']=='private'
    assert result['metadata']['tags']==sorted(result['metadata']['tags'])
    assert result['metadata']['description']=='\n'.join(['.']*13)+'\n\nCredits:\nCreator\n\nAbout this Short:\nCat jumps over sofa.\n\nOriginal summary'
    snippet=put.call_args.kwargs['json']['snippet']
    assert snippet['title']=='Existing title' and len(snippet['tags'])==32 and snippet['categoryId']=='17'
    assert 'parkour' not in snippet['tags']
    assert 'status' not in put.call_args.kwargs['json']
    assert put.call_args.kwargs['params']=={'part':'snippet'}
    youtube.update_uploaded_description('clip');assert put.call_count==1


def test_legacy_staged_public_upload_also_releases_after_confirmed_review(publishing,monkeypatch):
    from app.publishing import copyright
    staged_for_copyright(monkeypatch)
    upload=youtube.get_upload('clip');meta=upload['metadata'];meta.pop('auto_release_after_copyright')
    with database.get_connection() as c:
        c.execute('UPDATE youtube_uploads SET metadata=? WHERE clip_id=?',(json.dumps(meta),'clip'))
    put=Mock(return_value=response(body={'status':{'privacyStatus':'public'}}))
    monkeypatch.setattr(youtube.requests,'put',put)
    copyright.record_review('clip','passed','Studio copyright checks passed with no issues.')
    copyright.monitor_uploads()
    assert copyright.read('clip')['state']=='PUBLISHED' and put.call_count==1


# --- a video deleted on YouTube can be uploaded again ------------------------
def uploaded_clip():
    connected()
    youtube.queue_upload('clip', {'privacy': 'private', 'made_for_kids': False})
    youtube._update('clip', status='UPLOADED', video_id='old_video', actual_privacy='private')
    youtube._active.clear()
    youtube._last_remote_check.clear()


def test_a_video_deleted_on_youtube_is_marked_deleted_and_its_copyright_record_cleared(publishing, monkeypatch):
    uploaded_clip()
    with database.get_connection() as c:
        c.execute('INSERT INTO copyright_checks VALUES(?,?)', ('clip', json.dumps({'state': 'PASSED', 'video_id': 'old_video'})))
    monkeypatch.setattr(youtube.requests, 'get', Mock(return_value=response(body={'items': []})))
    result = youtube.verify_remote('clip', force=True)
    assert result['status'] == 'DELETED' and 'removed from YouTube' in result['error'] and 'upload it again' in result['error']
    with database.get_connection() as c:
        assert c.execute('SELECT 1 FROM copyright_checks WHERE clip_id=?', ('clip',)).fetchone() is None


def test_an_existing_video_stays_uploaded_and_its_visibility_is_refreshed(publishing, monkeypatch):
    uploaded_clip()
    monkeypatch.setattr(youtube.requests, 'get', Mock(return_value=response(body={'items': [{'id': 'old_video', 'status': {'uploadStatus': 'processed', 'privacyStatus': 'public'}}]})))
    result = youtube.verify_remote('clip', force=True)
    assert result['status'] == 'UPLOADED' and result['actual_privacy'] == 'public'


@pytest.mark.parametrize('failure', [response(500), response(403), response(401), requests.ConnectionError('offline')])
def test_errors_never_mark_a_video_deleted(publishing, monkeypatch, failure):
    uploaded_clip()
    monkeypatch.setattr(youtube.requests, 'get', Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure))
    assert youtube.verify_remote('clip', force=True)['status'] == 'UPLOADED'


def test_remote_checks_are_throttled(publishing, monkeypatch):
    uploaded_clip()
    get = Mock(return_value=response(body={'items': [{'id': 'old_video', 'status': {'privacyStatus': 'private'}}]}))
    monkeypatch.setattr(youtube.requests, 'get', get)
    for _ in range(5):
        youtube.verify_remote('clip')
    assert get.call_count == 1
    youtube.verify_remote('clip', force=True)
    assert get.call_count == 2


def test_uploading_again_after_a_deletion_starts_a_fresh_upload(publishing, monkeypatch):
    uploaded_clip()
    monkeypatch.setattr(youtube.requests, 'get', Mock(return_value=response(body={'items': []})))
    fresh = youtube.queue_upload('clip', {'privacy': 'public', 'made_for_kids': False, 'title': 'Second try'})
    assert fresh['status'] == 'QUEUED' and fresh['video_id'] is None and fresh['metadata']['title'] == 'Second try'
    publishing['executor'].submit.assert_called()                                  # a real upload was scheduled
    assert youtube.get_upload('clip')['status'] == 'QUEUED'


def test_repeat_clicks_still_do_not_duplicate_a_video_that_exists(publishing, monkeypatch):
    uploaded_clip()
    monkeypatch.setattr(youtube.requests, 'get', Mock(return_value=response(body={'items': [{'id': 'old_video', 'status': {'privacyStatus': 'private'}}]})))
    again = youtube.queue_upload('clip', {})
    assert again['status'] == 'UPLOADED' and again['video_id'] == 'old_video'
    publishing['executor'].submit.assert_called_once()                              # only the original upload, no second one


def test_status_and_verify_endpoints_report_a_deleted_video(publishing, monkeypatch):
    from app.api.server import app
    uploaded_clip()
    monkeypatch.setattr(youtube.requests, 'get', Mock(return_value=response(body={'items': []})))
    client = TestClient(app, client=('127.0.0.1', 5000))
    body = client.get('/api/youtube/uploads/clip').json()
    assert body['status'] == 'DELETED'
    assert client.get('/api/youtube/uploads/nothing').json() is None


def test_reupload_clears_the_record_and_old_verdict_without_touching_youtube(publishing, monkeypatch):
    from app.api.server import app
    uploaded_clip()
    with database.get_connection() as c:
        c.execute('INSERT INTO copyright_checks VALUES(?,?)', ('clip', json.dumps({'state': 'PASSED'})))
    network = Mock()
    monkeypatch.setattr(youtube.requests, 'get', network); monkeypatch.setattr(youtube.requests, 'put', network)
    monkeypatch.setattr(youtube.requests, 'delete', network, raising=False)
    client = TestClient(app, client=('127.0.0.1', 5000))
    assert client.post('/api/youtube/uploads/clip/reupload', json={}, headers={'content-type': 'application/json'}).status_code == 200
    network.assert_not_called()                                    # YouTube is never contacted, let alone deleted from
    assert youtube.get_upload('clip') is None
    with database.get_connection() as c:
        assert c.execute('SELECT 1 FROM copyright_checks WHERE clip_id=?', ('clip',)).fetchone() is None
    again = youtube.queue_upload('clip', {'privacy': 'private', 'made_for_kids': False, 'title': 'Fresh'})
    assert again['status'] == 'QUEUED' and again['video_id'] is None and again['metadata']['title'] == 'Fresh'


def test_reupload_is_refused_while_a_transfer_is_running_or_nothing_exists(publishing):
    connected()
    with pytest.raises(youtube.YouTubeError, match='no upload'):
        youtube.reset_upload('clip')
    youtube.queue_upload('clip', {'privacy': 'private', 'made_for_kids': False})       # QUEUED / active
    with pytest.raises(youtube.YouTubeError, match='Wait for the running upload'):
        youtube.reset_upload('clip')


def test_a_failed_upload_can_be_discarded(publishing):
    connected()
    youtube.queue_upload('clip', {'privacy': 'private', 'made_for_kids': False})
    youtube._update('clip', status='FAILED', error='Connection interrupted.')
    youtube._active.clear()
    youtube.reset_upload('clip')
    assert youtube.get_upload('clip') is None

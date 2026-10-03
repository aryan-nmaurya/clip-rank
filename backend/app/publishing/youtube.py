"""Local OAuth storage and resumable uploads. No browser automation or Studio redirect."""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

from app.core import database
from app.core.config import DATA_DIR, OUTPUT_STORAGE_DIR, STORAGE_DIR
from app.core.secrets import SecretVault

AUTH_FILE = DATA_DIR / 'youtube_connection.json'
SCOPES = 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube.force-ssl https://www.googleapis.com/auth/yt-analytics.readonly'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_URL = 'https://www.googleapis.com/youtube/v3'
UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'
CHUNK_SIZE = 8 * 1024 * 1024  # Multiple of the protocol's 256 KiB chunk unit.
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='youtube-upload')
_active = set()


class YouTubeError(ValueError):
    pass


def _read_auth():
    if not AUTH_FILE.exists():
        return {}
    data=json.loads(AUTH_FILE.read_text())
    return SecretVault.decrypt(data) if 'ciphertext' in data else data


def _save_auth(data):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = AUTH_FILE.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(SecretVault.encrypt(data), stream)
    os.chmod(temporary, 0o600)
    temporary.replace(AUTH_FILE)


def connection_status():
    with _lock:
        data = _read_auth()
    channel = data.get('channel', {})
    return {'configured': bool(data.get('client_id')), 'connected': bool(data.get('refresh_token') and channel),
            'channel_title': channel.get('title'), 'channel_id': channel.get('id'),
            'default_privacy': data.get('default_privacy', 'public'),
            'default_made_for_kids': data.get('default_made_for_kids', False),
            'secure_storage_ready':SecretVault.ready(), 'analytics_connected':'https://www.googleapis.com/auth/yt-analytics.readonly' in data.get('scopes','')}


def _assert_idle():
    if _active:
        raise YouTubeError('Wait for the active upload before changing the YouTube connection.')


def configure(client_document=None, privacy='public', made_for_kids=False):
    with _lock:
        _assert_idle()
        data = _read_auth()
        if client_document is not None:
            client = client_document.get('installed', {})
            if not isinstance(client, dict):
                raise YouTubeError('Choose a Google OAuth client JSON for application type Desktop app.')
            client_id, secret = client.get('client_id', ''), client.get('client_secret', '')
            if not isinstance(client_id, str) or not client_id.endswith('.apps.googleusercontent.com') or not isinstance(secret, str) or not secret:
                raise YouTubeError('Choose a Google OAuth client JSON for application type Desktop app.')
            if data.get('client_id') != client_id or data.get('client_secret') != secret:
                data = {'client_id': client_id, 'client_secret': secret}
        data.update(default_privacy=privacy, default_made_for_kids=made_for_kids)
        _save_auth(data)
    return connection_status()


def start_authorization(redirect_uri):
    with _lock:
        _assert_idle()
        data = _read_auth()
        if not data.get('client_id'):
            raise YouTubeError('Add your Desktop app OAuth client JSON in Settings first.')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        data['pending'] = {'state': state, 'verifier': verifier, 'redirect_uri': redirect_uri, 'expires': time.time() + 600}
        _save_auth(data)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': data['client_id'], 'redirect_uri': redirect_uri, 'response_type': 'code',
            'scope': SCOPES, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline', 'prompt': 'consent select_account'})


def _check_response(response):
    if response.status_code >= 400:
        # Never include request URLs, resumable session URIs, tokens or response bodies in errors.
        if response.status_code == 401:
            raise YouTubeError('Google authorization expired. Reconnect your channel in Settings.')
        try:
            payload = response.json()
            reason = payload.get('error', {}).get('errors', [{}])[0].get('reason', '') if isinstance(payload.get('error'), dict) else payload.get('error', '')
        except (ValueError, AttributeError, IndexError):
            reason = ''
        known = {'quotaExceeded': 'YouTube API quota is exhausted. Try after the quota resets.',
                 'dailyLimitExceeded': 'YouTube API daily quota is exhausted.',
                 'uploadLimitExceeded': 'Your channel has reached its YouTube upload limit.',
                 'youtubeSignupRequired': 'Create a YouTube channel for this Google account first.',
                 'accessNotConfigured': 'Enable YouTube Data API v3 in your Google Cloud project.',
                 'invalid_grant': 'Google authorization expired or was revoked. Reconnect your channel.',
                 'insufficientPermissions': 'Reconnect your channel and allow the requested YouTube permissions.'}
        raise YouTubeError(known.get(reason, f'YouTube request failed (HTTP {response.status_code}). Check your channel connection and retry.'))


def finish_authorization(state, code=None, denied=False):
    with _lock:
        data = _read_auth()
        pending = data.get('pending', {})
        if not state or not secrets.compare_digest(state, pending.get('state', '')) or pending.get('expires', 0) < time.time():
            raise YouTubeError('This connection request expired or is invalid. Start Connect YouTube again.')
        _assert_idle()
        data.pop('pending', None)  # Single use, even if Google rejects the exchange.
        _save_auth(data)
        if denied or not code:
            raise YouTubeError('Connection cancelled. Return to Settings when ready to connect.')
        response = requests.post(TOKEN_URL, data={'client_id': data['client_id'], 'client_secret': data['client_secret'],
            'code': code, 'code_verifier': pending['verifier'], 'redirect_uri': pending['redirect_uri'],
            'grant_type': 'authorization_code'}, timeout=30)
        _check_response(response)
        token = response.json()
        if not token.get('refresh_token') or not token.get('access_token'):
            raise YouTubeError('Google did not grant offline access. Start Connect YouTube again.')
        if not set(SCOPES.split()).issubset(set(token.get('scope', '').split())):
            raise YouTubeError('Allow both upload and channel access to finish connecting.')
        channel_res = requests.get(API_URL + '/channels', params={'part': 'snippet', 'mine': 'true'},
            headers={'Authorization': 'Bearer ' + token['access_token']}, timeout=30)
        _check_response(channel_res)
        items = channel_res.json().get('items', [])
        if not items:
            raise YouTubeError('No YouTube channel found. Create a channel, then reconnect.')
        channel = items[0]
        data.update(refresh_token=token['refresh_token'], access_token=token['access_token'],
                    scopes=token.get('scope',''),
                    expires_at=time.time() + token.get('expires_in', 3600),
                    channel={'id': channel['id'], 'title': channel['snippet']['title']})
        _save_auth(data)
    return connection_status()


def disconnect():
    with _lock:
        _assert_idle()
        data = _read_auth()
        for key in ('refresh_token', 'access_token', 'expires_at', 'channel', 'pending'):
            data.pop(key, None)
        _save_auth(data)
    return connection_status()


def _access_token(channel_id):
    with _lock:
        data = _read_auth()
        if not data.get('refresh_token') or data.get('channel', {}).get('id') != channel_id:
            raise YouTubeError('Connect the original destination channel before resuming this upload.')
        if data.get('expires_at', 0) > time.time() + 60 and data.get('access_token'):
            return data['access_token']
        response = requests.post(TOKEN_URL, data={'client_id': data['client_id'], 'client_secret': data['client_secret'],
            'refresh_token': data['refresh_token'], 'grant_type': 'refresh_token'}, timeout=30)
        _check_response(response)
        token = response.json()
        data.update(access_token=token['access_token'], expires_at=time.time() + token.get('expires_in', 3600))
        _save_auth(data)
        return token['access_token']


def get_upload(clip_id, private=False):
    conn = database.get_connection()
    row = conn.execute('SELECT * FROM youtube_uploads WHERE clip_id = ?', (clip_id,)).fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    result['metadata'] = json.loads(result['metadata'])
    if not private:
        for key in ('session_uri', 'file_size', 'file_mtime'):
            result.pop(key, None)
        result['url'] = 'https://www.youtube.com/watch?v=' + result['video_id'] if result.get('video_id') else None
    return result


_last_remote_check = {}
REMOTE_CHECK_SECONDS = 120


def verify_remote(clip_id, force=False):
    """Is an uploaded video still on YouTube? Marks it DELETED only on a clear answer from YouTube.

    A network error, expired login or quota problem never marks anything deleted. Throttled to one
    check per clip every two minutes unless forced.
    """
    upload = get_upload(clip_id, private=True)
    if not upload or upload['status'] != 'UPLOADED' or not upload.get('video_id'):
        return upload and get_upload(clip_id)
    if not force and time.time() - _last_remote_check.get(clip_id, 0) < REMOTE_CHECK_SECONDS:
        return get_upload(clip_id)
    _last_remote_check[clip_id] = time.time()
    try:
        token = _access_token(upload['channel_id'])
        response = requests.get(API_URL + '/videos', params={'part': 'status', 'id': upload['video_id']},
                                headers={'Authorization': 'Bearer ' + token}, timeout=20)
    except (YouTubeError, requests.RequestException):
        return get_upload(clip_id)
    if response.status_code != 200:
        return get_upload(clip_id)
    items = response.json().get('items', [])
    gone = not items or items[0].get('status', {}).get('uploadStatus') in ('deleted', 'rejected', 'failed')
    if gone:
        reason = 'This video was removed from YouTube.' if not items else 'YouTube removed or rejected this video.'
        _update(clip_id, status='DELETED', error=reason + ' You can upload it again.')
        conn = database.get_connection()
        with conn:   # a copyright verdict belongs to the old video, not to a re-upload
            conn.execute('DELETE FROM copyright_checks WHERE clip_id = ?', (clip_id,))
        conn.close()
    elif items[0].get('status', {}).get('privacyStatus') != upload.get('actual_privacy'):
        _update(clip_id, actual_privacy=items[0]['status'].get('privacyStatus'))
    return get_upload(clip_id)


def reset_upload(clip_id):
    """Forget a finished/failed upload so the clip can be uploaded again as a new video.

    This only clears ClipRank's record (and the old copyright verdict, which belonged to the old video).
    It never touches YouTube: delete the old video there first to avoid a duplicate. An upload that is
    still transferring cannot be reset.
    """
    with _lock:
        upload = get_upload(clip_id, private=True)
        if not upload:
            raise YouTubeError('This clip has no upload to reset.')
        if clip_id in _active or upload['status'] in ('QUEUED', 'UPLOADING'):
            raise YouTubeError('Wait for the running upload to finish before uploading again.')
        conn = database.get_connection()
        with conn:
            conn.execute('DELETE FROM youtube_uploads WHERE clip_id = ?', (clip_id,))
            conn.execute('DELETE FROM copyright_checks WHERE clip_id = ?', (clip_id,))
        conn.close()
        _last_remote_check.pop(clip_id, None)
        return None


def _update(clip_id, **fields):
    allowed = {'status', 'progress', 'session_uri', 'video_id', 'actual_privacy', 'error'}  # status may also be DELETED
    assert set(fields).issubset(allowed)
    fields['updated_at'] = datetime.now(timezone.utc).isoformat()
    conn = database.get_connection()
    with conn:
        conn.execute('UPDATE youtube_uploads SET ' + ', '.join(k + ' = ?' for k in fields) + ' WHERE clip_id = ?',
                     (*fields.values(), clip_id))
    conn.close()


def _clip_file(clip):
    if not clip or clip.get('status') != 'READY' or not clip.get('video_path'):
        raise YouTubeError('Only a finished video can be uploaded.')
    path = (STORAGE_DIR / clip['video_path'].lstrip('/')).resolve()
    if not path.is_relative_to(OUTPUT_STORAGE_DIR.resolve()) or not path.is_file() or path.suffix.lower() != '.mp4':
        raise YouTubeError('The finished MP4 is missing from output storage. Regenerate this clip.')
    return path


def _credits(clip):
    project = database.get_project(clip['project_id']) or {}
    result = project.get('result_data', {})
    sources = result.get('moments') or result.get('sources', [])
    for variant in result.get('variants', []):
        if variant.get('clip_id') == clip['id']:
            sources = variant.get('moments', [])
            break
    else:
        matching = [source for source in sources if source.get('clip_id') == clip['id']]
        if matching:
            sources = matching
    lines = []
    for source in sources:
        if isinstance(source, dict):
            credit=source.get('attribution') or source.get('creator') or source.get('title')
            if credit:
                credit=_without_links(str(credit)).rstrip(': ')
                license=source.get('license')
                if license and str(license).lower() not in ('unknown','unverified') and str(license).lower() not in credit.lower():
                    credit+=f' ({license})'
                lines.append(credit)
            music=(source.get('music') or {}).get('provenance') or {}
            music_credit=music.get('attribution')
            if music_credit:
                lines.append(_without_links(str(music_credit)).rstrip(': '))
    if not lines and project.get('input_data', {}).get('video_url'):
        lines.append('Source creator not provided')
    for voice in result.get('voice_provenance',[]):
        if voice.get('engine')=='pocket-tts':
            from app.tts.pocket import PocketTTS
            # Use the server-maintained attribution, not arbitrary stored text.
            lines.extend(PocketTTS.provenance(voice['voice'])['credits'])
    return '\n'.join(dict.fromkeys(_without_links(line).rstrip(': ') for line in lines if _without_links(line)))


DESCRIPTION_PREFIX='\n'.join(['.']*13)


def _without_links(text):
    # Keep human-readable credit labels, including Markdown link labels.
    text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
    text=re.sub(r'(?i)\b(?:https?://|www\.)[^\s<>]+', '', text)
    text=re.sub(r'(?i)\b(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,63}(?::\d+)?(?:/[^\s<>]*)?', '', text)
    return '\n'.join(re.sub(r'[ \t]+',' ',line).strip() for line in text.splitlines()).strip()


def description_preview(clip_id,description=''):
    clip=database.get_clip(clip_id)
    if not clip: raise YouTubeError('The finished clip was not found.')
    body=_without_links(description).splitlines()
    while body and body[0].strip() in ('','.'):body.pop(0)
    # Replace older generated credit sections instead of duplicating them.
    clean=[];in_credits=False
    for line in body:
        if line.strip().lower().rstrip(':') in ('credits','source credits','sources'):
            in_credits=True;continue
        if in_credits and not line.strip():in_credits=False;continue
        if not in_credits:clean.append(line)
    credits=_credits(clip) or 'User-provided footage'
    text=DESCRIPTION_PREFIX+'\n\nCredits:\n'+credits
    from app.publishing.metadata import scene_description,scene_tags,tag_characters
    scene=_without_links(scene_description(clip,database.get_project(clip['project_id']) or {}))
    text+='\n\nAbout this Short:\n'+scene
    extra='\n'.join(clean).strip()
    # A regenerated preview or retry must not repeat its automatic scene summary.
    extra=re.sub(r'(?is)^About this Short:?\s*\n.*?(?:\n\n|$)','',extra).strip()
    if extra:text+='\n\n'+extra
    # Affiliate links only where the Short's actual content relates to the program.
    from app.business.store import matching_affiliate_block
    project=database.get_project(clip['project_id']) or {}
    affiliate=matching_affiliate_block(' '.join([scene,clip.get('title') or '',project.get('title') or '']))
    if affiliate:text+='\n\n'+affiliate
    if len(text.encode('utf-8'))>5000 or any(c in text for c in '<>'):
        raise YouTubeError('The description including credits must fit within 5,000 bytes without < or >.')
    try:tags=scene_tags(scene)
    except ValueError as exc:raise YouTubeError(str(exc)) from exc
    return {'description':text,'tags':tags,'tag_characters':tag_characters(tags),'scene_description':scene}


def update_uploaded_description(clip_id):
    """Apply the requested description layout to an existing upload, preserving visibility."""
    from app.publishing.copyright import identity
    with _lock:
        upload,_=identity(clip_id)
        preview=description_preview(clip_id,upload['metadata'].get('description',''))
        text,tags=preview['description'],preview['tags']
        saved_tags=upload['metadata'].get('tags',[])
        if text==upload['metadata'].get('description') and len(saved_tags)==len(tags) and set(saved_tags)==set(tags):return get_upload(clip_id)
        headers={'Authorization':'Bearer '+_access_token(upload['channel_id'])}
        response=requests.get(API_URL+'/videos',params={'part':'snippet','id':upload['video_id']},headers=headers,timeout=30)
        _check_response(response)
        items=response.json().get('items',[])
        if len(items)!=1 or items[0].get('id')!=upload['video_id']:
            raise YouTubeError('The uploaded video could not be verified.')
        source=items[0].get('snippet',{})
        if source.get('channelId')!=upload['channel_id'] or not source.get('title') or not source.get('categoryId'):
            raise YouTubeError('The uploaded video metadata or channel could not be verified.')
        mutable=('title','categoryId','tags','defaultLanguage','defaultAudioLanguage')
        snippet={key:source[key] for key in mutable if key in source}
        snippet['description']=text
        snippet['tags']=tags
        response=requests.put(API_URL+'/videos',params={'part':'snippet'},json={'id':upload['video_id'],'snippet':snippet},
            headers=headers,timeout=30)
        _check_response(response)
        confirmed=response.json();confirmed_tags=confirmed.get('snippet',{}).get('tags',[])
        # Google can alphabetize tags; order is not part of keyword identity.
        if (confirmed.get('id')!=upload['video_id'] or confirmed.get('snippet',{}).get('description')!=text
                or len(confirmed_tags)!=len(tags) or set(confirmed_tags)!=set(tags)):
            raise YouTubeError('YouTube did not confirm the updated description. Retry this action.')
        meta={**upload['metadata'],'description':text,'tags':confirmed_tags,'scene_description':preview['scene_description']}
        with database.get_connection() as c:
            c.execute('UPDATE youtube_uploads SET metadata=? WHERE clip_id=?',(json.dumps(meta),clip_id))
        return get_upload(clip_id)


def queue_upload(clip_id, metadata):
    with _lock:
        clip = database.get_clip(clip_id)
        if not database.clip_passed_production_qc(clip):
            raise YouTubeError('Only a finished video that passed production QC can be uploaded. Regenerate this export first.')
        project=database.get_project(clip['project_id'])
        if project and project['mode']=='discovery':
            from app.studio.policy import QualityGate
            from app.studio import store
            result=project['result_data']
            if not QualityGate.evaluate(result.get('checks',{}),result.get('editorial_scores',{}),store.profile().quality_threshold)['passed']:
                raise YouTubeError('Rights, originality, factuality and production checks must all pass before uploading this original story.')
        path = _clip_file(clip)
        if project and project['mode'] != 'discovery':
            from app.publishing import gates
            gates.require_publishable(clip_id)
        connection = connection_status()
        if not connection['connected']:
            raise YouTubeError('Connect your YouTube channel in Settings before uploading.')
        existing = get_upload(clip_id, private=True)
        if existing and existing['status'] == 'UPLOADED':
            existing = verify_remote(clip_id, force=True) and get_upload(clip_id, private=True)   # still there?
        if existing and existing['status'] == 'DELETED':
            conn = database.get_connection()
            with conn:   # start clean: new session, new video ID, fresh metadata
                conn.execute('DELETE FROM youtube_uploads WHERE clip_id = ?', (clip_id,))
            conn.close()
            existing = None
        if existing and (clip_id in _active or existing['status'] in ('UPLOADED', 'QUEUED', 'UPLOADING')):
            return get_upload(clip_id)  # Repeated clicks do not create duplicate videos.
        if existing and existing['channel_id'] != connection['channel_id']:
            raise YouTubeError('Reconnect the original channel to resume this upload.')
        if not existing:
            meta = dict(metadata)
            meta['title'] = (meta.get('title') or clip['title']).strip()
            if not meta['title'] or len(meta['title']) > 100 or any(c in meta['title'] for c in '<>'):
                raise YouTubeError('Use a title of 1–100 characters without < or >.')
            preview=description_preview(clip_id,meta.get('description') or '')
            meta.update(description=preview['description'],tags=preview['tags'],scene_description=preview['scene_description'])
            meta['privacy'] = meta.get('privacy') or connection['default_privacy']
            meta['copyright_gate']=True
            meta['auto_release_after_copyright']=meta['privacy']!='private'
            if meta.get('made_for_kids') is None:
                meta['made_for_kids'] = connection['default_made_for_kids']
            stat = path.stat()
            conn = database.get_connection()
            with conn:
                conn.execute('INSERT INTO youtube_uploads (clip_id,status,metadata,channel_id,file_size,file_mtime,updated_at) VALUES (?,?,?,?,?,?,?)',
                    (clip_id, 'QUEUED', json.dumps(meta), connection['channel_id'], stat.st_size, stat.st_mtime_ns,
                     datetime.now(timezone.utc).isoformat()))
            conn.close()
        else:
            _update(clip_id, status='QUEUED', error=None)
        _active.add(clip_id)
        try:
            _executor.submit(_run_upload, clip_id)
        except Exception:
            _active.discard(clip_id)
            _update(clip_id, status='FAILED', error='Upload could not start. Retry this clip.')
            raise YouTubeError('Upload could not start. Retry this clip.')
        return get_upload(clip_id)


def _session_url(uri):
    parsed = urlparse(uri)
    if parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com' or not parsed.path.startswith('/upload/youtube/'):
        raise YouTubeError('YouTube returned an invalid upload session.')
    return uri


def _complete(clip_id, response):
    _check_response(response)
    video = response.json()
    if not isinstance(video.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', video['id']):
        raise YouTubeError('YouTube did not return a video ID. Retry to check the existing session.')
    _update(clip_id, status='UPLOADED', progress=100, video_id=video['id'],
            actual_privacy=video.get('status', {}).get('privacyStatus'), error=None)


def _offset(response, size):
    _check_response(response)
    if response.status_code != 308:
        raise YouTubeError('Unexpected YouTube upload response. Retry to resume the session.')
    range_header = response.headers.get('Range', '')
    match = re.fullmatch(r'bytes=0-(\d+)', range_header)
    if range_header and not match:
        raise YouTubeError('Invalid upload position returned by YouTube.')
    value = int(match[1]) + 1 if match else 0
    if value < 0 or value > size:
        raise YouTubeError('Invalid upload position returned by YouTube.')
    return value


def _run_upload(clip_id):
    try:
        row = get_upload(clip_id, private=True)
        path = _clip_file(database.get_clip(clip_id))
        stat = path.stat()
        if stat.st_size != row['file_size'] or stat.st_mtime_ns != row['file_mtime']:
            raise YouTubeError('The MP4 changed after upload started. Regenerate it before a new upload.')
        size, meta = stat.st_size, row['metadata']
        _update(clip_id, status='UPLOADING', error=None)
        def headers():
            return {'Authorization': 'Bearer ' + _access_token(row['channel_id'])}
        uri = row.get('session_uri')
        offset = 0
        if uri:
            uri = _session_url(uri)
            response = requests.put(uri, headers={**headers(), 'Content-Length': '0', 'Content-Range': f'bytes */{size}'},
                                    data=b'', timeout=60, allow_redirects=False)
            if response.status_code in (200, 201):
                _complete(clip_id, response)
                return
            if response.status_code in (404, 410):
                # An expired session cannot resume; next explicit retry starts a new one.
                _update(clip_id, session_uri=None, progress=0)
                raise YouTubeError('The upload session expired. Click Retry upload to start again.')
            offset = _offset(response, size)
        else:
            response = requests.post(UPLOAD_URL, params={'uploadType': 'resumable', 'part': 'snippet,status'},
                headers={**headers(), 'X-Upload-Content-Length': str(size), 'X-Upload-Content-Type': 'video/mp4'},
                json={'snippet': {'title': meta['title'], 'description': meta['description'], 'tags':meta.get('tags',[]),'categoryId': meta.get('category_id','22')},
                      'status': {'privacyStatus': 'private' if meta.get('copyright_gate') else meta['privacy'], 'selfDeclaredMadeForKids': meta['made_for_kids']}},
                timeout=60, allow_redirects=False)
            _check_response(response)
            if response.status_code not in (200, 201) or not response.headers.get('Location'):
                raise YouTubeError('YouTube did not start an upload session. Retry this clip.')
            uri = _session_url(response.headers['Location'])
            _update(clip_id, session_uri=uri)
        with path.open('rb') as stream:
            while offset < size:
                stream.seek(offset)
                chunk = stream.read(CHUNK_SIZE)
                end = offset + len(chunk) - 1
                response = requests.put(uri, headers={**headers(), 'Content-Type': 'video/mp4',
                    'Content-Length': str(len(chunk)), 'Content-Range': f'bytes {offset}-{end}/{size}'},
                    data=chunk, timeout=120, allow_redirects=False)
                if response.status_code in (200, 201):
                    _complete(clip_id, response)
                    return
                next_offset = _offset(response, size)
                if next_offset <= offset or next_offset > end + 1:
                    raise YouTubeError('Upload made no progress. Retry to resume the existing session.')
                offset = next_offset
                _update(clip_id, progress=min(99, int(offset * 100 / size)))
        raise YouTubeError('Waiting for YouTube upload confirmation. Retry to check the existing session.')
    except requests.RequestException:
        _update(clip_id, status='FAILED', error='Connection interrupted. Click Retry upload to resume without creating a duplicate.')
    except YouTubeError as error:
        _update(clip_id, status='FAILED', error=str(error))
    except Exception:
        _update(clip_id, status='FAILED', error='Upload failed. Check the connection and retry the existing upload.')
    finally:
        with _lock:
            _active.discard(clip_id)


def recover_interrupted():
    conn = database.get_connection()
    with conn:
        conn.execute("UPDATE youtube_uploads SET status='FAILED', error='Upload interrupted by restart. Retry to resume the existing session.' WHERE status IN ('QUEUED','UPLOADING')")
    conn.close()

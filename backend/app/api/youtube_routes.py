from html import escape
from ipaddress import ip_address
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import requests

from app.publishing import youtube
from app.publishing import copyright

def local_access(request: Request):
    try:
        local = bool(request.client and ip_address(request.client.host).is_loopback)
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, 'YouTube connections and uploads are only available on this computer.')


router = APIRouter(prefix='/youtube', tags=['YouTube'], dependencies=[Depends(local_access)])


def local_mutation(request: Request):
    # Google tokens remain on the backend; other websites cannot trigger uploads.
    origin = request.headers.get('origin')
    allowed = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (8000, 5173)}
    if origin and origin not in allowed:
        raise HTTPException(403, 'YouTube actions are only available from the local app.')
    if 'application/json' not in request.headers.get('content-type', ''):
        raise HTTPException(415, 'Send application/json.')


def invoke(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except youtube.YouTubeError as error:
        raise HTTPException(400, str(error)) from None
    except requests.RequestException:
        raise HTTPException(502, 'Could not reach Google. Check your connection and retry.') from None


class ConnectionRequest(BaseModel):
    client_document: dict[str, Any] | None = None
    privacy: Literal['private', 'unlisted', 'public'] = 'public'
    made_for_kids: bool = False


class UploadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    description: str = Field(default='', max_length=5000)
    privacy: Literal['private', 'unlisted', 'public'] | None = None
    made_for_kids: bool | None = None


class CopyrightReview(BaseModel):
    verdict: Literal['passed','blocked']
    note: str = Field(min_length=10,max_length=1000)


@router.get('/copyright/{clip_id}')
def copyright_status(clip_id:str):
    return copyright.read(clip_id)


@router.post('/copyright/{clip_id}/check',dependencies=[Depends(local_mutation)])
def copyright_check(clip_id:str): return invoke(copyright.refresh,clip_id,force=True)


@router.post('/copyright/{clip_id}/review',dependencies=[Depends(local_mutation)])
def copyright_review(clip_id:str,payload:CopyrightReview):
    result=invoke(copyright.record_review,clip_id,payload.verdict,payload.note)
    if payload.verdict=='passed':
        invoke(copyright.auto_release,clip_id,force=True)
        result=copyright.read(clip_id)
    return result


@router.post('/copyright/{clip_id}/publish',dependencies=[Depends(local_mutation)])
def copyright_publish(clip_id:str): return invoke(copyright.release,clip_id)


@router.get('/connection')
def connection():
    return youtube.connection_status()


@router.post('/configure', dependencies=[Depends(local_mutation)])
def configure(payload: ConnectionRequest):
    return invoke(youtube.configure, payload.client_document, payload.privacy, payload.made_for_kids)


@router.post('/connect', dependencies=[Depends(local_mutation)])
def connect():
    return {'authorization_url': invoke(youtube.start_authorization, 'http://127.0.0.1:8000/api/youtube/callback')}


@router.get('/callback', response_class=HTMLResponse)
def callback(state: str = '', code: str | None = None, error: str | None = None):
    try:
        result = youtube.finish_authorization(state, code, denied=bool(error))
        message = f"Connected to {result['channel_title']}. Close this tab and return to the app. Uploads will happen directly inside the app."
        status = 200
    except youtube.YouTubeError as exc:
        message, status = str(exc), 400
    except requests.RequestException:
        message, status = 'Could not reach Google. Return to Settings and connect again.', 502
    return HTMLResponse('<!doctype html><html><head><meta name="referrer" content="no-referrer"><title>YouTube connection</title></head>'
                        '<body style="font:16px system-ui;max-width:600px;margin:80px auto;padding:24px">'
                        '<h1>YouTube connection</h1><p>' + escape(message) + '</p></body></html>', status_code=status,
                        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@router.post('/disconnect', dependencies=[Depends(local_mutation)])
def disconnect():
    return invoke(youtube.disconnect)


@router.get('/uploads/{clip_id}')
def upload_status(clip_id: str):
    # Opening an uploaded clip also checks that it still exists on YouTube (throttled).
    return youtube.verify_remote(clip_id) if youtube.get_upload(clip_id) else None


@router.post('/uploads/{clip_id}/reupload', dependencies=[Depends(local_mutation)])
def reupload(clip_id: str):
    """Clear the upload record so the clip can be uploaded again as a new video."""
    invoke(youtube.reset_upload, clip_id)
    return {'clip_id': clip_id, 'status': 'READY_TO_UPLOAD'}


@router.post('/uploads/{clip_id}/verify', dependencies=[Depends(local_mutation)])
def verify_upload(clip_id: str):
    return invoke(youtube.verify_remote, clip_id, True)


@router.post('/uploads/{clip_id}/preview', dependencies=[Depends(local_mutation)])
def upload_preview(clip_id:str,payload:UploadRequest):
    return invoke(youtube.description_preview,clip_id,payload.description)


@router.post('/uploads/{clip_id}/description', dependencies=[Depends(local_mutation)])
def update_description(clip_id:str):
    return invoke(youtube.update_uploaded_description,clip_id)


@router.post('/uploads/{clip_id}', dependencies=[Depends(local_mutation)])
def upload(clip_id: str, payload: UploadRequest):
    return invoke(youtube.queue_upload, clip_id, payload.model_dump())

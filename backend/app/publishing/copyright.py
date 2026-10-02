"""Private staging and a copyright-review gate bound to the actual MP4/video/channel.

YouTube's ordinary API does not expose the Studio Content ID check verdict.
Processing success is therefore never interpreted as copyright clearance.
"""
import hashlib
import json
import time
import requests
from app.core.database import get_connection,get_clip,clip_passed_production_qc
from app.publishing import youtube


def read(clip_id):
    with get_connection() as c: row=c.execute('SELECT data FROM copyright_checks WHERE clip_id=?',(clip_id,)).fetchone()
    return json.loads(row['data']) if row else {'state':'NOT_CHECKED','message':'Upload privately before copyright review.'}


def save(clip_id,data):
    with get_connection() as c:c.execute('INSERT INTO copyright_checks VALUES(?,?) ON CONFLICT(clip_id) DO UPDATE SET data=excluded.data',(clip_id,json.dumps(data)))
    return data


def identity(clip_id):
    upload=youtube.get_upload(clip_id,private=True)
    if not upload or upload['status']!='UPLOADED' or not upload.get('video_id'):
        raise youtube.YouTubeError('Complete the private upload before copyright review.')
    clip=get_clip(clip_id)
    if not clip_passed_production_qc(clip): raise youtube.YouTubeError('Production QC must pass before copyright review.')
    path=youtube._clip_file(clip)
    if path.stat().st_size!=upload['file_size'] or path.stat().st_mtime_ns!=upload['file_mtime']:
        raise youtube.YouTubeError('The final MP4 changed after upload; this copyright verdict cannot be reused.')
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return upload,{'video_id':upload['video_id'],'channel_id':upload['channel_id'],'sha256':digest}


def refresh(clip_id,force=False):
    upload,binding=identity(clip_id);previous=read(clip_id)
    same=all(previous.get(k)==v for k,v in binding.items())
    if same and not force and time.time()-previous.get('checked_at',0)<120:return previous
    token=youtube._access_token(upload['channel_id'])
    response=requests.get(youtube.API_URL+'/videos',params={'part':'status,processingDetails','id':upload['video_id']},
        headers={'Authorization':'Bearer '+token},timeout=30)
    youtube._check_response(response)
    videos=response.json().get('items',[])
    if len(videos)!=1 or videos[0].get('id')!=upload['video_id']:raise youtube.YouTubeError('The uploaded video could not be verified.')
    video=videos[0];status=video.get('status',{});processing=video.get('processingDetails',{}).get('processingStatus')
    state='PROCESSING';message='YouTube is processing the private upload.'
    if status.get('uploadStatus') in ('rejected','failed','deleted') or status.get('rejectionReason') or processing in ('failed','terminated'):
        state='BLOCKED';message='YouTube rejected the video: '+str(status.get('rejectionReason') or processing or status.get('uploadStatus'))
    elif processing=='succeeded' or status.get('uploadStatus')=='processed':
        state=previous.get('state') if same and previous.get('state') in ('PASSED','BLOCKED','PUBLISHED','PUBLISHING') else 'PENDING_REVIEW'
        if state=='PUBLISHING' and time.time()-previous.get('reserved_at',0)>120:state='PASSED'
        message='YouTube processing passed. Confirm the actual copyright check result; processing is not copyright clearance.'
    data={**(previous if same else {}),**binding,'state':state,'message':message,'checked_at':time.time(),
          'youtube_status':status,'processing_status':processing,'copyright_verdict_source':'owner_review' if same and previous.get('reviewed_at') else None}
    return save(clip_id,data)


def record_review(clip_id,verdict,note):
    if verdict not in ('passed','blocked') or not isinstance(note,str) or not 10<=len(note.strip())<=1000:
        raise youtube.YouTubeError('Record the actual copyright review result and an evidence note.')
    current=refresh(clip_id,force=True)
    if verdict=='passed' and current['state'] not in ('PENDING_REVIEW','PASSED'):
        raise youtube.YouTubeError('A processing, rejected or blocked video cannot be cleared for publication.')
    current.update(state='PASSED' if verdict=='passed' else 'BLOCKED',review_note=note.strip(),reviewed_at=time.time(),
        copyright_verdict_source='owner_review',message='Copyright check confirmed clear by channel owner.' if verdict=='passed' else 'Copyright review reported an unresolved issue.')
    return save(clip_id,current)


def release(clip_id,autonomous=False):
    with youtube._lock:
        current=refresh(clip_id,force=True)
        if current['state']=='PUBLISHED': return youtube.get_upload(clip_id)
        if current['state']!='PASSED': raise youtube.YouTubeError('Publication waits for a confirmed, clear copyright check.')
        upload,binding=identity(clip_id)
        if any(current.get(k)!=v for k,v in binding.items()):raise youtube.YouTubeError('Copyright review is for a different MP4 or upload.')
        target=upload['metadata']['privacy']
        # Reserve the public-release spacing across separate worker processes.
        # Private uploads for checking do not consume this publication slot.
        with get_connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT data FROM copyright_checks WHERE clip_id=?',(clip_id,)).fetchone()
            authoritative=json.loads(row['data']) if row else {}
            if authoritative.get('state')!='PASSED' or any(authoritative.get(k)!=v for k,v in binding.items()):
                raise youtube.YouTubeError('This video is already releasing or its clearance changed.')
            if autonomous:
                others=[json.loads(row['data']) for row in c.execute('SELECT data FROM copyright_checks WHERE clip_id<>?',(clip_id,))]
                if any(time.time()-other.get('published_at',0)<6*3600 or
                    (other.get('state')=='PUBLISHING' and time.time()-other.get('reserved_at',0)<120) for other in others):
                    raise youtube.YouTubeError('Autonomous publication waits for the six-hour spacing window.')
            current.update(state='PUBLISHING',reserved_at=time.time())
            c.execute('UPDATE copyright_checks SET data=? WHERE clip_id=?',(json.dumps(current),clip_id))
        if target!='private':
            mutable=('embeddable','license','publicStatsViewable','selfDeclaredMadeForKids','containsSyntheticMedia')
            status={k:v for k,v in current['youtube_status'].items() if k in mutable}
            status['privacyStatus']=target
            token=youtube._access_token(upload['channel_id'])
            try:
                response=requests.put(youtube.API_URL+'/videos',params={'part':'status'},json={'id':upload['video_id'],'status':status},
                    headers={'Authorization':'Bearer '+token},timeout=30)
                youtube._check_response(response)
                if response.json().get('status',{}).get('privacyStatus')!=target:
                    raise youtube.YouTubeError('YouTube kept the video private. Check your API project/channel publication permissions.')
            except Exception:
                current['state']='PASSED';save(clip_id,current)
                raise
        youtube._update(clip_id,actual_privacy=target)
        current.update(state='PUBLISHED',published_at=time.time(),message='Released after confirmed copyright review.')
        save(clip_id,current)
        return youtube.get_upload(clip_id)

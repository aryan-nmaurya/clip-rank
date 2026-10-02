"""SQLite control plane for local use. State survives application/worker restarts."""
import hashlib
import json
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.core.database import get_connection
from app.studio.models import ChannelProfile, now, LEGACY_PILLARS,CHANNEL_NAME,CHANNEL_PROMISE,PILLARS


def init_studio():
    with get_connection() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS studio_config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS opportunities (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS daily_plans (day TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS studio_tasks (
          id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, project_id TEXT NOT NULL,
          day TEXT, format TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'CREATED', stage TEXT NOT NULL DEFAULT 'CREATED',
          checkpoint TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
          lease_owner TEXT, lease_until TEXT, publish_at TEXT, clip_id TEXT, video_id TEXT,
          error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS studio_task_queue ON studio_tasks(status, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS studio_daily_candidate ON studio_tasks(day, opportunity_id) WHERE day IS NOT NULL;
        CREATE TABLE IF NOT EXISTS studio_workers (id TEXT PRIMARY KEY, heartbeat TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS performance (video_id TEXT PRIMARY KEY, data TEXT NOT NULL, collected_at TEXT NOT NULL);
        ''')
        c.execute('INSERT OR IGNORE INTO studio_config VALUES(1,?)', (ChannelProfile().model_dump_json(),))
        saved=json.loads(c.execute('SELECT data FROM studio_config WHERE id=1').fetchone()['data'])
        if saved.get('name')=='AI & Future Tech' and set(saved.get('pillars',[]))==set(LEGACY_PILLARS):
            saved.update(name=CHANNEL_NAME,positioning=CHANNEL_PROMISE,pillars=list(PILLARS))
            saved['voice_profile']={'Tech Curious':'Curious','Tech Energetic':'Energetic'}.get(saved.get('voice_profile'),saved.get('voice_profile','Energetic'))
            c.execute('UPDATE studio_config SET data=? WHERE id=1',(ChannelProfile.model_validate(saved).model_dump_json(),))


def profile():
    with get_connection() as c:
        row = c.execute('SELECT data FROM studio_config WHERE id=1').fetchone()
    return ChannelProfile.model_validate_json(row['data'])


def save_profile(value):
    with get_connection() as c:
        c.execute('UPDATE studio_config SET data=? WHERE id=1', (value.model_dump_json(),))
    return value


def put_opportunity(value):
    identity=value['source_url']
    if value.get('moment'): identity+=':'+json.dumps({k:value['moment'][k] for k in ('start','end')},sort_keys=True)
    identifier = hashlib.sha256(identity.encode()).hexdigest()[:24]
    value = {**value, 'id': identifier}
    with get_connection() as c:
        c.execute('INSERT INTO opportunities VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                  (identifier, json.dumps(value), now()))
    return value


def opportunity(identifier):
    with get_connection() as c:
        row = c.execute('SELECT data FROM opportunities WHERE id=?', (identifier,)).fetchone()
    return json.loads(row['data']) if row else None


def opportunities():
    with get_connection() as c:
        rows = c.execute('SELECT data FROM opportunities ORDER BY created_at DESC LIMIT 100').fetchall()
    return [json.loads(r['data']) for r in rows]


def tasks():
    with get_connection() as c:
        rows = c.execute('SELECT * FROM studio_tasks ORDER BY created_at DESC LIMIT 200').fetchall()
    return [decode(r) for r in rows]


def decode(row):
    value = dict(row)
    value['checkpoint'] = json.loads(value['checkpoint'])
    return value


def task(identifier):
    with get_connection() as c:
        row = c.execute('SELECT * FROM studio_tasks WHERE id=?', (identifier,)).fetchone()
    return decode(row) if row else None


def enqueue(opportunity_id, format='auto', day=None, publish_at=None, connection=None):
    identifier = 'studio_' + uuid.uuid4().hex
    project_id = 'proj_' + identifier
    timestamp = now()
    with nullcontext(connection) if connection is not None else get_connection() as c:
        if connection is None: c.execute('BEGIN IMMEDIATE')
        if day:
            existing = c.execute('SELECT id FROM studio_tasks WHERE day=? AND opportunity_id=?', (day, opportunity_id)).fetchone()
            if existing:
                row=c.execute('SELECT * FROM studio_tasks WHERE id=?',(existing['id'],)).fetchone()
                return decode(row)
        item = opportunity(opportunity_id)
        if not item: raise ValueError('Opportunity not found.')
        if format=='auto':
            from app.studio.director import ContentDirector
            format=ContentDirector.choose_format(item,opportunities())
        c.execute('INSERT INTO studio_tasks(id,opportunity_id,project_id,day,format,publish_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                  (identifier, opportunity_id, project_id, day, format, publish_at, timestamp, timestamp))
        c.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)',
                  (project_id,'discovery',item['topic'],'CREATED',json.dumps({'studio_task_id':identifier,'autopilot':bool(day),'format':format}),'{}',timestamp,timestamp))
        c.execute('INSERT INTO jobs(id,project_id,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                  (identifier,project_id,'QUEUED',timestamp,timestamp))
        value=decode(c.execute('SELECT * FROM studio_tasks WHERE id=?',(identifier,)).fetchone())
    return value


def update(identifier, **fields):
    allowed = {'status', 'stage', 'checkpoint', 'publish_at', 'clip_id', 'video_id', 'error', 'lease_owner', 'lease_until'}
    if set(fields) - allowed:
        raise ValueError('Unsupported studio state mutation.')
    if 'checkpoint' in fields:
        fields['checkpoint'] = json.dumps(fields['checkpoint'])
    fields['updated_at'] = now()
    with get_connection() as c:
        c.execute('UPDATE studio_tasks SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', (*fields.values(), identifier))


def claim(worker_id, seconds=120, allow_daily=True):
    timestamp = now()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    with get_connection() as c:
        c.execute('BEGIN IMMEDIATE')
        expired=c.execute("SELECT id,project_id FROM studio_tasks WHERE status='PROCESSING' AND lease_until<? AND attempts>=3",(timestamp,)).fetchall()
        for failed in expired:
            c.execute("UPDATE studio_tasks SET status='FAILED',stage='FAILED',error='Worker recovery retry limit reached.',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(timestamp,failed['id']))
            c.execute("UPDATE jobs SET status='FAILED',current_stage='Failed',error_message='Worker recovery retry limit reached.',updated_at=? WHERE id=?",(timestamp,failed['id']))
            c.execute("UPDATE projects SET status='FAILED',updated_at=? WHERE id=?",(timestamp,failed['project_id']))
        row = c.execute("SELECT * FROM studio_tasks WHERE (status='CREATED' OR (status='PROCESSING' AND lease_until<?)) AND attempts<3 AND (? OR day IS NULL) ORDER BY created_at LIMIT 1", (timestamp,allow_daily)).fetchone()
        if not row:
            return None
        c.execute("UPDATE studio_tasks SET status='PROCESSING',lease_owner=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",
                  (worker_id, expires, timestamp, row['id']))
        value = decode(row)
        value.update(status='PROCESSING', lease_owner=worker_id, lease_until=expires, attempts=row['attempts']+1)
    return value


def reserve_upload(identifier, channel, staging=False):
    """Reserve a daily slot and spacing in one transaction across local workers."""
    timestamp=now(); instant=datetime.fromisoformat(timestamp)
    day_start=instant.astimezone(ZoneInfo(channel.timezone)).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat()
    with get_connection() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM studio_tasks WHERE id=?',(identifier,)).fetchone()
        if not row or row['status'] not in ('PUBLISH_READY','WAITING_TO_PUBLISH'): return False
        value=decode(row);checkpoint=value['checkpoint']
        last=checkpoint.get('last_upload_attempt')
        if last and instant-datetime.fromisoformat(last)<timedelta(minutes=15): return False
        if staging:
            checkpoint['last_upload_attempt']=timestamp
            c.execute("UPDATE studio_tasks SET status='UPLOADING',stage='COPYRIGHT_UPLOAD',checkpoint=?,updated_at=? WHERE id=?",(json.dumps(checkpoint),timestamp,identifier))
            return True
        uploads=c.execute("SELECT clip_id,updated_at FROM youtube_uploads WHERE status IN ('UPLOADED','QUEUED','UPLOADING')").fetchall()
        reservations=c.execute("SELECT clip_id,checkpoint FROM studio_tasks WHERE status='UPLOADING'").fetchall()
        observations=[(r['clip_id'],r['updated_at']) for r in uploads]
        observations.extend((r['clip_id'],json.loads(r['checkpoint']).get('last_upload_attempt')) for r in reservations)
        observations=[(clip,t) for clip,t in observations if t]
        if len({clip for clip,t in observations if t>=day_start})>=channel.publication_limit: return False
        if any(instant-datetime.fromisoformat(t)<timedelta(hours=6) for _,t in observations): return False
        checkpoint['last_upload_attempt']=timestamp
        c.execute("UPDATE studio_tasks SET status='UPLOADING',stage='UPLOADING',checkpoint=?,updated_at=? WHERE id=?",(json.dumps(checkpoint),timestamp,identifier))
    return True


def heartbeat(worker_id, data, task_id=None):
    timestamp = now()
    with get_connection() as c:
        c.execute('INSERT INTO studio_workers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,data=excluded.data',
                  (worker_id, timestamp, json.dumps(data)))
        if task_id:
            expires = (datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat()
            c.execute('UPDATE studio_tasks SET lease_until=? WHERE id=? AND lease_owner=? AND status=\'PROCESSING\'', (expires, task_id, worker_id))


def worker_status():
    with get_connection() as c:
        row = c.execute('SELECT * FROM studio_workers ORDER BY heartbeat DESC LIMIT 1').fetchone()
    if not row:
        return {'online': False, 'last_seen': None}
    age = (datetime.now(timezone.utc)-datetime.fromisoformat(row['heartbeat'])).total_seconds()
    return {'online': age < 90, 'last_seen': row['heartbeat'], **json.loads(row['data'])}

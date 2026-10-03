import logging
import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from app.core.config import (
    DB_PATH,
    DEFAULT_AI_PROVIDER,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_LOCAL_ENDPOINT,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_VOICE,
    DEFAULT_LANGUAGE,
    HW_ACCEL,
    TEMP_RETENTION_HOURS,
)

SECRET_FIELDS = ('gemini_api_key', 'gemini_api_key_2', 'openai_api_key', 'groq_api_key', 'nvidia_api_key')


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    conn = get_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ai_provider TEXT NOT NULL DEFAULT 'auto',
                gemini_api_key TEXT,
                gemini_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite',
                openai_api_key TEXT,
                openai_model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                local_endpoint TEXT NOT NULL DEFAULT 'http://localhost:11434',
                local_model TEXT NOT NULL DEFAULT 'qwen3-vl:4b',
                default_voice TEXT NOT NULL DEFAULT 'en-US-GuyNeural',
                language TEXT NOT NULL DEFAULT 'en',
                hardware_accel TEXT NOT NULL DEFAULT 'cpu',
                temp_retention_hours INTEGER NOT NULL DEFAULT 12,
                updated_at TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CREATED',
                input_data TEXT NOT NULL DEFAULT '{}',
                result_data TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'QUEUED',
                progress INTEGER NOT NULL DEFAULT 0,
                current_stage TEXT NOT NULL DEFAULT 'Queued',
                error_message TEXT,
                detailed_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT,
                duration REAL NOT NULL DEFAULT 0.0,
                rank INTEGER,
                viral_score INTEGER,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'PROCESSING',
                preview_path TEXT,
                video_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_uploads (
                clip_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_mtime INTEGER NOT NULL,
                session_uri TEXT,
                video_id TEXT,
                actual_privacy TEXT,
                error TEXT,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute('CREATE TABLE IF NOT EXISTS copyright_checks (clip_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS movie_music (id TEXT PRIMARY KEY, data TEXT NOT NULL)')

        row = conn.execute("SELECT id FROM settings WHERE id = 1").fetchone()
        if not row:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT INTO settings (
                    id, ai_provider, gemini_model, openai_model, local_endpoint,
                    local_model, default_voice, language, hardware_accel,
                    temp_retention_hours, updated_at
                ) VALUES (
                    1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                DEFAULT_AI_PROVIDER,
                DEFAULT_GEMINI_MODEL,
                DEFAULT_OPENAI_MODEL,
                DEFAULT_LOCAL_ENDPOINT,
                DEFAULT_LOCAL_MODEL,
                DEFAULT_VOICE,
                DEFAULT_LANGUAGE,
                HW_ACCEL,
                TEMP_RETENTION_HOURS,
                now,
            ))
        # Existing installations receive optional, disabled-by-default branding.
        columns={r['name'] for r in conn.execute('PRAGMA table_info(settings)')}
        for name,definition in (('watermark_enabled','INTEGER NOT NULL DEFAULT 0'),
                                ('watermark_text',"TEXT NOT NULL DEFAULT ''")):
            if name not in columns:conn.execute(f'ALTER TABLE settings ADD COLUMN {name} {definition}')
        for extra in ('groq_api_key', 'groq_model', 'nvidia_api_key', 'nvidia_model'):
            if extra not in columns:conn.execute(f'ALTER TABLE settings ADD COLUMN {extra} TEXT')
        if 'quality_control' not in columns:conn.execute('ALTER TABLE settings ADD COLUMN quality_control INTEGER NOT NULL DEFAULT 0')
        if 'enforce_publish_gates' not in columns:conn.execute('ALTER TABLE settings ADD COLUMN enforce_publish_gates INTEGER NOT NULL DEFAULT 0')
        if 'gemini_fallback_models' not in columns:conn.execute("ALTER TABLE settings ADD COLUMN gemini_fallback_models TEXT")
        if 'gemini_api_key_2' not in columns:conn.execute('ALTER TABLE settings ADD COLUMN gemini_api_key_2 TEXT')
        job_columns={r['name'] for r in conn.execute('PRAGMA table_info(jobs)')}
        if 'failure' not in job_columns:conn.execute('ALTER TABLE jobs ADD COLUMN failure TEXT')
        # Migrate prior system-voice choices to their closest neural voice.
        from app.tts.voice_engine import LEGACY_VOICES
        for legacy, neural in LEGACY_VOICES.items():
            conn.execute("UPDATE settings SET default_voice = ? WHERE default_voice = ?", (neural, legacy))
        conn.execute("UPDATE settings SET local_model = ? WHERE local_model = 'qwen2.5:latest'", (DEFAULT_LOCAL_MODEL,))
        conn.execute("UPDATE settings SET gemini_model = ? WHERE gemini_model = 'gemini-2.5-flash'", (DEFAULT_GEMINI_MODEL,))
    conn.close()
    _init_business_tables()

def _init_business_tables():
    from app.business.store import init_business
    init_business()


def get_settings() -> Dict[str, Any]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    result=dict(row) if row else {}
    result['watermark_enabled']=bool(result.get('watermark_enabled',False))
    result['enforce_publish_gates']=bool(result.get('enforce_publish_gates',False))
    result['quality_control']=bool(result.get('quality_control',False))
    for name in SECRET_FIELDS:
        if result.get(name)==f'keychain:{name}':
            from app.core.secrets import SecretVault
            result[name]=SecretVault.get(name)
    return result

def update_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_connection()
    if 'watermark_enabled' in updates or 'watermark_text' in updates:
        from app.media.watermark import Watermark
        existing=conn.execute('SELECT watermark_enabled,watermark_text FROM settings WHERE id=1').fetchone()
        try:
            enabled,text=Watermark.validate(updates.get('watermark_enabled',existing['watermark_enabled']),
                                            updates.get('watermark_text',existing['watermark_text']))
        except ValueError:
            conn.close();raise
        updates={**updates,'watermark_enabled':enabled,'watermark_text':text}
    if 'quality_control' in updates:
        from app.core import qc as _qc
        _qc.reset_cache()
    allowed_fields = [
        "ai_provider", "gemini_api_key", "gemini_api_key_2", "gemini_model", "gemini_fallback_models", "openai_api_key", "groq_api_key", "groq_model", "nvidia_api_key", "nvidia_model",
        "openai_model", "local_endpoint", "local_model", "default_voice",
        "language", "hardware_accel", "temp_retention_hours", "watermark_enabled", "watermark_text", "enforce_publish_gates", "quality_control"
    ]
    fields = []
    values = []
    for k, v in updates.items():
        if k in allowed_fields:
            if k in SECRET_FIELDS and v:
                from app.core.secrets import SecretVault
                SecretVault.set(k,v)
                v=f'keychain:{k}'
            fields.append(f"{k} = ?")
            values.append(v)

    if fields:
        now = datetime.now(timezone.utc).isoformat()
        fields.append("updated_at = ?")
        values.append(now)
        values.append(1)
        query = f"UPDATE settings SET {', '.join(fields)} WHERE id = ?"
        with conn:
            conn.execute(query, tuple(values))
    conn.close()
    return get_settings()

def create_project(project_id: str, mode: str, title: str, input_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    inp_str = json.dumps(input_data or {})
    with conn:
        conn.execute("""
            INSERT OR REPLACE INTO projects (id, mode, title, status, input_data, result_data, created_at, updated_at)
            VALUES (?, ?, ?, 'CREATED', ?, '{}', ?, ?)
        """, (project_id, mode, title, inp_str, now, now))
    conn.close()
    return get_project(project_id)

def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        conn.close()
        return None
    p = dict(row)
    try:
        p["input_data"] = json.loads(p.get("input_data") or "{}")
    except Exception:
        p["input_data"] = {}
    try:
        p["result_data"] = json.loads(p.get("result_data") or "{}")
    except Exception:
        p["result_data"] = {}

    clips_rows = conn.execute("SELECT * FROM clips WHERE project_id = ? AND job_id = (SELECT id FROM jobs WHERE project_id = clips.project_id ORDER BY created_at DESC LIMIT 1) ORDER BY rank ASC, viral_score DESC", (project_id,)).fetchall()
    p["clips"] = [dict(c) for c in clips_rows]

    job_row = conn.execute("SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
    p["job"] = _job_dict(job_row)
    conn.close()
    return p

def list_projects(mode: Optional[str] = None, search: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    clauses = []
    params = []

    if mode and mode.lower() == 'autopilot':
        clauses.append("json_extract(input_data, '$.autopilot') = 1")
    elif mode and mode.lower() != "all":
        clauses.append("mode = ?")
        params.append(mode.lower())
    if search:
        clauses.append("title LIKE ?")
        params.append(f"%{search}%")
    if status and status.lower() != "all":
        clauses.append("status = ?")
        params.append(status.upper())

    query = "SELECT * FROM projects"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC"

    rows = conn.execute(query, tuple(params)).fetchall()
    results = []
    for r in rows:
        p = dict(r)
        try:
            p["input_data"] = json.loads(p.get("input_data") or "{}")
        except Exception:
            p["input_data"] = {}
        try:
            p["result_data"] = json.loads(p.get("result_data") or "{}")
        except Exception:
            p["result_data"] = {}
        clips_rows = conn.execute("SELECT * FROM clips WHERE project_id = ? AND job_id = (SELECT id FROM jobs WHERE project_id = clips.project_id ORDER BY created_at DESC LIMIT 1) ORDER BY rank ASC, viral_score DESC", (p["id"],)).fetchall()
        p["clips"] = [dict(c) for c in clips_rows]
        job_row = conn.execute("SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (p["id"],)).fetchone()
        p["job"] = _job_dict(job_row)
        results.append(p)

    conn.close()
    return results

def update_project(
    project_id: str,
    status: Optional[str] = None,
    title: Optional[str] = None,
    result_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    fields = ["updated_at = ?"]
    values = [now]

    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if title is not None:
        fields.append("title = ?")
        values.append(title)
    if result_data is not None:
        fields.append("result_data = ?")
        values.append(json.dumps(result_data))

    values.append(project_id)
    query = f"UPDATE projects SET {', '.join(fields)} WHERE id = ?"
    with conn:
        conn.execute(query, tuple(values))
    conn.close()
    return get_project(project_id)

def delete_project(project_id: str) -> bool:
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM clips WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM jobs WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.close()
    return True

def create_job(job_id: str, project_id: str) -> Dict[str, Any]:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
            INSERT OR REPLACE INTO jobs (id, project_id, status, progress, current_stage, created_at, updated_at)
            VALUES (?, ?, 'QUEUED', 0, 'Queued', ?, ?)
        """, (job_id, project_id, now, now))
    conn.close()
    return get_job(job_id)

def _job_dict(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    job = dict(row)
    from app.core.failures import parse
    job['failure'] = parse(job.get('failure'))
    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return _job_dict(row)

def get_job_by_project(project_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
    conn.close()
    return _job_dict(row)

def update_job(
    job_id: str,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    current_stage: Optional[str] = None,
    error_message: Optional[str] = None,
    detailed_error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    fields = ["updated_at = ?"]
    values = [now]

    if status is not None:
        from app.core.states import can_transition
        current = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if current and not can_transition(current["status"], status):
            # A terminal job stays terminal: a late progress write must not resurrect it.
            logging.getLogger("ai_shorts.jobs").warning("Ignored illegal transition %s -> %s for %s", current["status"], status, job_id)
            conn.close()
            return get_job(job_id)
        fields.append("status = ?")
        values.append(status)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    if current_stage is not None:
        fields.append("current_stage = ?")
        values.append(current_stage)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if detailed_error is not None:
        fields.append("detailed_error = ?")
        values.append(detailed_error)

    values.append(job_id)
    query = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?"
    with conn:
        conn.execute(query, tuple(values))
    conn.close()
    return get_job(job_id)

def record_job_failure(job_id: str, exc: BaseException) -> Optional[Dict[str, Any]]:
    """Store WHAT/WHY/RETRY/NEXT once, from the stage the job was actually in.

    Pipelines record before re-raising and the job engine records again as a safety
    net; the second call is a no-op so the original stage is never overwritten.
    """
    from app.core.failures import failure_json, user_message
    job = get_job(job_id)
    if not job or (job['status'] == 'FAILED' and job.get('failure')):
        return job
    summary = failure_json(exc, job['status'] if job['status'] != 'QUEUED' else 'STARTING')
    summary['stage_detail'] = job.get('current_stage')
    conn = get_connection()
    with conn:
        conn.execute("UPDATE jobs SET status='FAILED', current_stage='Failed', error_message=?, detailed_error=?, failure=?, updated_at=? WHERE id=?",
                     (user_message(summary), summary['traceback'], json.dumps(summary),
                      datetime.now(timezone.utc).isoformat(), job_id))
    conn.close()
    return get_job(job_id)


def create_or_update_clip(
    clip_id: str,
    project_id: str,
    job_id: str,
    title: str,
    subtitle: Optional[str] = None,
    duration: float = 0.0,
    rank: Optional[int] = None,
    viral_score: Optional[int] = None,
    reason: Optional[str] = None,
    status: str = "PROCESSING",
    preview_path: Optional[str] = None,
    video_path: Optional[str] = None,
) -> Dict[str, Any]:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
            INSERT INTO clips (id, project_id, job_id, title, subtitle, duration, rank, viral_score, reason, status, preview_path, video_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                subtitle = excluded.subtitle,
                duration = excluded.duration,
                rank = excluded.rank,
                viral_score = excluded.viral_score,
                reason = excluded.reason,
                status = excluded.status,
                preview_path = excluded.preview_path,
                video_path = excluded.video_path
        """, (clip_id, project_id, job_id, title, subtitle, duration, rank, viral_score, reason, status, preview_path, video_path, now))
    conn.close()
    return get_clip(clip_id)

def get_clip(clip_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def clip_passed_production_qc(clip) -> bool:
    if not clip or clip.get('status') != 'READY':
        return False
    project = get_project(clip['project_id'])
    result = project.get('result_data',{}) if project else {}
    if not result.get('production_qc_passed'):
        return False
    records = result.get('variants') or result.get('moments',[])
    return any(row.get('clip_id')==clip['id'] and row.get('qc',{}).get('passed') is True
               and row.get('final_review',{}).get('passed') is True for row in records)

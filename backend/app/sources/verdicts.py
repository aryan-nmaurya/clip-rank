"""Remember which sources were *judged* unsuitable for a topic, so retries don't pay to re-judge them.

Only genuine verdicts are stored (the vision model looked and said no). Failures to obtain a
verdict - quota, network, a model hiccup - are never recorded, so a bad day can't blacklist good footage.
"""
import re
import time
from typing import Iterable, Optional

from app.core.database import get_connection

MAX_AGE_DAYS = 30


def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS source_verdicts (
        key TEXT NOT NULL, topic TEXT NOT NULL, reason TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (key, topic))""")


def topic_key(topic: str) -> str:
    return " ".join(re.findall(r"\w+", (topic or "").lower()))


def recall(keys: Iterable[str], topic: str, now: Optional[float] = None) -> Optional[str]:
    keys = list(keys)
    if not keys:
        return None
    now = now or time.time()
    conn = get_connection()
    try:
        with conn:
            _ensure(conn)
            marks = ",".join("?" * len(keys))
            row = conn.execute(f"SELECT reason FROM source_verdicts WHERE topic=? AND ts>=? AND key IN ({marks}) LIMIT 1",
                               (topic_key(topic), now - MAX_AGE_DAYS * 86400, *keys)).fetchone()
        return row["reason"] if row else None
    finally:
        conn.close()


def remember(keys: Iterable[str], topic: str, reason: str, now: Optional[float] = None) -> None:
    keys = list(keys)
    if not keys:
        return
    conn = get_connection()
    try:
        with conn:
            _ensure(conn)
            for key in keys:
                conn.execute("INSERT INTO source_verdicts VALUES(?,?,?,?) ON CONFLICT(key,topic) DO UPDATE SET reason=excluded.reason, ts=excluded.ts",
                             (key, topic_key(topic), reason[:600], now or time.time()))
    finally:
        conn.close()

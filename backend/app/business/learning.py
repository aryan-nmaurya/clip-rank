"""PerformanceAnalyzer: learn from real results without being fooled by one lucky video.

Method: each published video's reach is log-scaled and capped, each creative choice
(format, duration band, hook type, ...) is scored by the *median* of its group, then
shrunk toward the channel baseline in proportion to how little data it has. Groups with
fewer than MIN_GROUP samples, or any analysis under MIN_VIDEOS videos, produce nothing.
Every adjustment stores the numbers behind it. The output is a set of small, bounded
weights; this module never rewrites strategy by itself.
"""
import json
import math
import statistics
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.core.database import get_connection

MIN_VIDEOS = 8          # below this the channel has no baseline worth learning from
MIN_GROUP = 3           # a pattern needs at least this many videos
SHRINK = 5              # pseudo-observations pulling a group toward the baseline
MAX_WEIGHT = 0.30       # no single dimension can move selection odds by more than ±30%
OUTLIER_QUANTILE = 0.90 # one viral video cannot dominate its group
WINDOW_PREFERENCE = ("7d", "72h", "24h", "early")


def duration_band(seconds: Optional[float]) -> Optional[str]:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    return "≤20s" if seconds <= 20 else "20–30s" if seconds <= 30 else "30–45s" if seconds <= 45 else ">45s"


def creative_metadata(clip: Dict[str, Any], project: Dict[str, Any]) -> Dict[str, Any]:
    """The creative choices behind one published video (recorded facts, not guesses)."""
    result = project.get("result_data", {}) if project else {}
    records = result.get("variants") or result.get("moments") or []
    record = next((r for r in records if r.get("clip_id") == clip.get("id")), records[0] if records else {})
    timeline = record.get("timeline") or []
    voices = [b["speech"].get("voice") for b in timeline if isinstance(b.get("speech"), dict) and b["speech"].get("voice")]
    mode = project.get("mode") if project else None
    hook = record.get("hook") or (record.get("script") or {}).get("hook") or ""
    return {
        "engine": mode, "topic": result.get("verified_topic") or (project or {}).get("title"),
        "duration": clip.get("duration"), "duration_band": duration_band(clip.get("duration")),
        "narration": "narrated" if voices else "original_audio",
        "voice": voices[0] if voices else None,
        "hook_type": ("question" if hook.strip().endswith("?") else "statement") if hook else None,
        "caption_style": result.get("caption_style") or "word_highlight",
        "content_pillar": result.get("pillar") or result.get("verified_topic"),
        "source_type": "ranking_compilation" if mode == "ranking" else record.get("format") or mode,
        "ranking": mode == "ranking",
        "visual_style": (record.get("framing") or {}).get("layout") or (project or {}).get("input_data", {}).get("layout"),
    }


def _window_for(hours: float) -> str:
    return "early" if hours < 24 else "24h" if hours < 72 else "72h" if hours < 168 else "7d"


def record_snapshot(video_id: str, published_at: float, data: Dict[str, Any], now: Optional[float] = None) -> str:
    """Store one reporting window per video (the latest reading inside that window)."""
    now = now or time.time()
    window = _window_for(max(0.0, (now - published_at) / 3600))
    conn = get_connection()
    with conn:
        conn.execute("INSERT INTO analytics_snapshots VALUES(?,?,?,?) ON CONFLICT(video_id,window) DO UPDATE SET collected_at=excluded.collected_at,data=excluded.data",
                     (video_id, window, now, json.dumps(data)))
    conn.close()
    return window


def best_snapshots() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT video_id, window, collected_at, data FROM analytics_snapshots").fetchall()
    conn.close()
    by_video: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for row in rows:
        by_video[row["video_id"]][row["window"]] = {**json.loads(row["data"]), "window": row["window"]}
    chosen = []
    for video_id, windows in by_video.items():
        for name in WINDOW_PREFERENCE:
            if name in windows:
                chosen.append({"video_id": video_id, **windows[name]})
                break
    return chosen


def _score(views: Optional[float]) -> Optional[float]:
    return math.log1p(views) if isinstance(views, (int, float)) and views >= 0 else None


def analyze(snapshots: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    snapshots = best_snapshots() if snapshots is None else snapshots
    scored = [(s, _score(s.get("views"))) for s in snapshots]
    scored = [(s, v) for s, v in scored if v is not None]
    if len(scored) < MIN_VIDEOS:
        return {"ready": False, "videos": len(scored), "adjustments": [],
                "message": f"{len(scored)} of {MIN_VIDEOS} published videos have measured results. ClipRank will not learn from fewer."}
    baseline = statistics.median(v for _, v in scored)
    dimensions = ("engine", "duration_band", "narration", "hook_type", "voice", "caption_style", "source_type", "ranking", "visual_style")
    adjustments = []
    for dimension in dimensions:
        groups: Dict[Any, List[float]] = defaultdict(list)
        for snapshot, value in scored:
            key = (snapshot.get("creative") or {}).get(dimension)
            if key is not None:
                groups[key].append(value)
        if len(groups) < 2:
            continue                       # a dimension with one value teaches nothing
        for key, values in groups.items():
            if len(values) < MIN_GROUP:
                continue
            cap = sorted(values)[min(len(values) - 1, int(len(values) * OUTLIER_QUANTILE))]
            values = [min(v, cap) for v in values]
            n = len(values)
            shrunk = (n * statistics.median(values) + SHRINK * baseline) / (n + SHRINK)
            weight = max(-MAX_WEIGHT, min(MAX_WEIGHT, (shrunk - baseline) / max(baseline, 1e-9)))
            if abs(weight) < 0.02:
                continue
            adjustments.append({"dimension": dimension, "value": str(key), "weight": round(weight, 3), "sample_size": n,
                "reason": f"{dimension} = {key}: median reach {math.expm1(statistics.median(values)):,.0f} views over {n} videos vs channel median "
                          f"{math.expm1(baseline):,.0f} over {len(scored)} (shrunk toward baseline; outliers capped)."})
    adjustments.sort(key=lambda a: -abs(a["weight"]))
    return {"ready": True, "videos": len(scored), "baseline_views": round(math.expm1(baseline)), "adjustments": adjustments,
            "message": f"Learned from {len(scored)} measured videos." if adjustments else "No pattern is strong enough yet; strategy unchanged."}


def refresh() -> Dict[str, Any]:
    """Analyze and persist the current adjustments, replacing the previous set."""
    result = analyze()
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM strategy_adjustments")
        for a in result["adjustments"]:
            conn.execute("INSERT INTO strategy_adjustments(created_at,dimension,value,weight,sample_size,reason) VALUES(?,?,?,?,?,?)",
                         (time.time(), a["dimension"], a["value"], a["weight"], a["sample_size"], a["reason"]))
    conn.close()
    return result


def current_adjustments() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT dimension,value,weight,sample_size,reason,created_at FROM strategy_adjustments ORDER BY ABS(weight) DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

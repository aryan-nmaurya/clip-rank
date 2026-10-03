"""One honest dashboard: growth, monetization tracking, revenue, costs and net - recorded data only."""
import json
import time
from typing import Any, Dict, Optional

import requests

from app.business import learning, store
from app.core.database import get_connection

# YouTube Partner Programme entry paths as published by YouTube; shown as tracking targets, never as a prediction.
YPP_TRACKS = [
    {"name": "Fan funding (500 subscribers)", "subscribers": 500, "public_uploads_90d": 3, "shorts_views_90d": 3_000_000},
    {"name": "Ad revenue sharing (1,000 subscribers)", "subscribers": 1000, "public_uploads_90d": None, "shorts_views_90d": 10_000_000},
]


def channel_statistics() -> Optional[Dict[str, Any]]:
    """Subscribers/views/videos straight from the connected channel; None when unavailable."""
    try:
        from app.publishing import youtube
        status = youtube.connection_status()
        if not status["connected"]:
            return None
        token = youtube._access_token(status["channel_id"])
        response = requests.get(youtube.API_URL + "/channels", params={"part": "statistics", "mine": "true"},
                                headers={"Authorization": "Bearer " + token}, timeout=20)
        if response.status_code != 200:
            return None
        items = response.json().get("items", [])
        stats = items[0].get("statistics", {}) if items else {}
        hidden = bool(stats.get("hiddenSubscriberCount"))
        return {"channel": status.get("channel_title"), "subscribers": None if hidden else int(stats.get("subscriberCount", 0)),
                "views": int(stats.get("viewCount", 0)), "videos": int(stats.get("videoCount", 0))}
    except Exception:
        return None


def build(channel: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = store.get_config()
    money = store.totals()
    conn = get_connection()
    uploads = conn.execute("SELECT COUNT(*) AS n FROM youtube_uploads WHERE status='UPLOADED'").fetchone()["n"]
    recent = conn.execute("SELECT COUNT(*) AS n FROM youtube_uploads WHERE status='UPLOADED' AND updated_at >= ?",
                          (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 90 * 86400)),)).fetchone()["n"]
    conn.close()
    snapshots = learning.best_snapshots()
    tracked_views = sum(s.get("views") or 0 for s in snapshots) if snapshots else None
    subscribers_gained = [s.get("subscribersGained") for s in snapshots if s.get("subscribersGained") is not None]

    revenue_total = sum(money["revenue_cents"].values()) / 100
    cost_ledger = sum(money["cost_cents"].values()) / 100
    ai = store.ai_usage_summary()
    ai_cost = ai["estimated_cost"] or 0.0
    recorded_costs = round(cost_ledger + ai_cost, 2)
    channel = channel if channel is not None else channel_statistics()
    subs = channel["subscribers"] if channel else None
    return {
        "currency": config["currency"],
        "growth": {"uploads": uploads, "uploads_last_90_days": recent, "tracked_views": tracked_views,
                   "subscribers_gained_tracked": sum(subscribers_gained) if subscribers_gained else None, "channel": channel,
                   "source": "YouTube Data/Analytics API for ClipRank uploads; unavailable metrics stay unavailable."},
        "monetization": {"status": config["ypp_status"], "subscribers": subs, "tracks": [
            {**t, "subscribers_progress": (round(min(1.0, subs / t["subscribers"]), 3) if subs is not None else None),
             "uploads_ok": (recent >= t["public_uploads_90d"]) if t["public_uploads_90d"] else None} for t in YPP_TRACKS],
            "note": "Eligibility is tracked against YouTube's published thresholds. Only YouTube decides approval; ClipRank guarantees nothing."},
        "revenue": {"total": round(revenue_total, 2), "platform": money["revenue_cents"].get("platform", 0) / 100,
                    "note": "Entered or imported revenue only. No projections are shown."},
        "affiliate": {"revenue": money["revenue_cents"].get("affiliate", 0) / 100, "programs": len(store.affiliates())},
        "sponsorship": {"revenue": money["revenue_cents"].get("sponsorship", 0) / 100, "tracking": "manual"},
        "costs": {"recorded": recorded_costs, "ledger": round(cost_ledger, 2), "cloud_ai": ai,
                  "by_category": {k: v / 100 for k, v in money["cost_cents"].items()}},
        "net": {"amount": round(revenue_total - recorded_costs, 2),
                "formula": "recorded revenue − recorded operating costs"},
        "insights": learning.current_adjustments(),
    }

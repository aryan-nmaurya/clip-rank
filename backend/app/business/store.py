"""Actual-data business records: revenue and cost ledger, AI usage, affiliate programs, strategy notes.

Nothing in here is projected or estimated. A figure exists only because an integration
reported it or the channel owner entered it, and every entry says which.
"""
import json
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.database import get_connection

REVENUE_CATEGORIES = ("platform", "sponsorship", "affiliate", "other")
COST_CATEGORIES = ("cloud_ai", "video_generation", "storage", "software", "other")
CONFIG_KEYS = {"ypp_status", "currency", "ai_input_price_per_million", "ai_output_price_per_million"}
YPP_STATUSES = ("not_applied", "eligible_tracking", "applied", "approved")


def init_business() -> None:
    conn = get_connection()
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK (kind IN ('revenue','cost')),
            category TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0), currency TEXT NOT NULL,
            note TEXT NOT NULL, occurred_on TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual', created_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS business_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS affiliate_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, brand TEXT NOT NULL, url TEXT NOT NULL, categories TEXT NOT NULL,
            disclosure TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS analytics_snapshots (
            video_id TEXT NOT NULL, window TEXT NOT NULL, collected_at REAL NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY (video_id, window))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS strategy_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, dimension TEXT NOT NULL, value TEXT NOT NULL,
            weight REAL NOT NULL, sample_size INTEGER NOT NULL, reason TEXT NOT NULL)""")
    conn.close()


# ----------------------------------------------------------------------- config
def get_config() -> Dict[str, Any]:
    conn = get_connection()
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM business_config")}
    conn.close()
    return {"ypp_status": rows.get("ypp_status", "not_applied"), "currency": rows.get("currency", "USD"),
            "ai_input_price_per_million": float(rows["ai_input_price_per_million"]) if "ai_input_price_per_million" in rows else None,
            "ai_output_price_per_million": float(rows["ai_output_price_per_million"]) if "ai_output_price_per_million" in rows else None}


def set_config(values: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, str] = {}
    for key, value in values.items():
        if key not in CONFIG_KEYS:
            raise ValueError(f"Unknown setting {key!r}.")
        if key == "ypp_status" and value not in YPP_STATUSES:
            raise ValueError("Choose a valid monetization status.")
        if key == "currency" and not (isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value)):
            raise ValueError("Use a three-letter currency code such as USD.")
        if key.endswith("million"):
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 1000:
                raise ValueError("Token prices must be a number between 0 and 1000 per million tokens.")
        clean[key] = str(value)
    conn = get_connection()
    with conn:
        for key, value in clean.items():
            conn.execute("INSERT INTO business_config VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.close()
    return get_config()


# ----------------------------------------------------------------------- ledger
def add_entry(kind: str, category: str, amount: float, note: str, occurred_on: Optional[str] = None) -> Dict[str, Any]:
    if kind not in ("revenue", "cost"):
        raise ValueError("Entry kind must be revenue or cost.")
    if category not in (REVENUE_CATEGORIES if kind == "revenue" else COST_CATEGORIES):
        raise ValueError(f"Choose a {kind} category: " + ", ".join(REVENUE_CATEGORIES if kind == "revenue" else COST_CATEGORIES))
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount != amount or not 0 <= amount <= 10_000_000:
        raise ValueError("Enter an amount between 0 and 10,000,000.")
    if not isinstance(note, str) or not 3 <= len(note.strip()) <= 300:
        raise ValueError("Describe where this figure comes from (3–300 characters).")
    day = occurred_on or date.today().isoformat()
    try:
        if date.fromisoformat(day) > date.today():
            raise ValueError("Future-dated entries are projections, not records.")
    except ValueError as exc:
        raise ValueError(str(exc) if "projection" in str(exc) else "Use a YYYY-MM-DD date.") from exc
    currency = get_config()["currency"]
    conn = get_connection()
    with conn:
        cursor = conn.execute("INSERT INTO ledger(kind,category,amount_cents,currency,note,occurred_on,created_at) VALUES(?,?,?,?,?,?,?)",
                              (kind, category, round(amount * 100), currency, note.strip(), day, time.time()))
    conn.close()
    return {"id": cursor.lastrowid, "kind": kind, "category": category, "amount": round(amount, 2), "currency": currency,
            "note": note.strip(), "occurred_on": day, "source": "manual"}


def delete_entry(entry_id: int) -> bool:
    conn = get_connection()
    with conn:
        removed = conn.execute("DELETE FROM ledger WHERE id=?", (entry_id,)).rowcount
    conn.close()
    return bool(removed)


def entries(limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM ledger ORDER BY occurred_on DESC, id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [{"id": r["id"], "kind": r["kind"], "category": r["category"], "amount": r["amount_cents"] / 100, "currency": r["currency"],
             "note": r["note"], "occurred_on": r["occurred_on"], "source": r["source"]} for r in rows]


def totals() -> Dict[str, Any]:
    conn = get_connection()
    revenue = {r["category"]: r["total"] for r in conn.execute(
        "SELECT category, SUM(amount_cents) AS total FROM ledger WHERE kind='revenue' GROUP BY category")}
    costs = {r["category"]: r["total"] for r in conn.execute(
        "SELECT category, SUM(amount_cents) AS total FROM ledger WHERE kind='cost' GROUP BY category")}
    conn.close()
    return {"revenue_cents": revenue, "cost_cents": costs}


# ------------------------------------------------------------------- AI usage
def record_ai_usage(provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
    conn = get_connection()
    with conn:
        conn.execute("INSERT INTO ai_usage(ts,provider,model,input_tokens,output_tokens) VALUES(?,?,?,?,?)",
                     (time.time(), provider, model, max(0, int(input_tokens or 0)), max(0, int(output_tokens or 0))))
    conn.close()


def ai_usage_summary() -> Dict[str, Any]:
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o FROM ai_usage").fetchone()
    conn.close()
    config = get_config()
    priced = config["ai_input_price_per_million"] is not None and config["ai_output_price_per_million"] is not None
    cost = (row["i"] * config["ai_input_price_per_million"] + row["o"] * config["ai_output_price_per_million"]) / 1_000_000 if priced else None
    return {"calls": row["calls"], "input_tokens": row["i"], "output_tokens": row["o"],
            "estimated_cost": round(cost, 4) if cost is not None else None, "priced": priced,
            "note": "Cost is tokens × the per-million prices you configured." if priced else "Set your token prices to convert usage into a cost."}


# ------------------------------------------------------------------ affiliates
def add_affiliate(brand: str, url: str, categories: List[str], disclosure: str) -> Dict[str, Any]:
    if not isinstance(brand, str) or not 2 <= len(brand.strip()) <= 80:
        raise ValueError("Enter the brand name.")
    if not re.fullmatch(r"https://[^\s<>\"']{4,500}", url or ""):
        raise ValueError("Use the approved https:// affiliate link supplied by the program.")
    words = [c.strip().lower() for c in categories if isinstance(c, str) and 2 <= len(c.strip()) <= 40]
    if not words:
        raise ValueError("List at least one topic this affiliate genuinely relates to (e.g. 'parkour shoes').")
    if not isinstance(disclosure, str) or not 10 <= len(disclosure.strip()) <= 300:
        raise ValueError("Write the disclosure viewers will see (e.g. 'Affiliate link: I may earn a commission.').")
    conn = get_connection()
    with conn:
        cursor = conn.execute("INSERT INTO affiliate_profiles(brand,url,categories,disclosure) VALUES(?,?,?,?)",
                              (brand.strip(), url, json.dumps(words), disclosure.strip()))
    conn.close()
    return {"id": cursor.lastrowid, "brand": brand.strip(), "url": url, "categories": words, "disclosure": disclosure.strip(), "enabled": True}


def affiliates() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM affiliate_profiles ORDER BY id").fetchall()
    conn.close()
    return [{"id": r["id"], "brand": r["brand"], "url": r["url"], "categories": json.loads(r["categories"]),
             "disclosure": r["disclosure"], "enabled": bool(r["enabled"])} for r in rows]


def set_affiliate_enabled(affiliate_id: int, enabled: bool) -> None:
    conn = get_connection()
    with conn:
        conn.execute("UPDATE affiliate_profiles SET enabled=? WHERE id=?", (1 if enabled else 0, affiliate_id))
    conn.close()


def delete_affiliate(affiliate_id: int) -> bool:
    conn = get_connection()
    with conn:
        removed = conn.execute("DELETE FROM affiliate_profiles WHERE id=?", (affiliate_id,)).rowcount
    conn.close()
    return bool(removed)


def matching_affiliate_block(content_text: str) -> str:
    """Disclosure + link for each enabled program whose topic words genuinely appear in this Short's content.

    No keyword in the content, no link: unrelated entertainment is never decorated with offers.
    """
    text = (content_text or "").lower()
    blocks = []
    for profile in affiliates():
        if profile["enabled"] and any(re.search(rf"\b{re.escape(word)}s?\b", text) for word in profile["categories"]):
            blocks.append(f"{profile['disclosure']}\n{profile['brand']}: {profile['url']}")
    return "\n\n".join(blocks)

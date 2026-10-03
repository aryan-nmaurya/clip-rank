"""Errors that must stop a job instead of being mistaken for "this clip failed verification"."""
import re
from typing import Optional


class AIQuotaExceeded(Exception):
    """The AI provider refused further requests (daily quota / billing).

    Deliberately NOT a ValueError/RuntimeError: per-source handlers that turn those into
    "rejected clip" must not swallow it. Burning through candidates while every call is
    refused only labels good footage as unverifiable.
    """

    def __init__(self, provider: str, retry_after: Optional[float], detail: str = ""):
        self.provider, self.retry_after = provider, retry_after
        wait = (f" Retry in about {format_wait(retry_after)}." if retry_after and retry_after >= 60
                else " The provider gave no reset time; free-tier daily quotas usually reset within 24 hours.")
        super().__init__(f"{provider.title()} quota exceeded: no more AI requests are available right now.{wait} "
                         "Wait for the quota to reset, or switch the AI provider in Settings. " + detail[:200])


def format_wait(seconds: Optional[float]) -> str:
    if not seconds:
        return "unknown time"
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    return (f"{hours}h " if hours else "") + f"{minutes}m"


_DELAY = re.compile(r"retry in (?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:([\d.]+)s)?", re.I)
_RETRY_DELAY_FIELD = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", re.I)


def retry_delay_seconds(message: str) -> Optional[float]:
    """Seconds the provider asked us to wait, from either of its message formats."""
    field = _RETRY_DELAY_FIELD.search(message or "")
    if field:
        return float(field.group(1))
    match = _DELAY.search(message or "")
    if match and any(match.groups()):
        hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    return None


def is_daily_quota(message: str, delay: Optional[float]) -> bool:
    text = (message or "").lower()
    return ("perday" in text.replace(" ", "").replace("_", "") or "free_tier_requests" in text or "insufficient_quota" in text
            or "billing" in text and "quota" in text or (delay is not None and delay > 120))

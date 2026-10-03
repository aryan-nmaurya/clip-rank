"""The Quality-control switch.

ON  : every production check runs (file spec, black/frozen/audio inspection, AI verification and final review,
      minimum length, source screening, scoring gates).
OFF : nothing is rejected for quality. ClipRank renders whatever the pipeline produces and stores it. The AI is not
      asked to verify or review, so no verification calls are spent and no check can discard a render.
Only structural requirements remain, because the render cannot be made without them: the footage must be a readable
video, narration needs measurable word timings for captions, and a Ranking needs enough clips to fill its ranks.
The Diagnostics "production test" always runs real checks regardless of this switch.
"""
import time

_cache = {"at": 0.0, "value": True}


def enabled() -> bool:
    """True when quality control is on. Defaults to ON if settings cannot be read (never silently unprotected)."""
    now = time.monotonic()
    if now - _cache["at"] < 1.0:
        return _cache["value"]
    try:
        from app.core.database import get_settings
        value = bool(get_settings().get("quality_control", False))
    except Exception:
        value = True
    _cache.update(at=now, value=value)
    return value


def reset_cache() -> None:
    _cache["at"] = 0.0

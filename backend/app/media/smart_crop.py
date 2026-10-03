"""Smart 9:16 reframing: landscape footage is cropped to a window that follows the subject.

Widescreen footage letterboxed over a blurred background wastes most of a phone screen. For landscape
sources ClipRank instead crops a portrait window and moves it with the faces (or, when there are none,
the action), using the same measured tracker Movie mode uses. Footage that is already portrait/square is
kept whole. Nothing is guessed: if tracking cannot be measured, the crop is centred rather than invented.
"""
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("ai_shorts.smart_crop")
LANDSCAPE_RATIO = 1.2     # width/height above this is treated as landscape


def is_landscape(info: Dict[str, Any]) -> bool:
    return bool(info.get("height")) and info["width"] / info["height"] >= LANDSCAPE_RATIO


def decide(video, info: Dict[str, Any], start: float, duration: float, canvas_w: int, canvas_h: int) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return (tracked framing or None, fallback layout). The canvas is the area the footage must fill."""
    if not is_landscape(info):
        return None, "fit"
    crop_w = round(info["height"] * canvas_w / canvas_h)       # a window with exactly the canvas' aspect ratio
    if crop_w >= info["width"]:
        return None, "fit"
    try:
        from app.pipelines.movie.framing import MovieReframing
        plan = MovieReframing.plan(video, start, start + duration, "COMMENTARY", portrait=True, focus="faces")
    except (ValueError, OSError) as exc:
        logger.warning("Subject tracking unavailable (%s); centre-cropping %s.", str(exc)[:120], video)
        return None, "fill"
    if plan.get("layout") != "tracked":
        return None, "fill"
    measured_w = plan["crop_width"]
    points = []
    for point in plan["points"]:
        centre = point["x"] + measured_w / 2          # keep the subject where the tracker found it
        x = max(0.0, min(info["width"] - crop_w, centre - crop_w / 2))
        points.append({**point, "x": round(x, 2)})
    return {"layout": "tracked", "crop_width": crop_w, "points": points, "reason": plan.get("reason", "")}, "fit"

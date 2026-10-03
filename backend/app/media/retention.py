"""RetentionDirector: deterministic checks that a Short earns the next second of attention.

It never scores "virality". It enforces what can be measured on the finished file and
its edit plan: no filler opening, value in the first seconds, no stretches of dead air,
and a pace that keeps moving. LLM-written hooks are validated elsewhere; this is the
shared, model-independent last line for every engine (ranking, clips, movie).
"""
import re
from typing import Any, Dict, List

from app.core.runtime import run_process

FILLER_OPENING = re.compile(
    r"^\W*(?:(?:hello|hi|hey|yo|what'?s up)\b[^.!?]{0,24}\b(?:guys|everyone|everybody|folks|friends|y'?all|people|world)|"
    r"welcome (?:back|to)|today we(?:'re| are)\b|in this video|before we (?:start|begin|get started)|"
    r"don'?t forget to|(?:please )?(?:like|subscribe)\b|let'?s (?:get started|jump right in)|"
    r"my name is|thanks for (?:watching|clicking))", re.I)
MAX_DEAD_AIR_SECONDS = 4.0     # fail: nothing audible for this long
WARN_DEAD_AIR_SECONDS = 2.5    # warn only
MAX_BEAT_SECONDS = 20.0        # a single unbroken beat longer than this drags
VALUE_BY_SECONDS = 2.0         # first narration/caption/graphic must land by here


def validate_opening(text: str) -> str:
    """Raise if a spoken or captioned opening is filler. Returns the text when it is acceptable."""
    if text and FILLER_OPENING.search(text.strip()):
        raise ValueError(f"The opening {text.strip()[:60]!r} is filler. Start with the hook or the action, not a greeting or announcement.")
    return text


def silences(video, noise_db: int = -45, minimum: float = 1.2) -> List[Dict[str, float]]:
    """Silent stretches of the finished audio (FFmpeg silencedetect on the real MP4)."""
    output = run_process(["ffmpeg", "-hide_banner", "-i", str(video), "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={minimum}",
                          "-f", "null", "-"], include_stderr=True).decode(errors="replace")
    starts = [float(m) for m in re.findall(r"silence_start: (-?[0-9.]+)", output)]
    ends = re.findall(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)", output)
    result = []
    for index, start in enumerate(starts):
        if index < len(ends):
            end, length = float(ends[index][0]), float(ends[index][1])
        else:  # silence runs to the end of the file
            end = length = None
        result.append({"start": max(0.0, start), "end": end, "duration": length})
    return result


def review(video, duration: float, timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a retention report; raise ValueError for rules that must fail the Short."""
    issues: List[str] = []
    warnings: List[str] = []
    first = timeline[0] if timeline else {}
    opening_text = str(first.get("narration") or " ".join(s.get("text", "") for s in first.get("original_speech", [])[:1]))
    validate_opening(opening_text)

    speech = first.get("speech") or {}
    first_word = (speech.get("words") or [{}])[0].get("start") if speech else None
    first_source = next((w["start"] for s in first.get("original_speech", []) for w in s.get("words", [])), None)
    spoken_at = min([t for t in (first_word, first_source) if isinstance(t, (int, float))], default=None)
    if spoken_at is not None and spoken_at > VALUE_BY_SECONDS:
        warnings.append(f"Speech does not begin until {spoken_at:.1f}s; the opening relies on the visuals and graphics.")

    dead = silences(video)
    for gap in dead:
        length = gap["duration"] if gap["duration"] is not None else max(0.0, duration - gap["start"])
        if length > MAX_DEAD_AIR_SECONDS:
            issues.append(f"{length:.1f}s of dead air starting at {gap['start']:.1f}s.")
        elif length > WARN_DEAD_AIR_SECONDS:
            warnings.append(f"{length:.1f}s of near-silence at {gap['start']:.1f}s.")
    longest_beat = max((float(item.get("duration") or 0) for item in timeline), default=0.0)
    if len(timeline) > 1 and longest_beat > MAX_BEAT_SECONDS:
        issues.append(f"One beat runs {longest_beat:.0f}s; tighten it so the countdown keeps escalating.")
    if issues:
        raise ValueError("Retention check failed: " + " ".join(issues))
    return {"passed": True, "opening": opening_text[:80], "first_speech_seconds": spoken_at,
            "longest_beat_seconds": round(longest_beat, 2), "dead_air": [g for g in dead if (g["duration"] or 0) > WARN_DEAD_AIR_SECONDS],
            "warnings": warnings}

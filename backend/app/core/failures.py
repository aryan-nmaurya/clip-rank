"""Turn exceptions into actionable job failures, and keep secrets out of every log.

Every failed job answers four questions: WHAT failed, WHY, CAN it retry, and what
should happen NEXT. The raw traceback is stored (redacted) for diagnosis; the user
sees the classified summary, never "Something went wrong".
"""
import json
import logging
import re
import traceback
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

SECRET_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "[redacted-google-key]"),
    (re.compile(r"ya29\.[0-9A-Za-z_\-]{20,}"), "[redacted-google-token]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[redacted-api-key]"),
    (re.compile(r"gsk_[A-Za-z0-9]{20,}"), "[redacted-groq-key]"),
    (re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"), "[redacted-nvidia-key]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"), r"\1[redacted]"),
    (re.compile(r"(?i)((?:api[_-]?key|key|token|access_token|refresh_token|client_secret|password|secret)[\"']?\s*[=:]\s*[\"']?)[^\s\"'&,}]{6,}"), r"\1[redacted]"),
]


def redact(text: Any) -> str:
    value = "" if text is None else str(text)
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class RedactingFilter(logging.Filter):
    """Installed on the root logger so no handler can print a credential."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # a malformed log call must never break the application
            record.msg = "[unprintable log record]"
            record.args = ()
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    for handler in root.handlers or [logging.StreamHandler()]:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
        install_log_redaction()


class ProcessError(RuntimeError):
    """A media subprocess exited non-zero. Keeps the facts instead of just stderr text."""

    def __init__(self, tool: str, returncode: int, stderr: str):
        self.tool, self.returncode, self.stderr = tool, returncode, stderr
        super().__init__(stderr or f"{tool} exited with code {returncode}")


@dataclass
class JobFailure:
    stage: str
    what: str
    why: str
    retryable: bool
    next_step: str
    code: str
    exception: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# (code, regex over "ExceptionType: message", what, retryable, next step). First match wins.
RULES = [
    ("FFMPEG_MISSING", r"No such file or directory: 'ff(?:mpeg|probe)'|ffmpeg: command not found",
     "FFmpeg is not installed", False, "Install FFmpeg and FFprobe, then retry."),
    ("DISK_FULL", r"No space left|needs at least \d+ MB of free disk|disk quota",
     "The computer is out of disk space", True, "Free disk space (Settings → Storage), then retry."),
    ("SIGN_IN_REQUIRED", r"Sign in to confirm|Please sign in|--cookies|login required|private video",
     "The source site requires sign-in", False, "Use a different public source or upload the footage you own."),
    ("SOURCE_UNAVAILABLE", r"Couldn't download|Video unavailable|HTTP Error 4\d\d|unable to download|removed by the uploader",
     "A source video could not be downloaded", True, "Retry; ClipRank will pick other sources. Supply links or uploads if it persists."),
    ("NOT_ENOUGH_SOURCES", r"Only \d+ unused individual clips|Found \d+ usable sources|No individual videos were found",
     "Not enough verified source clips", True, "Retry with a broader topic, add source links, or upload raw clips."),
    ("VISION_UNAVAILABLE", r"vision model|could not verify that the source|Gemini .*unavailable|Local .*unavailable|not connected",
     "The vision model could not be reached", True, "Check the AI connection in Settings (Test vision), then retry."),
    ("AI_QUOTA", r"AIQuotaExceeded|quota exceeded",
     "The AI provider's quota is used up", True, "Wait for the quota to reset (the time is in the message) or choose another AI provider in Settings, then retry."),
    ("AI_RATE_LIMIT", r"\b429\b|RESOURCE_EXHAUSTED|quota",
     "The AI provider rate limit or quota was reached", True, "Wait a few minutes or switch provider in Settings, then retry."),
    ("VOICE_FAILED", r"Pocket TTS|narration|NarrationAlignment|speech verification|voice",
     "Narration could not be produced or verified", True, "Retry (the line is rewritten) or choose another voice in Settings."),
    ("TRANSCRIPTION_FAILED", r"transcri|Whisper|caption word",
     "Speech recognition failed", True, "Retry; confirm the Whisper model exists in storage/models."),
    ("VERIFICATION_FAILED", r"failed verification|Final video does not|Export must contain|Export requires|Export ended early",
     "The rendered MP4 did not meet the delivery spec", True, "Retry; the render is repeated from the saved plan."),
    ("QC_REJECTED", r"QC rejected|production QC|did not pass|final multimodal|Narration is|black section|frozen section|clips or distorts",
     "Quality control rejected the finished video", True, "Retry for a new edit, or choose a different topic or sources."),
    ("RIGHTS_BLOCKED", r"rights|licen[cs]e|provenance",
     "Source rights could not be established", False, "Record the rights basis for the sources or use cleared footage."),
    ("TIMEOUT", r"TimeoutError|exceeded \d+s|timed out",
     "A step exceeded its time limit", True, "Retry; use a shorter source if it repeats."),
    ("CANCELLED", r"InterruptedError|cancelled",
     "The job was cancelled", False, "Start a new job when ready."),
    ("RENDER_FAILED", r"ProcessError|ffmpeg|Media operation|Invalid argument|Error (?:while|opening)",
     "FFmpeg could not render the video", True, "Retry; if it repeats the log below names the failing filter or input."),
]


def classify(exc: BaseException, stage: str = "Unknown") -> JobFailure:
    kind = type(exc).__name__
    text = redact(f"{kind}: {exc}")
    if isinstance(exc, ProcessError):
        text = redact(f"ProcessError: {exc.tool} exit {exc.returncode}: {exc.stderr}")
    for code, pattern, what, retryable, next_step in RULES:
        if re.search(pattern, text, re.I):
            return JobFailure(stage, what, redact(str(exc))[-600:] or kind, retryable, next_step, code, kind)
    return JobFailure(stage, f"{stage.title()} failed unexpectedly", redact(str(exc))[-600:] or kind, True,
                      "Retry once. If it fails again, the technical log holds the full traceback.", "UNEXPECTED", kind)


def failure_json(exc: BaseException, stage: str) -> Dict[str, Any]:
    """Classified summary plus the redacted traceback for the technical log."""
    summary = classify(exc, stage).as_dict()
    summary["traceback"] = redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))[-6000:]
    return summary


def user_message(failure: Dict[str, Any]) -> str:
    return f"{failure['what']} during {failure['stage'].lower()}: {failure['why']} → {failure['next_step']}"[:1000]


def parse(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(raw) if raw else None
        return value if isinstance(value, dict) and "what" in value else None
    except ValueError:
        return None

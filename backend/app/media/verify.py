"""Golden-output verification: does a finished MP4 meet the ClipRank delivery spec?

``FFmpegCore.validate_output`` checks a render against an expected duration while the
pipeline is still working. This module is the independent, final answer used before a
file is stored, played or published. It inspects the file on disk with FFprobe and a
full decode, and returns every violated rule instead of stopping at the first.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import AUDIO_SAMPLE_RATE, VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH
from app.core.runtime import run_process

MIN_DURATION = 10.1       # product rule: every Short is longer than 10 seconds
MAX_DURATION = 180.0      # YouTube Shorts ceiling
MAX_SHORT_SECONDS = 179.0  # longest source ClipRank will keep whole, safely inside that ceiling
MIN_FILE_BYTES = 20_000


class VerificationError(ValueError):
    """The MP4 violates the delivery spec. ``failures`` lists every broken rule."""

    def __init__(self, failures: List[str], report: Dict[str, Any]):
        super().__init__("Finished MP4 failed verification: " + "; ".join(failures))
        self.failures, self.report = failures, report


def probe(path: Path) -> Dict[str, Any]:
    raw = run_process(["ffprobe", "-v", "error", "-count_packets", "-show_entries",
                       "stream=index,codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,"
                       "duration,nb_read_packets,sample_rate,channels:format=duration,size,format_name",
                       "-of", "json", str(path)], timeout=120)
    return json.loads(raw)


def _fps(value: Optional[str]) -> float:
    try:
        numerator, denominator = (value or "0/1").split("/")
        return float(numerator) / float(denominator) if float(denominator) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def verify_mp4(path: Path, expected_duration: Optional[float] = None, expect_audio: bool = True,
               decode: bool = True) -> Dict[str, Any]:
    """Return a report for a passing file; raise ``VerificationError`` for a failing one."""
    path = Path(path)
    failures: List[str] = []
    if not path.is_file() or path.stat().st_size < MIN_FILE_BYTES:
        raise VerificationError(["file is missing or empty"], {"path": str(path)})
    try:
        data = probe(path)
    except (RuntimeError, ValueError) as exc:
        raise VerificationError([f"FFprobe could not read the file ({str(exc)[-160:]})"], {"path": str(path)}) from exc

    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    extras = [s for s in streams if s.get("codec_type") not in ("video", "audio")]
    duration = float(data.get("format", {}).get("duration") or 0)
    report: Dict[str, Any] = {
        "path": str(path), "size_bytes": path.stat().st_size, "duration": duration,
        "container": data.get("format", {}).get("format_name"),
        "streams": [s.get("codec_type") for s in streams],
    }

    if len(videos) != 1:
        failures.append(f"expected exactly one video stream, found {len(videos)}")
    else:
        video = videos[0]
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        frames = int(video.get("nb_read_packets") or 0)
        fps = _fps(video.get("avg_frame_rate"))
        report.update(width=width, height=height, video_codec=video.get("codec_name"), pix_fmt=video.get("pix_fmt"),
                      fps=round(fps, 3), frame_count=frames)
        if (width, height) != (VIDEO_WIDTH, VIDEO_HEIGHT):
            failures.append(f"resolution is {width}x{height}, required {VIDEO_WIDTH}x{VIDEO_HEIGHT} (9:16)")
        if video.get("codec_name") != "h264":
            failures.append(f"video codec is {video.get('codec_name')}, required h264")
        if video.get("pix_fmt") != "yuv420p":
            failures.append(f"pixel format is {video.get('pix_fmt')}, required yuv420p for universal playback")
        if abs(fps - VIDEO_FPS) > 0.5:
            failures.append(f"frame rate is {fps:.2f}, required {VIDEO_FPS}")
        if frames < 2:
            failures.append("video has no decodable frames")
        elif duration and fps and abs(frames / fps - duration) > max(0.75, duration * 0.05):
            failures.append(f"frame count {frames} does not match duration {duration:.2f}s at {fps:.1f} fps")

    if expect_audio:
        if len(audios) != 1:
            failures.append(f"expected one audio stream, found {len(audios)}")
        else:
            audio = audios[0]
            report.update(audio_codec=audio.get("codec_name"), sample_rate=int(audio.get("sample_rate") or 0),
                          channels=int(audio.get("channels") or 0))
            if audio.get("codec_name") != "aac":
                failures.append(f"audio codec is {audio.get('codec_name')}, required aac")
            if int(audio.get("sample_rate") or 0) != AUDIO_SAMPLE_RATE:
                failures.append(f"audio sample rate is {audio.get('sample_rate')}, required {AUDIO_SAMPLE_RATE}")
            if int(audio.get("channels") or 0) != 2:
                failures.append("audio is not stereo")
    elif audios:
        failures.append("unexpected audio stream")
    if extras:
        failures.append("stray " + "/".join(sorted({str(s.get('codec_type')) for s in extras})) + " stream(s) in the container")

    if not MIN_DURATION <= duration <= MAX_DURATION:
        failures.append(f"duration {duration:.2f}s is outside {MIN_DURATION}-{MAX_DURATION:.0f}s")
    if expected_duration is not None and abs(duration - expected_duration) > max(0.5, expected_duration * 0.03):
        failures.append(f"duration {duration:.2f}s differs from the planned {expected_duration:.2f}s")

    if decode and not failures:
        try:
            run_process(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], timeout=600)
        except (RuntimeError, TimeoutError) as exc:
            failures.append("file does not decode cleanly: " + str(exc)[-200:])
    report["checks_passed"] = not failures
    if failures:
        raise VerificationError(failures, report)
    return report

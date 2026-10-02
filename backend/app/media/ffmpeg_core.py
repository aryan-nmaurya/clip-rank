import json
from pathlib import Path
from typing import Dict, Any, List
from app.core.runtime import run_process


class FFmpegCore:
    @staticmethod
    def get_video_info(file_path: Path) -> Dict[str, Any]:
        data = json.loads(run_process([
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,duration:format=duration,size",
            "-of", "json", str(file_path)
        ], timeout=30))
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        info = data.get("format", {})
        return {"duration": float(info.get("duration") or video.get("duration") or 0),
                "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
                "has_video": bool(video), "has_audio": bool(audio), "size_bytes": int(info.get("size") or 0)}

    @staticmethod
    def extract_audio(input_video: Path, output_wav: Path) -> Path:
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        run_process(["ffmpeg", "-v", "error", "-y", "-i", str(input_video), "-vn",
                     "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(output_wav)])
        return output_wav

    @staticmethod
    def extract_frame(input_video: Path, output_jpg: Path, timestamp: float = 2.0) -> Path:
        output_jpg.parent.mkdir(parents=True, exist_ok=True)
        info = FFmpegCore.get_video_info(input_video)
        timestamp = max(0, min(timestamp, info["duration"] - 0.1))
        run_process(["ffmpeg", "-v", "error", "-y", "-ss", f"{timestamp:.3f}", "-i", str(input_video),
                     "-frames:v", "1", "-q:v", "2", str(output_jpg)], timeout=60)
        if not output_jpg.is_file():
            raise ValueError("Preview frame could not be extracted.")
        return output_jpg

    @staticmethod
    def concatenate_clips(clip_paths: List[Path], output_video: Path) -> Path:
        if not clip_paths:
            raise ValueError("No clips to concatenate.")
        output_video.parent.mkdir(parents=True, exist_ok=True)
        list_file = output_video.parent / "concat_list.txt"
        # FFmpeg's concat syntax has its own escaping (no shell is involved).
        list_file.write_text("".join("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n" for p in clip_paths))
        try:
            run_process(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                         "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(output_video)])
        finally:
            list_file.unlink(missing_ok=True)
        return output_video

    @staticmethod
    def validate_output(path: Path, expected_duration: float):
        info = FFmpegCore.get_video_info(path)
        if not info["has_video"] or not info["has_audio"] or (info["width"], info["height"]) != (720, 1280):
            raise ValueError("Export must contain a 720×1280 video and audio track.")
        if abs(info["duration"] - expected_duration) > max(0.5, expected_duration * .03):
            raise ValueError("Export ended early or exceeded its selected footage.")
        # Decode the file as well as inspecting its container metadata.
        run_process(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], timeout=600)
        return info

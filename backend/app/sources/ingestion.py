import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse
from app.core.runtime import check_cancelled
from app.media.ffmpeg_core import FFmpegCore

logger = logging.getLogger("ai_shorts.ingestion")


class SourceIngestion:
    @staticmethod
    def validate_url(url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError("Provide a valid HTTP(S) video URL or upload a video file.")
        return url.strip()

    @staticmethod
    def verify(path: Path):
        info = FFmpegCore.get_video_info(path)
        if not info["has_video"] or info["duration"] < 1:
            raise ValueError("Source has no playable video stream or is shorter than one second.")
        if info["duration"] > 7200:
            raise ValueError("Source videos must be two hours or shorter.")
        return info

    @classmethod
    def ingest_video_file(cls, source_file: Path, destination_file: Path) -> Path:
        if not source_file.is_file():
            raise ValueError("Source file is missing. Upload it again.")
        cls.verify(source_file)
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination_file)
        return destination_file

    @classmethod
    def download_video(cls, url: str, destination_file: Path):
        """Download actual footage; unavailable sources never become demo videos."""
        cls.validate_url(url)
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Video downloading requires yt-dlp. Run the app's dependency installer.") from exc
        destination_file.parent.mkdir(parents=True, exist_ok=True)

        def progress(_):
            check_cancelled()

        options = {
            "format": "bv*[height<=1080]+ba/b[height<=1080]/best",
            "outtmpl": str(destination_file.with_suffix("")) + ".%(ext)s",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            "max_filesize": 2 * 1024**3,
            "progress_hooks": [progress],
            "match_filter": lambda info, **_: "Source exceeds two hours" if (info.get("duration") or 0) > 7200 else None,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
                if not info:
                    raise ValueError("Source could not be downloaded.")
                path = Path(downloader.prepare_filename(info))
                merged = destination_file.with_suffix(".mp4")
                if merged.exists():
                    path = merged
                if not path.exists():
                    raise ValueError("Download did not produce a video file.")
                cls.verify(path)
                return path, {
                    "source_id": info.get("id") or url,
                    "title": info.get("title") or "Source video",
                    "url": info.get("webpage_url") or url,
                    "creator": info.get("uploader") or "",
                    "view_count": info.get("view_count") or 0,
                    "like_count": info.get("like_count") or 0,
                }
        except InterruptedError:
            raise
        except Exception as exc:
            check_cancelled()
            raise RuntimeError(f"Couldn't download this video. It may be private, restricted, or require sign-in. Try a public URL or upload the source. {str(exc)[-500:]}") from exc

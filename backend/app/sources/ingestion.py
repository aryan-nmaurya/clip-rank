import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from app.core.runtime import check_cancelled
from app.media.ffmpeg_core import FFmpegCore
from app.sources.errors import RankedSourceRejected

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
    def download_video(cls, url: str, destination_file: Path, reject_rankings=False, max_height: int = 1080, max_filesize: int = 2 * 1024**3,
                       max_seconds: int = 900, min_bytes_per_second: int = 40_000):
        """Download actual footage; unavailable sources never become demo videos."""
        cls.validate_url(url)
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Video downloading requires yt-dlp. Run the app's dependency installer.") from exc
        destination_file.parent.mkdir(parents=True, exist_ok=True)

        started = time.monotonic()

        def progress(status):
            """Runs on every chunk. A transfer that is too slow or too long is abandoned, never waited on forever:
            a throttled connection that trickles bytes never trips yt-dlp's own socket timeout."""
            check_cancelled()
            elapsed = time.monotonic() - started
            if elapsed > max_seconds:
                raise TimeoutError(f"Download exceeded {max_seconds}s and was abandoned.")
            done = status.get("downloaded_bytes") or 0
            if status.get("status") == "downloading" and elapsed > 30 and done / elapsed < min_bytes_per_second:
                raise TimeoutError(f"Download is too slow ({done / elapsed / 1000:.0f} KB/s) and was abandoned.")

        rejected_metadata = {}

        def match_filter(info, **_):
            if (info.get("duration") or 0) > 7200:
                return "Source exceeds two hours"
            if reject_rankings:
                from app.sources.ranking_policy import RankingSourcePolicy
                reason = RankingSourcePolicy.metadata_reason(info)
                if reason:
                    rejected_metadata.update(reason=reason, title=info.get("title"))
                return reason
            return None

        options = {
            "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/best",
            "outtmpl": str(destination_file.with_suffix("")) + ".%(ext)s",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            "max_filesize": max_filesize,
            "progress_hooks": [progress],
            "match_filter": match_filter,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
                if rejected_metadata:
                    raise RankedSourceRejected(rejected_metadata["reason"], rejected_metadata.get("title"))
                if not info:
                    raise ValueError("Source could not be downloaded.")
                rejected = match_filter(info)
                if rejected:
                    raise RankedSourceRejected(rejected, info.get("title"))
                path = Path(downloader.prepare_filename(info))
                merged = destination_file.with_suffix(".mp4")
                if merged.exists():
                    path = merged
                if not path.exists():
                    raise ValueError("Download did not produce a video file.")
                cls.verify(path)
                return path, {
                    "source_id": f"{info.get('extractor_key') or 'web'}:{info.get('id') or url}",
                    "title": info.get("title") or "Source video",
                    "url": info.get("webpage_url") or url,
                    "creator": info.get("uploader") or "",
                    "view_count": info.get("view_count") or 0,
                    "like_count": info.get("like_count") or 0,
                    "tags": info.get("tags") or [],
                    "platform": info.get("extractor_key") or urlparse(url).hostname,
                    "license": info.get('license') or 'unknown',
                    "license_evidence_url": info.get('license_url'),
                    "published_at": info.get('upload_date'),
                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                }
        except InterruptedError:
            raise
        except RankedSourceRejected:
            raise
        except Exception as exc:
            check_cancelled()
            raise RuntimeError(f"Couldn't download this video. It may be private, restricted, or require sign-in. Try a public URL or upload the source. {str(exc)[-500:]}") from exc

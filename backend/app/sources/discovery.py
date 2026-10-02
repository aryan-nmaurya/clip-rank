"""Search real videos and retain provenance for every downloaded candidate."""
import re
import hashlib
import json
import requests
from urllib.parse import urlencode
from pathlib import Path
from typing import List, Dict, Any
from app.core.config import MAX_SEARCH_RESULTS, MAX_SOURCE_VIDEOS
from app.core.runtime import check_cancelled
from app.sources.ingestion import SourceIngestion


class SourceDiscovery:
    @staticmethod
    def generate_search_queries(topic: str) -> List[str]:
        base = re.sub(r"\bmoments?\b", "", topic, flags=re.I).strip()
        return [base, f"{base} caught on camera"]

    @staticmethod
    def parse_search_page(html):
        """Read public video cards, including the current Shorts lockup renderer."""
        match = re.search(r"(?:var\s+)?ytInitialData\s*=\s*", html)
        if not match:
            return []
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
        entries = []
        seen = set()
        def visit(node):
            if isinstance(node, dict):
                lockup = node.get("shortsLockupViewModel")
                reel = node.get("reelItemRenderer")
                if lockup:
                    command = lockup.get("onTap", {}).get("innertubeCommand", {})
                    video_id = command.get("reelWatchEndpoint", {}).get("videoId")
                    title = lockup.get("overlayMetadata", {}).get("primaryText", {}).get("content")
                    title = title or re.sub(r", [\d.,]+ [\w ]*views.*$", "", lockup.get("accessibilityText", ""))
                elif reel:
                    video_id = reel.get("videoId")
                    title = reel.get("headline", {}).get("simpleText") or " ".join(r.get("text", "") for r in reel.get("headline", {}).get("runs", []))
                else:
                    video_id = None
                if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) and video_id not in seen:
                    seen.add(video_id)
                    entries.append({"id": video_id, "title": title or "Short video", "url": f"https://www.youtube.com/shorts/{video_id}"})
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        visit(data)
        return entries

    @classmethod
    def search_public_shorts(cls, query):
        url = "https://www.youtube.com/results?" + urlencode({"search_query": query, "sp": "EgIQCQ=="})
        response = requests.get(url, timeout=25)
        response.raise_for_status()
        return cls.parse_search_page(response.text)

    @classmethod
    def discover_candidate_videos(cls, topic: str, count: int, temp_dir: Path,
                                  source_urls=None, source_files=None, source_titles=None) -> List[Dict[str, Any]]:
        candidates = []
        failures = []
        entries = []
        supplied = bool(source_urls or source_files)
        for file_idx, path in enumerate(source_files or []):
            check_cancelled()
            source = Path(path)
            info = SourceIngestion.verify(source)
            with source.open("rb") as source_handle:
                source_hash = hashlib.file_digest(source_handle, "sha256").hexdigest()
            entries.append({"file_path": str(source), "title": source_titles[file_idx] if source_titles and file_idx < len(source_titles) else source.stem, "source_id": source_hash,
                            "duration": info["duration"], "url": None, "view_count": 0, "like_count": 0})
        for url in source_urls or []:
            entries.append({"url": url})
        if not supplied:
            try:
                import yt_dlp
            except ImportError as exc:
                raise RuntimeError("Automatic discovery requires yt-dlp. Install backend requirements.") from exc
            seen = set()
            keywords = [w for w in re.findall(r"[a-z]+", topic.lower())
                        if w not in {"top", "best", "ranking", "moments", "moment", "craziest", "funniest", "videos"}]
            try:
                for query in cls.generate_search_queries(topic):
                    check_cancelled()
                    if len(entries) >= MAX_SEARCH_RESULTS:
                        break
                    for entry in cls.search_public_shorts(query):
                        if entry["id"] in seen:
                            continue
                        seen.add(entry["id"])
                        title = entry.get("title", "").lower()
                        if any(word in title for word in ("compilation", "ranking", "ranked", "top 10", "top 5", "top 7")) or re.search(r"\bbest\b.*\bmoments\b|\btop\s*\d+\b", title):
                            continue
                        if keywords and not any(w.rstrip("s") in title for w in keywords):
                            continue
                        entries.append({"url": entry["url"]})
                        if len(entries) >= MAX_SEARCH_RESULTS:
                            break
            except Exception as exc:
                check_cancelled()
                if not entries:
                    raise RuntimeError("Video search failed. Check your connection or supply source links/uploads. " + str(exc)[-300:]) from exc
        for idx, entry in enumerate(entries[:MAX_SEARCH_RESULTS]):
            check_cancelled()
            if len(candidates) >= min(MAX_SOURCE_VIDEOS, count + 3):
                break
            try:
                if entry.get("file_path"):
                    path = Path(entry["file_path"])
                    metadata = dict(entry)
                else:
                    path, metadata = SourceIngestion.download_video(entry["url"], temp_dir / "downloads" / f"candidate_{idx}.mp4")
                info = SourceIngestion.verify(path)
                candidates.append({**metadata, "id": f"source_{idx}", "file_path": str(path),
                                   "duration": info["duration"]})
            except InterruptedError:
                raise
            except Exception as exc:
                failures.append(str(exc))
        if len(candidates) < count:
            detail = failures[-1] if failures else "Too few matching single-clip videos were found."
            raise ValueError(f"Found {len(candidates)} usable sources; Top {count} needs {count} distinct videos. Add source URLs/uploads or choose a smaller count. {detail}")
        return candidates

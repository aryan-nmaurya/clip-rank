"""Modular discovery providers that normalize every result to one candidate schema.

Providers only *find* candidates. They never download media and never decide that a
source may be reused: ``rights_status`` starts as ``unverified`` unless a provider has
verifiable licence evidence, and the RightsGate makes the production decision.
"""
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

from app.core.config import STORAGE_DIR

# One raw event is rarely longer than this; longer videos are overwhelmingly compilations.
MIN_EVENT_SECONDS = 3
MAX_EVENT_SECONDS = 150

RIGHTS_STATUSES = ("unverified", "licensed", "owned", "blocked")
SUPERLATIVES = (
    r"craziest|best|worst|insane|unbelievable|funniest|greatest|amazing|incredible|epic|luckiest|"
    r"scariest|most|ever|impossible|ultimate|wildest|biggest"
)
# Query endings that describe a single captured event rather than an edited list.
EVENT_SUFFIXES = ("caught on camera", "close call", "one take", "original clip", "POV")


def normalize_candidate(entry: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Shared schema. Legacy keys (``platform`` etc.) stay so existing callers keep working."""
    published = entry.get("published_at")
    if not published and isinstance(entry.get("timestamp"), (int, float)):
        published = datetime.fromtimestamp(entry["timestamp"], timezone.utc).isoformat()
    rights = entry.get("rights_status") or "unverified"
    if rights not in RIGHTS_STATUSES:
        rights = "unverified"
    views = entry.get("view_count")
    candidate = {
        **entry,
        "id": str(entry.get("id") or entry.get("url") or ""),
        "source": source,
        "platform": entry.get("platform") or source,
        "title": str(entry.get("title") or "Untitled video"),
        "url": entry.get("url"),
        "creator": entry.get("creator") or entry.get("channel") or entry.get("uploader") or "",
        "published_at": published,
        "duration": entry.get("duration"),
        "engagement_signals": {"views": views, "likes": entry.get("like_count"),
                               **(entry.get("engagement_signals") or {})},
        "description": str(entry.get("description") or "")[:1000],
        "rights_status": rights,
        "source_metadata": entry.get("source_metadata") or {
            k: entry.get(k) for k in ("channel_id", "channel_url", "uploader_id", "license") if entry.get(k)},
    }
    candidate["opportunity_class"] = classify_opportunity(candidate)
    return candidate


def classify_opportunity(candidate: Dict[str, Any]) -> str:
    """TRENDING = already proven reach. EMERGING = strong event from a small source.

    Reach alone never selects a video; this label only records why it was considered
    so the content director can balance proven and undiscovered material.
    """
    views = (candidate.get("engagement_signals") or {}).get("views")
    if not isinstance(views, (int, float)):
        return "UNKNOWN"
    return "TRENDING" if views >= 500_000 else "EMERGING" if views < 100_000 else "ESTABLISHED"


def duration_reason(entry: Dict[str, Any]) -> Optional[str]:
    duration = entry.get("duration")
    if not isinstance(duration, (int, float)) or duration <= 0:
        return None  # unknown until downloaded; verified again after ingest
    if duration < MIN_EVENT_SECONDS:
        return f"Source is only {duration:.0f}s long."
    if duration > MAX_EVENT_SECONDS:
        return f"A {duration:.0f}s video is most likely a compilation, not a single raw event."
    return None


def build_queries(topic: str) -> List[str]:
    """Search for the underlying event, not for ranking-style pages.

    'Top 5 Craziest Parkour Saves' must not search the word 'craziest' because that
    surfaces ranking compilations. The first query keeps the legacy singular form.
    """
    base = re.sub(r"\b(?:rankings?|top|moments?|clips?|videos?)\b|\b\d+\b", "", topic, flags=re.I)
    base = re.sub(rf"\b(?:{SUPERLATIVES})\b", "", base, flags=re.I)
    base = " ".join(base.split()) or topic.strip()
    individual = re.sub(r"\bfails\b", "fail", base, flags=re.I)
    individual = re.sub(r"\bsaves\b", "save", individual, flags=re.I)
    queries = [individual, f"{individual} original clip", f"{individual} single attempt", f"{base} caught on camera"]
    queries += [f"{individual} {suffix}" for suffix in EVENT_SUFFIXES if suffix not in queries[-1]]
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


class Provider:
    name = "base"
    requires_network = True

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def candidates(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        return [normalize_candidate(e, self.name) for e in self.search(query, limit)]


class YouTubeProvider(Provider):
    name = "youtube"

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        from app.sources.discovery import SourceDiscovery
        return SourceDiscovery.search_public_shorts(query)[:limit]


class FlatSearchProvider(Provider):
    """yt-dlp flat extraction: duration, views and creator without downloading."""
    name = "ytdlp"
    prefix = "ytsearch"

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        import yt_dlp
        options = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "socket_timeout": 20}
        with yt_dlp.YoutubeDL(options) as downloader:
            result = downloader.extract_info(f"{self.prefix}{limit}:{query}", download=False) or {}
        entries = []
        for item in result.get("entries") or []:
            if not item or not item.get("id") or not item.get("title"):
                continue
            url = item.get("url") or f"https://www.youtube.com/watch?v={item['id']}"
            entries.append({"id": item["id"], "title": item["title"], "url": url, "duration": item.get("duration"),
                            "view_count": item.get("view_count"), "creator": item.get("channel") or item.get("uploader"),
                            "channel_id": item.get("channel_id"), "channel_url": item.get("channel_url"),
                            "description": item.get("description"), "timestamp": item.get("timestamp")})
        return entries


class WebProvider(Provider):
    """Public-index search for video pages on Reddit, TikTok, Instagram and Vimeo."""

    def __init__(self, platform: str):
        self.name = platform

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        from app.sources.web_search import WebVideoSearch
        return WebVideoSearch.search(query, self.name)[:limit]


class RSSProvider(Provider):
    """Operator-configured feeds (e.g. a licensed footage agency) - never guessed URLs."""
    name = "rss"

    def __init__(self, feed_url: str, rights_status: str = "unverified"):
        if urlparse(feed_url).scheme not in ("http", "https"):
            raise ValueError("RSS feeds must be HTTP(S) URLs.")
        self.feed_url, self.rights_status = feed_url, rights_status

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        response = requests.get(self.feed_url, timeout=20, headers={"User-Agent": "ClipRank/2.0"})
        response.raise_for_status()
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
        found = []
        for item in ET.fromstring(response.content).iter("item"):
            title, link = item.findtext("title", ""), item.findtext("link", "")
            blob = f"{title} {item.findtext('description', '')}".lower()
            if link and (not terms or any(t in blob for t in terms)):
                found.append({"id": link, "title": title, "url": link, "description": item.findtext("description"),
                              "published_at": item.findtext("pubDate"), "rights_status": self.rights_status})
        return found[:limit]


class LocalMediaProvider(Provider):
    """User-owned or already-acquired files. Rights come from the sidecar, never the filename."""
    name = "local"
    requires_network = False
    EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
        found = []
        for path in sorted(self.folder.rglob("*")) if self.folder.is_dir() else []:
            if path.suffix.lower() not in self.EXTENSIONS or not path.is_file():
                continue
            sidecar = path.with_suffix(".rights.json")
            rights = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
            if terms and not any(t in path.stem.lower() for t in terms):
                continue
            found.append({"id": str(path), "title": path.stem.replace("_", " "), "url": None, "file_path": str(path),
                          "creator": rights.get("creator", ""), "rights_status": rights.get("rights_status", "unverified"),
                          "source_metadata": rights})
        return found[:limit]


class LicensedMediaProvider(LocalMediaProvider):
    """A local library whose every file has a ``<name>.rights.json`` licence record."""
    name = "licensed"

    def __init__(self, folder: Optional[Path] = None):
        super().__init__(folder or STORAGE_DIR / "licensed")

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        # A licensed library only offers files that carry proof; the rest are not candidates.
        return [e for e in super().search(query, limit) if e["rights_status"] in ("licensed", "owned")
                and (e["source_metadata"].get("proof_reference") or e["source_metadata"].get("license"))]


def providers_for(platforms: Iterable[str]) -> List[Provider]:
    chosen: List[Provider] = []
    for platform in platforms:
        chosen.append(YouTubeProvider() if platform == "youtube" else WebProvider(platform))
    return chosen

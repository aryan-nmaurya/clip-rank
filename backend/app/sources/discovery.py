"""Search real videos and retain provenance for every downloaded candidate."""
import re
import hashlib
import json
import requests
from urllib.parse import urlencode
from pathlib import Path
from typing import List, Dict, Any
from itertools import zip_longest
from app.core.config import MAX_SEARCH_RESULTS, MAX_SOURCE_VIDEOS
from app.core.runtime import check_cancelled
from app.sources.ingestion import SourceIngestion
from app.sources.ranking_policy import RankingSourcePolicy
from app.sources.web_search import WebVideoSearch, PLATFORMS
from app.sources.errors import RankedSourceRejected
from app.sources.providers import build_queries, duration_reason, normalize_candidate, FlatSearchProvider


_CC_OK = re.compile(r"creative commons attribution|cc[\s-]*by", re.I)
_CC_RESTRICTED = re.compile(r"non-?commercial|no\s*derivatives|cc[\s-]*by[\s-]*(?:nc|nd)", re.I)


def is_reusable_cc(license_text):
    """Commercial, derivative-friendly Creative Commons only."""
    text = str(license_text or "")
    return bool(_CC_OK.search(text)) and not _CC_RESTRICTED.search(text)


def note_rejection(rejections, record):
    """One entry per URL: the same video found by several queries is reported once."""
    if record.get("url") and any(r.get("url") == record["url"] for r in rejections):
        return
    rejections.append(record)


class SourceDiscovery:
    @staticmethod
    def generate_search_queries(topic: str) -> List[str]:
        return build_queries(topic)

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
                video = node.get("videoRenderer")
                if lockup:
                    command = lockup.get("onTap", {}).get("innertubeCommand", {})
                    video_id = command.get("reelWatchEndpoint", {}).get("videoId")
                    title = lockup.get("overlayMetadata", {}).get("primaryText", {}).get("content")
                    title = title or re.sub(r", [\d.,]+ [\w ]*views.*$", "", lockup.get("accessibilityText", ""))
                elif reel:
                    video_id = reel.get("videoId")
                    title = reel.get("headline", {}).get("simpleText") or " ".join(r.get("text", "") for r in reel.get("headline", {}).get("runs", []))
                elif video:
                    video_id = video.get("videoId")
                    title = " ".join(r.get("text", "") for r in video.get("title", {}).get("runs", []))
                else:
                    video_id = None
                if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) and video_id not in seen:
                    seen.add(video_id)
                    url = f"https://www.youtube.com/watch?v={video_id}" if video and not (lockup or reel) else f"https://www.youtube.com/shorts/{video_id}"
                    entries.append({"id": video_id, "title": title or "Short video", "url": url})
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        visit(data)
        return entries

    CC_FILTER = "EgIwAQ%3D%3D"   # YouTube's "Creative Commons" search filter

    @classmethod
    def search_public_shorts(cls, query, cc_only=False):
        """Shorts-filtered page results merged with yt-dlp metadata (duration, views, creator).

        ``cc_only`` uses YouTube's Creative Commons filter instead (no Shorts filter exists alongside it;
        the duration prefilter removes long videos and the licence is re-checked after download).
        """
        raw_query=f'{query} -ranking -compilation -countdown -top5 -top10'
        if cc_only:
            url = "https://www.youtube.com/results?" + urlencode({"search_query": raw_query}) + "&sp=" + cls.CC_FILTER
            response = requests.get(url, timeout=25)
            response.raise_for_status()
            return cls.parse_search_page(response.text)
        url = "https://www.youtube.com/results?" + urlencode({"search_query": raw_query, "sp": "EgIQCQ=="})
        scraped, error = [], None
        try:
            response = requests.get(url, timeout=25)
            response.raise_for_status()
            scraped = cls.parse_search_page(response.text)
        except (requests.RequestException, ValueError) as exc:
            error = exc
        try:
            flat = FlatSearchProvider().search(query, 20)
        except Exception as exc:  # yt-dlp raises broad DownloadError subclasses; the scrape may still have results
            if not scraped:
                raise ValueError(f"YouTube search failed: {str(error or exc)[-250:]}") from exc
            flat = []
        merged, by_id = [], {}
        for entry in [*flat, *scraped]:
            if entry["id"] in by_id:
                by_id[entry["id"]].update({k: v for k, v in entry.items() if v and not by_id[entry["id"]].get(k)})
                continue
            by_id[entry["id"]] = entry
            merged.append(entry)
        return merged

    @classmethod
    def discover_candidate_videos(cls, topic: str, count: int, temp_dir: Path,
                                  source_urls=None, source_files=None, source_titles=None,
                                  source_platforms=None, rejections=None, min_candidates=None, used_keys=None, source_provenance=None,
                                  exclude_urls=None, round_index=0, allow_partial=False, cc_only=False) -> List[Dict[str, Any]]:
        candidates = []
        failures = []
        entries = []
        warnings = []
        rejections = rejections if rejections is not None else []
        supplied = bool(source_urls or source_files)
        exclude_urls = set(exclude_urls or ())
        prefix = f'r{round_index}_' if round_index else ''
        if supplied and round_index:
            return []  # supplied links/uploads cannot be widened
        for file_idx, path in enumerate(source_files or []):
            check_cancelled()
            source = Path(path)
            info = SourceIngestion.verify(source)
            with source.open("rb") as source_handle:
                source_hash = hashlib.file_digest(source_handle, "sha256").hexdigest()
            original=(source_provenance or {}).get(str(source),{})
            entries.append({**original,"file_path": str(source), "title": source_titles[file_idx] if source_titles and file_idx < len(source_titles) else original.get('title') or source.stem,
                            "source_id":original.get('source_id') or source_hash,"source_sha256":source_hash,
                            "duration": info["duration"], "url": original.get('url'), "view_count": original.get('view_count',0), "like_count":original.get('like_count',0)})
        for url in source_urls or []:
            entries.append({"url": url})
        if not supplied:
            platforms = ["youtube"] if cc_only else (source_platforms or ["youtube", "reddit", "dailymotion"])
            if any(p not in PLATFORMS for p in platforms):
                raise ValueError("Unknown source platform.")
            groups = []
            all_queries = cls.generate_search_queries(topic)
            # Each round searches a fresh window of queries so widening finds different footage.
            queries = all_queries[round_index * 3: round_index * 3 + 4]
            if not queries:
                return []
            for platform in dict.fromkeys(platforms):
                query_groups = []
                for query in (queries if platform == "youtube" else queries[:1]):
                    check_cancelled()
                    try:
                        found = (cls.search_public_shorts(query, cc_only=True) if cc_only else cls.search_public_shorts(query)) if platform == "youtube" else WebVideoSearch.search(query, platform)
                        query_groups.append([])
                        for entry in found:
                            entry = normalize_candidate(entry, platform)
                            reason = RankingSourcePolicy.metadata_reason(entry) or duration_reason(entry)
                            if entry.get("url") in exclude_urls:
                                continue
                            if reason:
                                note_rejection(rejections, {"url": entry["url"], "title": entry.get("title"), "reason": reason})
                            else:
                                query_groups[-1].append({**entry, "platform": platform})
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        check_cancelled()
                        warnings.append(f"{platform.title()} search: {str(exc)[-250:]}")
                # Mix queries as well as sites so one popular repost cluster cannot consume the budget.
                groups.extend(query_groups)
            # Interleave platforms so YouTube cannot consume the whole download budget.
            seen = set()
            for row in zip_longest(*groups):
                for entry in row:
                    if entry and entry["url"] not in seen:
                        seen.add(entry["url"])
                        entries.append(entry)
            # Clips whose length suggests one event come first; unknown lengths follow, odd lengths last.
            entries.sort(key=lambda e: 0 if 8 <= (e.get("duration") or 0) <= 75 else 1 if not e.get("duration") else 2)
            entries = entries[:MAX_SEARCH_RESULTS]
            if not entries and round_index == 0:
                raise ValueError("No individual videos were found. Try public source links or uploads. " + " ".join(warnings)[-600:])
        seen_content = set()
        for idx, entry in enumerate(entries[:MAX_SEARCH_RESULTS]):
            check_cancelled()
            if len(candidates) >= (min(MAX_SOURCE_VIDEOS,max(min_candidates*3,min_candidates+4)) if min_candidates else min(MAX_SOURCE_VIDEOS,count*2+4)):
                break
            try:
                from app.sources.reuse import source_keys
                if used_keys and source_keys(entry).intersection(used_keys):
                    rejections.append({'url':entry.get('url'),'title':entry.get('title'),'reason':'Already used in an approved Short.'})
                    continue
                if not supplied:
                    from app.sources import verdicts
                    earlier = verdicts.recall(source_keys(entry), topic)
                    if earlier:
                        note_rejection(rejections, {'url':entry.get('url'),'title':entry.get('title'),'reason':'Judged unsuitable for this topic earlier: '+earlier})
                        continue
                reason = RankingSourcePolicy.metadata_reason(entry)
                if reason:
                    rejections.append({"url": entry.get("url"), "title": entry.get("title"), "reason": reason})
                    continue
                if entry.get("file_path"):
                    path = Path(entry["file_path"])
                    metadata = dict(entry)
                    metadata["platform"] = "Upload"
                else:
                    path, metadata = SourceIngestion.download_video(entry["url"], temp_dir / "downloads" / f"{prefix}candidate_{idx}.mp4", reject_rankings=True)
                reason = RankingSourcePolicy.metadata_reason(metadata)
                if reason:
                    rejections.append({"url": metadata.get("url"), "title": metadata.get("title"), "reason": reason})
                    continue
                if cc_only and not is_reusable_cc(metadata.get("license")):
                    rejections.append({"url": metadata.get("url"), "title": metadata.get("title"),
                                       "reason": f"Not under a reusable Creative Commons licence (found: {metadata.get('license') or 'none'})."})
                    continue
                info = SourceIngestion.verify(path)
                with path.open("rb") as handle:
                    fingerprint = hashlib.file_digest(handle, "sha256").hexdigest()
                metadata['source_sha256']=fingerprint
                if used_keys and source_keys(metadata).intersection(used_keys):
                    rejections.append({'url':metadata.get('url'),'title':metadata.get('title'),'reason':'This footage was already used in another approved Short.'})
                    continue
                if fingerprint in seen_content:
                    rejections.append({"url": metadata.get("url"), "title": metadata.get("title"), "reason": "Duplicate source footage."})
                    continue
                seen_content.add(fingerprint)
                candidates.append({**metadata, "id": f"{prefix}source_{idx}", "file_path": str(path),
                                   "duration": info["duration"], "discovery_warnings": warnings})
            except InterruptedError:
                raise
            except RankedSourceRejected as exc:
                rejections.append({"url": entry.get("url"), "title": exc.title or entry.get("title"), "reason": str(exc)})
            except Exception as exc:
                failures.append(str(exc))
        if failures:
            warnings.append(f"Skipped {len(failures)} unavailable source videos while finding footage.")
        required=min_candidates or count
        if allow_partial:
            required = 0
        if len(candidates) < required:
            detail = failures[-1] if failures else "Too few matching individual videos were found."
            raise ValueError(f"Found {len(candidates)} usable sources; this production needs {required} distinct unused videos. Excluded {len(rejections)} ranking/duplicate sources. Add raw source URLs/uploads or choose a smaller count. {detail}")
        (temp_dir/'source_manifest.json').write_text(json.dumps(candidates,ensure_ascii=False,default=str))
        return candidates

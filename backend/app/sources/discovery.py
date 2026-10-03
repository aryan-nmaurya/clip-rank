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


class SourceDiscovery:
    @staticmethod
    def generate_search_queries(topic: str) -> List[str]:
        base = re.sub(r"\b(?:rankings?|best|top|moments?|clips?|videos?)\b|\b\d+\b", "", topic, flags=re.I)
        base = " ".join(base.split())
        individual = re.sub(r'\bfails\b','fail',base,flags=re.I)
        individual = re.sub(r'\bsaves\b','save',individual,flags=re.I)
        return list(dict.fromkeys([individual, f"{individual} original clip", f"{individual} single attempt", f"{base} caught on camera"]))

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

    @classmethod
    def search_public_shorts(cls, query):
        raw_query=f'{query} -ranking -compilation -countdown -top5 -top10'
        url = "https://www.youtube.com/results?" + urlencode({"search_query": raw_query, "sp": "EgIQCQ=="})
        response = requests.get(url, timeout=25)
        response.raise_for_status()
        return cls.parse_search_page(response.text)

    @classmethod
    def discover_candidate_videos(cls, topic: str, count: int, temp_dir: Path,
                                  source_urls=None, source_files=None, source_titles=None,
                                  source_platforms=None, rejections=None, min_candidates=None, used_keys=None, source_provenance=None) -> List[Dict[str, Any]]:
        candidates = []
        failures = []
        entries = []
        warnings = []
        rejections = rejections if rejections is not None else []
        supplied = bool(source_urls or source_files)
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
            platforms = source_platforms or ["youtube", "reddit", "dailymotion"]
            if any(p not in PLATFORMS for p in platforms):
                raise ValueError("Unknown source platform.")
            groups = []
            queries = cls.generate_search_queries(topic)
            for platform in dict.fromkeys(platforms):
                query_groups = []
                for query in (queries if platform == "youtube" else queries[:1]):
                    check_cancelled()
                    try:
                        found = cls.search_public_shorts(query) if platform == "youtube" else WebVideoSearch.search(query, platform)
                        query_groups.append([])
                        for entry in found:
                            reason = RankingSourcePolicy.metadata_reason(entry)
                            if reason:
                                rejections.append({"url": entry["url"], "title": entry.get("title"), "reason": reason})
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
            entries = entries[:MAX_SEARCH_RESULTS]
            if not entries:
                raise ValueError("No individual videos were found. Try public source links or uploads. " + " ".join(warnings)[-600:])
        seen_content = set()
        for idx, entry in enumerate(entries[:MAX_SEARCH_RESULTS]):
            check_cancelled()
            if len(candidates) >= (MAX_SOURCE_VIDEOS if min_candidates else min(MAX_SOURCE_VIDEOS,count*2+4)):
                break
            try:
                from app.sources.reuse import source_keys
                if used_keys and source_keys(entry).intersection(used_keys):
                    rejections.append({'url':entry.get('url'),'title':entry.get('title'),'reason':'Already used in an approved Short.'})
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
                    path, metadata = SourceIngestion.download_video(entry["url"], temp_dir / "downloads" / f"candidate_{idx}.mp4", reject_rankings=True)
                reason = RankingSourcePolicy.metadata_reason(metadata)
                if reason:
                    rejections.append({"url": metadata.get("url"), "title": metadata.get("title"), "reason": reason})
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
                candidates.append({**metadata, "id": f"source_{idx}", "file_path": str(path),
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
        if len(candidates) < required:
            detail = failures[-1] if failures else "Too few matching individual videos were found."
            raise ValueError(f"Found {len(candidates)} usable sources; this production needs {required} distinct unused videos. Excluded {len(rejections)} ranking/duplicate sources. Add raw source URLs/uploads or choose a smaller count. {detail}")
        (temp_dir/'source_manifest.json').write_text(json.dumps(candidates,ensure_ascii=False,default=str))
        return candidates

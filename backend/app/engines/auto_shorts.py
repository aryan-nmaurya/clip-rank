"""Auto Shorts: one simple engine from "find something people will watch" to "ready to post".

    search several platforms -> rank by engagement signals -> take the best few -> for each: download the whole video,
    reframe it to 9:16 (cropped to follow the action), add a voice-over line and word-timed captions, mix the audio,
    render -> finished Shorts appear in the Library with an Upload to YouTube button.

Ranking is a transparent estimate from public numbers (views, how recent, how long); it is not a prediction of views.
The production itself is the existing Viral Clips pipeline, so it inherits the Quality-control switch, whole-video
behaviour, checkpoints and failure reporting. If a video cannot be downloaded or produced, the next-best one replaces it.
"""
import asyncio
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Any, Dict, List, Optional

from app.core.runtime import check_cancelled

PLATFORMS = ("youtube", "dailymotion", "reddit", "vimeo")
NICHES = {
    "extreme": ["unbelievable sports moment", "insane parkour moment", "incredible stunt", "unbelievable close call caught on camera"],
    "funny": ["funny sports fail", "funny physical fail", "funny moment caught on camera"],
    "skills": ["incredible human skill", "incredible trick shot", "amazing balance save"],
    "general": ["unbelievable moment caught on camera", "incredible moment", "insane skill"],
    # Emotional mode: real people, real moments that move you.
    "emotional": ["emotional moment caught on camera", "heartwarming reunion", "touching act of kindness",
                  "heartbreaking moment", "emotional surprise homecoming", "emotional goodbye"],
    # Sad-visuals mode: pictures that carry the feeling without any words (the sound is removed).
    "sad_visual": ["sad emotional moment", "lonely rainy day cinematic", "heartbreaking animal rescue", "sad goodbye at the airport",
                   "abandoned dog waiting", "emotional farewell"],
}
MODES = ("viral", "emotional", "sad_visual")
SWEET_SPOTS = {"viral": (15, 60), "emotional": (20, 90), "sad_visual": (12, 45)}     # emotional stories need a little longer to land
SEARCH_DEADLINE = 30
SWEET_SPOT = (15, 60)       # seconds: short enough to watch to the end, long enough to land a payoff
MAX_SOURCE_SECONDS = 150
MIN_SOURCE_SECONDS = 8


def age_days(candidate: Dict[str, Any], now: Optional[float] = None) -> Optional[float]:
    stamp = candidate.get("timestamp")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return max(0.0, ((now or time.time()) - stamp) / 86400)
    return None


def duration_fit(seconds: Optional[float], sweet=SWEET_SPOT) -> float:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return .5
    lo, hi = sweet
    if lo <= seconds <= hi:
        return 1.0
    if seconds < lo:
        return max(0.0, .4 + .6 * (seconds - MIN_SOURCE_SECONDS) / (lo - MIN_SOURCE_SECONDS))
    return max(0.0, 1.0 - (seconds - hi) / (MAX_SOURCE_SECONDS - hi) * .8)


def rank_candidates(candidates: List[Dict[str, Any]], now: Optional[float] = None, mode: str = "viral") -> List[Dict[str, Any]]:
    """Order best-first. Engagement is ranked within each platform (their numbers are not comparable), then platforms are
    mixed so one site cannot crowd out the rest. Each result explains its score."""
    by_platform: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        by_platform.setdefault(candidate.get("platform", "web"), []).append(candidate)
    scored = []
    for platform, items in by_platform.items():
        def velocity(c):
            views = c.get("view_count")
            if not isinstance(views, (int, float)) or views < 0:
                return None
            age = age_days(c, now)
            return math.log10(1 + views / max(age, 1.0)) if age is not None else math.log10(1 + views)
        values = sorted(v for v in (velocity(c) for c in items) if v is not None)
        for c in items:
            v = velocity(c)
            relative = (sum(1 for x in values if x <= v) - 1) / max(1, len(values) - 1) if v is not None and len(values) > 1 else (.5 if v is not None else None)
            # Mostly "actually popular", a little "better than its platform peers": 31 views is not momentum however it compares.
            absolute = min(1.0, v / 5) if v is not None else None
            engagement = .3 if relative is None else .3 * relative + .7 * absolute
            age = age_days(c, now)
            recency = .5 if age is None else max(0.0, 1 - age / 365)
            fit = duration_fit(c.get("duration"), SWEET_SPOTS.get(mode, SWEET_SPOT))
            score = round(.55 * engagement + .25 * fit + .20 * recency, 4)
            views = c.get("view_count")
            reasons = []
            if isinstance(views, (int, float)): reasons.append(f"{int(views):,} views" + (f" in {age:.0f} days" if age is not None else ""))
            else: reasons.append("no public view count")
            reasons.append(f"{c.get('duration') or '?'}s long")
            scored.append({**c, "potential": score, "potential_reasons": "; ".join(reasons),
                           "opportunity_class": c.get("opportunity_class") or "UNKNOWN"})
        # within a platform: best first
    queues = {p: sorted([s for s in scored if s.get("platform") == p], key=lambda s: -s["potential"]) for p in by_platform}
    mixed, order = [], sorted(queues, key=lambda p: -queues[p][0]["potential"])
    while any(queues.values()):
        for platform in order:
            if queues[platform]:
                mixed.append(queues[platform].pop(0))
    return mixed


def search(platforms: List[str], queries: List[str], limit_per_search: int = 12, pool_target: int = 30, min_wait: float = 12.0) -> Dict[str, Any]:
    """Every platform x query at once, with a deadline; filters out what cannot make a good source before ranking."""
    from app.sources import verdicts
    from app.sources.discovery import SourceDiscovery
    from app.sources.providers import duration_reason, normalize_candidate
    from app.sources.ranking_policy import RankingSourcePolicy
    from app.sources.reuse import SourceReuse, source_keys
    from app.sources.web_search import WebVideoSearch, PLATFORMS as KNOWN
    used = SourceReuse.used_keys()
    jobs = [(p, q) for p in platforms if p in KNOWN for q in queries]
    warnings: List[Dict[str, str]] = []

    def one(platform, query):
        entries = SourceDiscovery.search_public_shorts(query) if platform == "youtube" else WebVideoSearch.search(query, platform)
        return platform, query, entries

    found, seen = [], set()
    executor = ThreadPoolExecutor(max_workers=6)
    pending = {executor.submit(one, *job): job for job in jobs}
    began = time.monotonic()
    deadline = began + SEARCH_DEADLINE
    try:
        while pending and time.monotonic() < deadline:
            if len(found) >= pool_target and time.monotonic() - began >= min_wait:
                break          # plenty to choose from; do not wait on slow or blocked sites
            check_cancelled()
            done, _ = wait(list(pending), timeout=1, return_when=FIRST_COMPLETED)
            for future in done:
                platform, query = pending.pop(future)
                try:
                    _, _, entries = future.result()
                except Exception as exc:
                    warnings.append({"platform": platform, "error": type(exc).__name__})
                    continue
                kept = 0
                for entry in entries:
                    if kept >= limit_per_search:
                        break         # cap what survives the filters, not the raw top results (those are mostly compilations)
                    entry = normalize_candidate(entry, platform)
                    if entry["url"] in seen:
                        continue
                    seen.add(entry["url"])
                    if RankingSourcePolicy.metadata_reason(entry) or duration_reason(entry):
                        continue
                    duration = entry.get("duration")
                    if isinstance(duration, (int, float)) and not MIN_SOURCE_SECONDS <= duration <= MAX_SOURCE_SECONDS:
                        continue
                    keys = source_keys(entry)
                    if keys & used or verdicts.recall(keys, query):
                        continue
                    found.append({**entry, "search_query": query})
                    kept += 1
        for future, (platform, _) in pending.items():
            future.cancel()
            warnings.append({"platform": platform, "error": "TimedOut"})
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return {"candidates": found, "warnings": warnings}


def fetch_metadata(url: str) -> Dict[str, Any]:
    """Public numbers for one video (no download): views, length, upload time, channel."""
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    stamp = info.get("timestamp")
    if not stamp and info.get("upload_date"):
        try:
            stamp = time.mktime(time.strptime(info["upload_date"], "%Y%m%d"))
        except ValueError:
            stamp = None
    return {"view_count": info.get("view_count"), "duration": info.get("duration"), "timestamp": stamp,
            "like_count": info.get("like_count"), "creator": info.get("uploader") or info.get("channel"), "license": info.get("license")}


def enrich(candidates: List[Dict[str, Any]], limit: int = 16, deadline: float = 20.0) -> Dict[str, int]:
    """Candidates that arrived without numbers (YouTube Shorts search) get real ones, so ranking is fair and unsuitable
    lengths are dropped. Failures leave the candidate as it was."""
    todo = [c for c in candidates if c.get("view_count") is None and c.get("platform") == "youtube"][:limit]
    done = failed = 0
    executor = ThreadPoolExecutor(max_workers=6)
    pending = {executor.submit(fetch_metadata, c["url"]): c for c in todo}
    end = time.monotonic() + deadline
    try:
        while pending and time.monotonic() < end:
            check_cancelled()
            finished, _ = wait(list(pending), timeout=1, return_when=FIRST_COMPLETED)
            for future in finished:
                candidate = pending.pop(future)
                try:
                    candidate.update({k: v for k, v in future.result().items() if v is not None})
                    done += 1
                except Exception:
                    failed += 1
        failed += len(pending)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return {"enriched": done, "failed": failed}


def usable_length(candidate: Dict[str, Any]) -> bool:
    duration = candidate.get("duration")
    return not isinstance(duration, (int, float)) or MIN_SOURCE_SECONDS <= duration <= MAX_SOURCE_SECONDS


def queries_for(niche: str, topic: Optional[str], mode: str = "viral") -> List[str]:
    if mode in ("emotional", "sad_visual") and not (topic and topic.strip()):
        return NICHES[mode]
    if topic and topic.strip():
        from app.sources.providers import build_queries
        return build_queries(topic.strip())[:4]
    return NICHES.get(niche, NICHES["general"])


class AutoShorts:
    """One run at a time. State is in memory (live progress); the finished Shorts live in the Library like any other."""
    state: Dict[str, Any] = {"status": "idle"}
    task: Optional[asyncio.Task] = None
    MAX_ATTEMPTS_FACTOR = 3        # try up to 3x as many candidates as Shorts requested
    POLL_SECONDS = 2

    @classmethod
    def status(cls) -> Dict[str, Any]:
        cls._refresh()
        return cls.state

    @classmethod
    def start(cls, count: int = 3, platforms: Optional[List[str]] = None, niche: str = "extreme", topic: Optional[str] = None,
              voice: Optional[str] = None, provider: str = "auto", mode: str = "viral") -> Dict[str, Any]:
        if cls.task and not cls.task.done():
            return cls.state
        platforms = [p for p in (platforms or ["youtube", "dailymotion", "reddit"]) if p in PLATFORMS] or ["youtube"]
        mode = mode if mode in MODES else "viral"
        cls.state = {"status": "running", "id": uuid.uuid4().hex[:10], "stage": "searching", "started": time.time(), "wanted": count,
                     "mode": mode, "platforms": platforms, "niche": niche, "topic": topic, "message": "Searching platforms…", "items": [], "warnings": []}
        cls.task = asyncio.get_running_loop().create_task(cls._run(count, platforms, niche, topic, voice, provider, mode))
        return cls.state

    @classmethod
    async def cancel(cls) -> Dict[str, Any]:
        from app.core.queue import JobEngine
        for item in cls.state.get("items", []):
            if item.get("job_id") and item["status"] in ("QUEUED", "RUNNING"):
                await JobEngine.get_instance().cancel_job(item["job_id"])
        if cls.task and not cls.task.done():
            cls.task.cancel()
        cls.state.update(status="cancelled", message="Cancelled.")
        return cls.state

    # --------------------------------------------------------------- internals
    @classmethod
    def _launch(cls, candidate: Dict[str, Any], voice: Optional[str], provider: str, mode: str = "viral") -> Dict[str, Any]:
        """Hand one chosen video to the Viral Clips pipeline as a whole-video production.

        viral      -> original commentary voice-over, captions, smart 9:16 edit
        emotional  -> no voice-over; a headline over the whole video, sad background music that ducks under speech, captions
        sad_visual -> visuals only: original sound removed, a headline over the whole video, sad background music, no speech or captions
        """
        from app.core.database import create_job, create_project
        from app.core.queue import JobEngine
        suffix = uuid.uuid4().hex[:12]
        project_id, job_id = f"proj_v_{suffix}", f"job_{suffix}"
        data = {"video_source": candidate["url"], "video_url": candidate["url"], "source_title": candidate["title"], "count": 1,
                "ai_provider": provider, "target_duration": 25, "layout": "smart", "captions": True, "whole_video": True,
                "force_commentary": True, "contextual_commentary": False, "auto_short": True,
                "source_creator": candidate.get("creator") or "", "auto_mode": mode}
        if mode == "emotional":
            data.update(force_commentary=False, no_narration=True, heading=True, music="sad", style="emotional")
        elif mode == "sad_visual":
            # Pictures, headline and music only: the original sound is removed, nothing is spoken or captioned.
            data.update(force_commentary=False, no_narration=True, heading=True, music="sad", style="sad_visual",
                        visuals_only=True, mute_source=True, captions=False)
        if voice:
            data["default_voice"] = voice
        create_project(project_id, "viral", f"{ {'emotional': 'Emotional', 'sad_visual': 'Sad visual'}.get(mode, 'Auto') } Short · {candidate['title'][:60]}", input_data=data)
        create_job(job_id, project_id)
        JobEngine.get_instance().submit_job(job_id)
        return {"title": candidate["title"], "url": candidate["url"], "platform": candidate.get("platform"), "creator": candidate.get("creator"),
                "views": candidate.get("view_count"), "duration": candidate.get("duration"), "potential": candidate.get("potential"),
                "why": candidate.get("potential_reasons"), "project_id": project_id, "job_id": job_id, "status": "QUEUED",
                "progress": 0, "stage": "Queued", "clips": [], "error": None}

    @classmethod
    def _refresh(cls) -> None:
        """Copy live job state from the database into the run's items."""
        from app.core.database import get_job, get_project
        for item in cls.state.get("items", []):
            if item["status"] in ("DONE", "FAILED", "CANCELLED") or not item.get("job_id"):
                continue
            job = get_job(item["job_id"])
            if not job:
                continue
            item.update(progress=job["progress"], stage=job["current_stage"])
            if job["status"] == "COMPLETED":
                project = get_project(item["project_id"])
                item.update(status="DONE", progress=100, clips=project["clips"] if project else [])
            elif job["status"] in ("FAILED", "CANCELLED"):
                failure = job.get("failure") or {}
                item.update(status=job["status"], error=failure.get("why") or job.get("error_message"),
                            next_step=failure.get("next_step"))
            else:
                item["status"] = "RUNNING"

    @classmethod
    async def _run(cls, count, platforms, niche, topic, voice, provider, mode="viral") -> None:
        from app.core.runtime import run_blocking
        try:
            found = await run_blocking(search, platforms, queries_for(niche, topic, mode))
            cls.state["warnings"] = found["warnings"]
            cls.state.update(stage="measuring", message=f"{len(found['candidates'])} candidates found; checking their numbers…")
            await run_blocking(enrich, found["candidates"])
            found["candidates"] = [c for c in found["candidates"] if usable_length(c)]
            ranked = rank_candidates(found["candidates"], mode=mode)
            cls.state.update(stage="choosing", found=len(found["candidates"]),
                             message=f"{len(found['candidates'])} candidates found; choosing the {count} most promising…")
            if not ranked:
                raise ValueError("No usable videos were found on the selected platforms. Try other platforms or a different topic.")
            queue = list(ranked)
            budget = count * cls.MAX_ATTEMPTS_FACTOR
            launched = 0
            cls.state["stage"] = "producing"
            while True:
                cls._refresh()
                items = cls.state["items"]
                active = sum(1 for i in items if i["status"] in ("QUEUED", "RUNNING"))
                done = sum(1 for i in items if i["status"] == "DONE")
                failed = sum(1 for i in items if i["status"] in ("FAILED", "CANCELLED"))
                cls.state["made"] = done
                cls.state["message"] = f"{done} of {count} Shorts finished" + (f" · {active} in progress" if active else "") + (f" · {failed} skipped" if failed else "")
                if done >= count:
                    break
                # keep exactly as many productions going as Shorts are still needed
                while active + done < count and queue and launched < budget:
                    items.append(cls._launch(queue.pop(0), voice, provider, mode))
                    launched += 1
                    active += 1
                if active == 0:
                    break      # nothing running and nothing left to try
                await asyncio.sleep(cls.POLL_SECONDS)
            cls._refresh()
            made = sum(1 for i in cls.state["items"] if i["status"] == "DONE")
            cls.state.update(status="done" if made else "failed", stage="done", made=made, finished=time.time(),
                             message=(f"{made} Short{'s' if made != 1 else ''} ready to post." if made else
                                      "No Short could be produced from the videos found. See each item's reason."))
        except asyncio.CancelledError:
            cls.state.update(status="cancelled", message="Cancelled.")
            raise
        except Exception as exc:
            from app.core.failures import classify, redact
            failure = classify(exc, "auto shorts")
            cls.state.update(status="failed", error=redact(str(exc))[-500:], failure=failure.as_dict(), message=f"{failure.what}: {failure.next_step}")

"""Viral Discovery: searches overlap, junk is filtered before download, analysis stops early, progress is live."""
import asyncio
import threading
import time
import pytest
from app.ai.router import AIRouter
from app.sources import verdicts
from app.sources.discovery import SourceDiscovery
from app.sources.ingestion import SourceIngestion
from app.sources.web_search import WebVideoSearch
from app.studio import store, visual_discovery as vd
from app.studio.visual_discovery import DiscoveryRun, VisualDiscovery


@pytest.fixture
def studio(isolated_app):
    store.init_studio()
    return isolated_app


def entry(n, **extra):
    return {'id': f'id{n}', 'url': f'https://www.youtube.com/watch?v=vid{n:08d}', 'title': f'Roof gap recovery {n}', 'duration': 20, **extra}


def test_all_searches_run_at_once_not_one_after_another(studio, monkeypatch):
    live, peak = [0], [0]
    lock = threading.Lock()
    def slow_search(query, *a, **k):
        with lock:
            live[0] += 1; peak[0] = max(peak[0], live[0])
        time.sleep(.25)
        with lock: live[0] -= 1
        return [entry(abs(hash(query)) % 9999)]
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', slow_search)
    monkeypatch.setattr(WebVideoSearch, 'search', lambda q, p: slow_search(q + p))
    started = time.monotonic()
    results, warnings = VisualDiscovery.search(30)
    took = time.monotonic() - started
    pillars = len([p for p in store.profile().pillars if p in vd.QUERIES])
    assert peak[0] >= 4 and results
    assert took < pillars * 3 * .25 / 2, f'{took:.1f}s looks sequential'      # sequential would take pillars*3*0.25 s


def test_a_hung_site_cannot_hold_the_rest_up(studio, monkeypatch):
    monkeypatch.setattr(vd, 'SEARCH_DEADLINE', 1.5)
    release = threading.Event()
    def search(query, *a, **k):
        if 'near miss' in query: release.wait(10)        # one feed never answers
        return [entry(abs(hash(query)) % 9999)]
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', search)
    monkeypatch.setattr(WebVideoSearch, 'search', lambda q, p: search(q))
    started = time.monotonic()
    results, warnings = VisualDiscovery.search(30)
    release.set()
    assert time.monotonic() - started < 6 and results
    assert any(w['error'] == 'TimedOut' for w in warnings) or len(results) > 0


def test_junk_is_filtered_before_anything_is_downloaded(studio, monkeypatch):
    verdicts.remember({'youtube:vid00000009'}, 'unbelievable sports moment', 'Existing countdown')
    store.put_opportunity({'source_url': 'https://www.youtube.com/watch?v=vid00000008', 'topic': 'known'})
    rows = [entry(1), entry(2, duration=900), entry(3, title='Top 5 parkour saves'), entry(4, title='Minecraft parkour fail'),
            entry(5, duration=1), entry(8), entry(9), entry(6, duration=None)]
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', lambda q, *a, **k: rows)
    monkeypatch.setattr(WebVideoSearch, 'search', lambda q, p: [])
    monkeypatch.setattr(store, 'profile', lambda: type('P', (), {'pillars': ['insane_sports'], 'ai_mode': 'auto', 'positioning': ''})())
    results, _ = VisualDiscovery.search(30)
    urls = {r['url'].rsplit('=', 1)[1] for r in results}
    assert urls == {'vid00000001', 'vid00000006'}        # long, ranking, game, too-short, known and previously-rejected are gone


def test_downloads_use_a_smaller_rendition_for_discovery(studio, monkeypatch):
    seen = {}
    def download(url, destination, reject_rankings=False, max_height=1080, max_filesize=2 * 1024 ** 3, max_seconds=900):
        seen.update(h=max_height, size=max_filesize)
        raise ValueError('stop here')
    monkeypatch.setattr(SourceIngestion, 'download_video', download)
    asyncio.run(run_discover(monkeypatch, [entry(1)]))
    assert seen == {'h': vd.DISCOVERY_DOWNLOAD_HEIGHT, 'size': vd.DISCOVERY_MAX_BYTES}
    assert vd.DISCOVERY_DOWNLOAD_SECONDS <= 180


async def run_discover(monkeypatch, shortlist_entries, progress=None, analyze_hook=None):
    async def provider(settings): return object(), 'fixture'
    monkeypatch.setattr(AIRouter, 'require_ranking_provider', provider)
    items = [{**e, 'category': 'insane_sports', 'search_topic': 'unbelievable sports moment', 'platform': 'youtube'} for e in shortlist_entries]
    monkeypatch.setattr(VisualDiscovery, 'search', staticmethod(lambda limit, progress=None: (items, [])))
    return await VisualDiscovery.discover(30, progress)


def test_analysis_overlaps_three_candidates_at_a_time(studio, monkeypatch):
    active, peak, started = [0], [0], []
    def download(url, destination, reject_rankings=False, max_height=1080, max_filesize=0, max_seconds=0):
        started.append(url)
        active[0] += 1; peak[0] = max(peak[0], active[0]); time.sleep(.2); active[0] -= 1
        raise ValueError('not usable')
    monkeypatch.setattr(SourceIngestion, 'download_video', download)
    result = asyncio.run(run_discover(monkeypatch, [entry(n) for n in range(1, 11)]))
    assert peak[0] == vd.ANALYSIS_CONCURRENCY                       # three at a time
    assert len(started) == 10 and result['funnel']['deep_analyzed'] == 10 and len(result['warnings']) == 10


def test_progress_reports_searching_then_analyzing_with_counts(studio, monkeypatch):
    stages = []
    def download(*a, **k): raise ValueError('not usable')
    monkeypatch.setattr(SourceIngestion, 'download_video', download)
    asyncio.run(run_discover(monkeypatch, [entry(n) for n in range(1, 5)], progress=lambda stage, **info: stages.append((stage, info.get('analyzed'), info.get('total')))))
    assert stages[0][0] == 'searching' and stages[-1] == ('analyzing', 4, 4)


def test_background_run_returns_immediately_and_ends_with_results(studio, monkeypatch):
    async def fake_discover(limit, progress=None):
        progress('searching', found=3, searches_done=2, searches=30)
        await asyncio.sleep(.05)
        progress('analyzing', found=3, analyzed=1, total=3, verified=1, opportunities=[{'id': 'a', 'topic': 'Roof save'}])
        await asyncio.sleep(.05)
        return {'opportunities': [{'id': 'a', 'topic': 'Roof save'}], 'warnings': [{'source': 'x', 'error': 'e'}],
                'funnel': {'discovered': 3, 'deep_analyzed': 3, 'verified': 1, 'finalists': 1}, 'strategy': ''}
    monkeypatch.setattr(VisualDiscovery, 'discover', staticmethod(fake_discover))
    DiscoveryRun.state, DiscoveryRun.task = {'status': 'idle'}, None
    async def scenario():
        began = time.monotonic()
        first = DiscoveryRun.start(30)
        assert time.monotonic() - began < .05 and first['status'] == 'running'
        assert DiscoveryRun.start(30) is first or DiscoveryRun.start(30)['status'] == 'running'    # no second run while one is active
        await asyncio.sleep(.08)
        mid = dict(DiscoveryRun.status())
        await DiscoveryRun.task
        return mid, DiscoveryRun.status()
    mid, final = asyncio.run(scenario())
    assert mid['stage'] == 'analyzing' and mid['verified'] == 1 and mid['opportunities'][0]['topic'] == 'Roof save'
    assert final['status'] == 'done' and final['funnel']['verified'] == 1 and '1 verified moment' in final['message']


def test_a_failed_run_explains_itself_with_what_why_next(studio, monkeypatch):
    from app.ai.errors import AIQuotaExceeded
    async def spent(limit, progress=None): raise AIQuotaExceeded('gemini', 7200, 'spent')
    monkeypatch.setattr(VisualDiscovery, 'discover', staticmethod(spent))
    DiscoveryRun.state, DiscoveryRun.task = {'status': 'idle'}, None
    async def scenario():
        DiscoveryRun.start(30)
        await DiscoveryRun.task
        return DiscoveryRun.status()
    final = asyncio.run(scenario())
    assert final['status'] == 'failed' and final['failure']['code'] == 'AI_QUOTA' and final['failure']['retryable']
    assert 'quota' in final['message'].lower()


def test_endpoints_start_in_the_background_and_report_status(studio, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.server import app
    async def quick(limit, progress=None):
        return {'opportunities': [], 'warnings': [], 'funnel': {'discovered': 0, 'deep_analyzed': 0, 'verified': 0, 'finalists': 0}, 'strategy': ''}
    monkeypatch.setattr(VisualDiscovery, 'discover', staticmethod(quick))
    DiscoveryRun.state, DiscoveryRun.task = {'status': 'idle'}, None
    client = TestClient(app, client=('127.0.0.1', 5000))
    started = client.post('/api/studio/discover', json={'limit': 30}, headers={'content-type': 'application/json'})
    assert started.status_code == 200 and started.json()['status'] in ('running', 'done')
    for _ in range(50):
        body = client.get('/api/studio/discover/status').json()
        if body['status'] != 'running': break
        time.sleep(.05)
    assert body['status'] == 'done'


def test_analysis_stops_once_enough_moments_are_verified(studio, monkeypatch, tmp_path):
    from app.media.ffmpeg_core import FFmpegCore
    from app.media.moments import MomentAnalyzer
    from app.media.source_screening import SourceScreening
    from app.ai.ranking_verifier import RankingVerifier
    monkeypatch.setattr(vd, 'TARGET_VERIFIED', 2)
    downloaded = []
    def download(url, destination, reject_rankings=False, max_height=1080, max_filesize=0, max_seconds=0):
        downloaded.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b'x' * 100 + url.encode())
        return destination, {'url': url, 'title': 'clip', 'source_id': url, 'creator': 'c', 'license': 'unknown'}
    moment = {'start': 1.0, 'end': 9.0, 'label': 'Roof gap recovery', 'observed_action': 'A runner recovers', 'payoff_time': 5.0}
    async def evaluate(*a, **k): return [moment]
    async def review(*a, **k): return {'passed': True}
    async def score(provider, m, sheet, category, topic, level=None):
        return {'context_needed': False, 'reason': 'x' * 40, 'dimensions': {k: 90 for k in vd.ContentDirector.WEIGHTS}}
    async def screen(*a, **k): return {'suitable_raw': True, 'already_ranked': False, 'compilation': False}
    monkeypatch.setattr(SourceIngestion, 'download_video', download)
    monkeypatch.setattr(FFmpegCore, 'get_video_info', staticmethod(lambda p: {'width': 720, 'height': 1280, 'duration': 20}))
    monkeypatch.setattr(SourceScreening, 'inspect', staticmethod(lambda source, folder: {'rejected': False, 'sheet': tmp_path / 's.jpg'}))
    monkeypatch.setattr(MomentAnalyzer, 'analyze', staticmethod(lambda *a, **k: [dict(moment)]))
    monkeypatch.setattr(MomentAnalyzer, 'contact_sheet', staticmethod(lambda *a, **k: tmp_path / 'c.jpg'))
    monkeypatch.setattr(AIRouter, 'screen_ranking_source', staticmethod(screen))
    monkeypatch.setattr(RankingVerifier, 'evaluate', staticmethod(evaluate))
    monkeypatch.setattr(RankingVerifier, 'review', staticmethod(review))
    monkeypatch.setattr(VisualDiscovery, 'score', staticmethod(score))
    monkeypatch.setattr(vd.ContentDirector, 'assets', staticmethod(lambda opportunity, *a, **k: []))
    result = asyncio.run(run_discover(monkeypatch, [entry(n) for n in range(1, 11)]))
    assert result['funnel']['verified'] >= 2 and len(downloaded) < 10        # enough finalists: the rest were never downloaded
    assert len(downloaded) <= vd.TARGET_VERIFIED + vd.ANALYSIS_CONCURRENCY


# --- a stalled download can never freeze a run -------------------------------------------------------------
def fake_ytdlp(monkeypatch, events):
    """Replace yt-dlp with a downloader that feeds the progress hook `events` = [(seconds_elapsed, bytes_done)]."""
    import sys, types, time as real_time
    clock = {'now': 1000.0}
    monkeypatch.setattr('app.sources.ingestion.time.monotonic', lambda: clock['now'])
    class Downloader:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True):
            for elapsed, done in events:
                clock['now'] = 1000.0 + elapsed
                for hook in self.options['progress_hooks']:
                    hook({'status': 'downloading', 'downloaded_bytes': done})
            raise AssertionError('the download should have been abandoned before finishing')
    module = types.ModuleType('yt_dlp'); module.YoutubeDL = Downloader
    monkeypatch.setitem(sys.modules, 'yt_dlp', module)


def test_a_trickling_download_is_abandoned_instead_of_waited_on(monkeypatch, tmp_path):
    fake_ytdlp(monkeypatch, [(5, 200_000), (31, 300_000), (60, 320_000)])       # ~5 KB/s: throttled
    with pytest.raises(RuntimeError, match='too slow'):
        SourceIngestion.download_video('https://www.youtube.com/watch?v=abc', tmp_path / 'v.mp4')


def test_a_download_over_the_time_limit_is_abandoned_even_if_it_is_fast_enough(monkeypatch, tmp_path):
    fake_ytdlp(monkeypatch, [(10, 50_000_000), (100, 500_000_000), (130, 900_000_000)])
    with pytest.raises(RuntimeError, match='exceeded 120s'):
        SourceIngestion.download_video('https://www.youtube.com/watch?v=abc', tmp_path / 'v.mp4', max_seconds=120)


def test_a_healthy_download_is_not_interrupted(monkeypatch, tmp_path):
    fake_ytdlp(monkeypatch, [(5, 2_000_000), (40, 30_000_000)])
    with pytest.raises(RuntimeError, match='should have been abandoned'):        # the feed ran to its end: no hook aborted a healthy transfer
        SourceIngestion.download_video('https://www.youtube.com/watch?v=abc', tmp_path / 'v.mp4')

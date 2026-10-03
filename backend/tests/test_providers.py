"""Discovery providers normalize to one schema; queries target events, not ranking pages; rounds widen the search."""
import asyncio
import json
import pytest
from app.sources import providers
from app.sources.providers import (LicensedMediaProvider, LocalMediaProvider, RSSProvider, build_queries,
                                   classify_opportunity, duration_reason, normalize_candidate)
from app.sources.ranking_policy import RankingSourcePolicy
from app.sources.discovery import SourceDiscovery, note_rejection


def test_queries_describe_the_event_not_a_ranking_page():
    queries = build_queries('Craziest Parkour Saves')
    assert queries[0] == 'Parkour save'
    assert all(not any(w in q.lower() for w in ('craziest', 'best', 'top', 'ranking')) for q in queries)
    assert any('caught on camera' in q for q in queries) and len(queries) >= 7
    assert build_queries('Parkour fails')[0] == 'Parkour fail'          # legacy contract
    assert build_queries('Top 5 Insane Impossible Goalkeeper Saves')[0] == 'Goalkeeper save'


def test_candidate_schema_is_complete_and_keeps_legacy_keys():
    raw = {'id': 'abc', 'title': 'Roof gap recovery', 'url': 'https://youtu.be/abc', 'channel': 'Roofers', 'duration': 21,
           'view_count': 4200, 'timestamp': 1_700_000_000, 'description': 'x' * 2000}
    candidate = normalize_candidate(raw, 'youtube')
    for key in ('id', 'source', 'title', 'url', 'creator', 'published_at', 'duration', 'engagement_signals', 'description',
                'rights_status', 'source_metadata'):
        assert key in candidate
    assert candidate['creator'] == 'Roofers' and candidate['rights_status'] == 'unverified'
    assert candidate['engagement_signals']['views'] == 4200 and len(candidate['description']) == 1000
    assert candidate['published_at'].startswith('2023-') and candidate['platform'] == 'youtube'


def test_a_provider_cannot_claim_unknown_rights_values():
    assert normalize_candidate({'id': 'x', 'title': 't', 'rights_status': 'totally_fine'}, 'rss')['rights_status'] == 'unverified'
    assert normalize_candidate({'id': 'x', 'title': 't', 'rights_status': 'licensed'}, 'rss')['rights_status'] == 'licensed'


@pytest.mark.parametrize('views,label', [(2_000_000, 'TRENDING'), (800, 'EMERGING'), (250_000, 'ESTABLISHED'), (None, 'UNKNOWN')])
def test_trending_and_emerging_opportunities(views, label):
    assert classify_opportunity({'engagement_signals': {'views': views}}) == label


@pytest.mark.parametrize('duration,rejected', [(20, False), (None, False), (2, True), (180, True), (3935, True)])
def test_duration_prefilter(duration, rejected):
    assert bool(duration_reason({'duration': duration})) is rejected


@pytest.mark.parametrize('title', ['Minecraft parkour fail', 'Roblox obby save', 'Titanfall - Close Call!', 'Parkour tutorial for beginners', 'Animated cartoon parkour'])
def test_game_and_tutorial_content_is_rejected_before_download(title):
    assert RankingSourcePolicy.metadata_reason({'title': title})


@pytest.mark.parametrize('title', ['5 extreme parkour moments, which one is your favourite', '5 DRIVERS WHO REALLY COULDN’T CATCH A BREAK',
                                   '7 insane skate fails', '10 dogs that learned new tricks'])
def test_number_led_list_titles_are_rejected(title):
    assert RankingSourcePolicy.metadata_reason({'title': title})


def test_real_footage_titles_are_not_over_rejected():
    for title in ['Roof gap recovery', 'Close call on a rail', 'Goalkeeper game-winning save', 'Wall run slip', '1 in a million shot', '2 seconds from disaster', 'He lands it on his 3rd try']:
        assert RankingSourcePolicy.metadata_reason({'title': title}) is None


def test_rejection_list_has_one_entry_per_url():
    rejected = []
    for _ in range(3):
        note_rejection(rejected, {'url': 'https://x/1', 'title': 'a', 'reason': 'r'})
    note_rejection(rejected, {'url': 'https://x/2', 'title': 'b', 'reason': 'r'})
    assert len(rejected) == 2


def test_local_and_licensed_providers_only_trust_sidecar_proof(tmp_path):
    (tmp_path / 'my_parkour_run.mp4').write_bytes(b'v')
    (tmp_path / 'licensed_parkour_a.mp4').write_bytes(b'v')
    (tmp_path / 'licensed_parkour_a.rights.json').write_text(json.dumps(
        {'rights_status': 'licensed', 'creator': 'Studio', 'license': 'Commercial', 'proof_reference': 'invoice-77'}))
    (tmp_path / 'claimed_parkour_b.mp4').write_bytes(b'v')
    (tmp_path / 'claimed_parkour_b.rights.json').write_text(json.dumps({'rights_status': 'licensed'}))   # no proof
    local = {e['title']: e for e in LocalMediaProvider(tmp_path).candidates('parkour')}
    assert local['my parkour run']['rights_status'] == 'unverified'
    assert local['licensed parkour a']['rights_status'] == 'licensed'
    licensed = [e['title'] for e in LicensedMediaProvider(tmp_path).candidates('parkour')]
    assert licensed == ['licensed parkour a']


def test_rss_provider_filters_by_query_and_carries_declared_rights(monkeypatch):
    xml = (b'<rss><channel><item><title>Parkour save on a roof</title><link>https://agency.example/v/1</link>'
           b'<description>d</description><pubDate>Mon, 01 Sep 2025 10:00:00 GMT</pubDate></item>'
           b'<item><title>Cooking pasta</title><link>https://agency.example/v/2</link></item></channel></rss>')
    class Response:
        content = xml
        def raise_for_status(self): pass
    monkeypatch.setattr(providers.requests, 'get', lambda *a, **k: Response())
    found = RSSProvider('https://agency.example/feed', rights_status='licensed').candidates('parkour')
    assert [e['url'] for e in found] == ['https://agency.example/v/1'] and found[0]['rights_status'] == 'licensed'
    with pytest.raises(ValueError):
        RSSProvider('file:///etc/passwd')


# --- widening ---------------------------------------------------------------
def test_pipeline_widens_the_search_until_enough_verified_sources_exist(isolated_app, monkeypatch):
    from app.core import database
    from app.ai.router import AIRouter
    from app.pipelines.ranking import ranking_pipeline as rp
    from app.pipelines.ranking.ranking_pipeline import RankingPipeline
    rounds_seen, verified_ids = [], []

    def discover(topic, count, temp, **kw):
        rounds_seen.append((kw['round_index'], sorted(kw['exclude_urls'])))
        return [{'id': f"r{kw['round_index']}_s{i}", 'title': 't', 'url': f"https://v/{kw['round_index']}/{i}", 'file_path': 'x', 'duration': 30}
                for i in range(3)]
    async def verify(cls, idx, source, topic, count, settings, provider_info, temp, rejections):
        verified_ids.append(source['id'])
        if source['id'].endswith('s0'):     # only one survivor per batch: verification is strict
            return {**source, 'source_id': source['id'], 'file_path': 'x', 'score': 70}
        rejections.append({'title': 't', 'url': source['url'], 'reason': 'no verified event'})
    async def provider(*a, **k): return object(), 'fixture'
    reached = {}
    async def produce(cls, job_id, project_id, topic, count, settings, provider_info, temp, pool, *a, **k):
        reached['pool'] = [m['id'] for m in pool]
    monkeypatch.setattr(AIRouter, 'require_ranking_provider', provider)
    monkeypatch.setattr(rp.SourceDiscovery, 'discover_candidate_videos', staticmethod(discover))
    monkeypatch.setattr(RankingPipeline, 'verify_source', classmethod(verify))
    monkeypatch.setattr(RankingPipeline, 'produce_verified_pool', classmethod(produce))
    monkeypatch.setattr(rp, 'MAX_DISCOVERY_ROUNDS', 4)
    database.create_project('p', 'ranking', 't', {})
    database.create_job('j', 'p')
    asyncio.run(RankingPipeline.run('j', 'p', 'Parkour saves', 3, {'voice_synthesizer': object(), 'variants': 1}, lambda *a: None))
    assert [r for r, _ in rounds_seen] == [0, 1, 2]                      # stopped once 3 were verified
    assert rounds_seen[1][1] == ['https://v/0/0', 'https://v/0/1', 'https://v/0/2']   # round 2 skips what was already judged
    assert reached['pool'] == ['r0_s0', 'r1_s0', 'r2_s0']
    assert len(verified_ids) == 9


def test_widening_stops_at_the_candidate_ceiling_and_reports_honestly(isolated_app, monkeypatch):
    from app.core import database
    from app.ai.router import AIRouter
    from app.pipelines.ranking import ranking_pipeline as rp
    from app.pipelines.ranking.ranking_pipeline import RankingPipeline
    def discover(topic, count, temp, **kw):
        return [{'id': f"r{kw['round_index']}_{i}", 'title': 't', 'url': f"https://v/{kw['round_index']}/{i}", 'file_path': 'x', 'duration': 30} for i in range(5)]
    async def verify(cls, idx, source, topic, count, settings, provider_info, temp, rejections):
        rejections.append({'title': 't', 'url': source['url'], 'reason': 'Not a complete event'})
    async def provider(*a, **k): return object(), 'fixture'
    monkeypatch.setattr(AIRouter, 'require_ranking_provider', provider)
    monkeypatch.setattr(rp.SourceDiscovery, 'discover_candidate_videos', staticmethod(discover))
    monkeypatch.setattr(RankingPipeline, 'verify_source', classmethod(verify))
    monkeypatch.setattr(rp, 'MAX_TOTAL_CANDIDATES', 10)
    database.create_project('p', 'ranking', 't', {})
    database.create_job('j', 'p')
    with pytest.raises(ValueError, match='Only 0 unused individual clips'):
        asyncio.run(RankingPipeline.run('j', 'p', 'Parkour saves', 3, {'voice_synthesizer': object(), 'variants': 1}, lambda *a: None))
    job = database.get_job('j')
    assert job['status'] == 'FAILED' and job['failure']['code'] == 'NOT_ENOUGH_SOURCES' and job['failure']['retryable']


def test_cc_filter_search_and_licence_recheck(monkeypatch, tmp_path):
    import shutil
    from app.sources.discovery import is_reusable_cc
    from app.sources.ingestion import SourceIngestion
    assert is_reusable_cc('Creative Commons Attribution license (reuse allowed)')
    assert not any(is_reusable_cc(x) for x in ('Standard YouTube License', None, 'CC BY-NC 4.0', 'Creative Commons Attribution-NoDerivatives', ''))
    calls = []
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', lambda q, cc_only=False: calls.append(cc_only) or [
        {'id': 'cc1', 'url': 'https://youtube.com/watch?v=cc1', 'title': 'Roof gap recovery', 'duration': 20},
        {'id': 'std', 'url': 'https://youtube.com/watch?v=std', 'title': 'Wall run slip', 'duration': 20}])
    def download(url, destination, reject_rankings=False):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SRC, destination)
        return destination, {'source_id': url, 'url': url, 'title': 'clip', 'license': 'Creative Commons Attribution license (reuse allowed)' if 'cc1' in url else 'Standard YouTube License'}
    from app.core.runtime import run_process
    SRC = tmp_path / 'src.mp4'
    run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=15:duration=3', '-pix_fmt', 'yuv420p', str(SRC)])
    monkeypatch.setattr(SourceIngestion, 'download_video', download)
    rejected = []
    found = SourceDiscovery.discover_candidate_videos('Parkour', 1, tmp_path / 'w', rejections=rejected, cc_only=True,
                                                      source_platforms=['youtube', 'reddit'], allow_partial=True)
    assert [s['url'] for s in found] == ['https://youtube.com/watch?v=cc1']
    assert set(calls) == {True}                     # only the CC-filtered YouTube search ran (no Reddit)
    assert any('Not under a reusable Creative Commons licence' in r['reason'] for r in rejected)

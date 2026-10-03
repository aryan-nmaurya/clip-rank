"""A refused AI provider stops the job with a clear reason; it never relabels good footage as unverifiable."""
import asyncio
import pytest
from app.ai import errors
from app.ai.errors import AIQuotaExceeded, is_daily_quota, retry_delay_seconds
from app.ai.gemini import GeminiProvider
from app.core import database
from app.core.failures import classify
from app.sources import verdicts

REAL = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota. * Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 500, model: gemini-3.1-flash-lite\\n"
        "Please retry in 11h46m59.214958544s.', 'status': 'RESOURCE_EXHAUSTED'}}")


def test_parse_the_provider_retry_delay_in_both_formats():
    assert retry_delay_seconds(REAL) == pytest.approx(11 * 3600 + 46 * 60 + 59.2, rel=1e-3)
    assert retry_delay_seconds("retryDelay': '37s'") == 37
    assert retry_delay_seconds('no hint here') is None


def test_daily_quota_is_told_apart_from_a_per_minute_limit():
    assert is_daily_quota(REAL, retry_delay_seconds(REAL))
    assert not is_daily_quota('Rate limit reached, retry in 12s', 12)
    assert is_daily_quota('quota', 4000)


class Boom(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def test_gemini_raises_instead_of_returning_none_when_the_daily_quota_is_gone(monkeypatch):
    provider = GeminiProvider(api_key='AIzaSyA1234567890123456789012345678')
    async def refuse(fn):
        raise Boom(REAL, 429)
    monkeypatch.setattr('app.ai.gemini.run_blocking', refuse)
    with pytest.raises(AIQuotaExceeded) as caught:
        asyncio.run(provider._generate('hello'))
    assert 'Retry in about 11h 46m' in str(caught.value) and 'AIzaSy' not in str(caught.value)
    assert caught.value.retry_after > 40000


def test_gemini_waits_out_a_short_rate_limit_then_succeeds(monkeypatch):
    provider = GeminiProvider(api_key='AIzaSyA1234567890123456789012345678')
    calls, slept = [], []
    async def flaky(fn):
        calls.append(1)
        if len(calls) == 1:
            raise Boom('429 Too many requests, retry in 3s', 429)
        return 'ok'
    async def fake_sleep(seconds): slept.append(seconds)
    monkeypatch.setattr('app.ai.gemini.run_blocking', flaky)
    monkeypatch.setattr('app.ai.gemini.asyncio.sleep', fake_sleep)
    assert asyncio.run(provider._generate('hello')) == 'ok' and slept == [3]


def test_quota_stops_the_whole_ranking_job_and_is_not_blamed_on_the_clips(isolated_app, monkeypatch):
    from app.ai.router import AIRouter
    from app.pipelines.ranking import ranking_pipeline as rp
    from app.pipelines.ranking.ranking_pipeline import RankingPipeline
    started, finished = [], []
    def discover(topic, count, temp, **kw):
        return [{'id': f's{i}', 'title': 't', 'url': f'https://v/{i}', 'file_path': 'x', 'duration': 30} for i in range(6)]
    async def verify(cls, idx, source, topic, count, settings, provider_info, temp, rejections):
        started.append(idx)
        if idx == 1:
            raise AIQuotaExceeded('gemini', 42000, 'free tier')
        await asyncio.sleep(.2)
        finished.append(idx)
    async def provider(*a, **k): return object(), 'fixture'
    monkeypatch.setattr(AIRouter, 'require_ranking_provider', provider)
    monkeypatch.setattr(rp.SourceDiscovery, 'discover_candidate_videos', staticmethod(discover))
    monkeypatch.setattr(RankingPipeline, 'verify_source', classmethod(verify))
    database.create_project('p', 'ranking', 't', {})
    database.create_job('j', 'p')
    with pytest.raises(AIQuotaExceeded):
        asyncio.run(RankingPipeline.run('j', 'p', 'Parkour saves', 3, {'voice_synthesizer': object(), 'variants': 1}, lambda *a: None))
    job = database.get_job('j')
    assert job['status'] == 'FAILED'
    assert job['failure']['code'] == 'AI_QUOTA' and job['failure']['retryable']
    assert 'Retry in about 11h 40m' in job['failure']['why']
    assert not finished or max(finished) < 3       # siblings were cancelled, not left spending API calls


def test_failure_classification_names_the_quota():
    failure = classify(AIQuotaExceeded('gemini', 3600, 'x'), 'ANALYZING')
    assert failure.code == 'AI_QUOTA' and 'reset' in failure.next_step


# --- verdict memory ---------------------------------------------------------
def test_only_real_verdicts_are_remembered_per_topic(isolated_app):
    keys = {'youtube:abc', 'url:https://x/abc'}
    assert verdicts.recall(keys, 'Parkour saves') is None
    verdicts.remember(keys, 'Parkour saves', 'Montage of unrelated clips')
    assert verdicts.recall({'youtube:abc'}, 'parkour  SAVES') == 'Montage of unrelated clips'
    assert verdicts.recall({'youtube:abc'}, 'Football saves') is None            # a verdict is about a topic
    assert verdicts.recall({'youtube:abc'}, 'Parkour saves', now=1e12) is None   # and it expires


def test_discovery_skips_sources_already_judged_unsuitable(isolated_app, monkeypatch, tmp_path):
    from app.sources.discovery import SourceDiscovery
    from app.sources.ingestion import SourceIngestion
    verdicts.remember({'youtube:old1'}, 'Parkour saves', 'Existing countdown video')
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', lambda q, cc_only=False: [
        {'id': 'old1', 'url': 'https://www.youtube.com/watch?v=old1', 'title': 'Roof gap', 'duration': 20}])
    downloads = []
    monkeypatch.setattr(SourceIngestion, 'download_video', lambda *a, **k: downloads.append(a))
    rejected = []
    SourceDiscovery.discover_candidate_videos('Parkour saves', 1, tmp_path, rejections=rejected, allow_partial=True, source_platforms=['youtube'])
    assert downloads == [] and 'Judged unsuitable for this topic earlier' in rejected[0]['reason']


# --- two-key failover -------------------------------------------------------
KEY1, KEY2 = 'AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'AIzaSyBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB'


def fake_transport(monkeypatch, behaviour):
    """Replace the SDK call; `behaviour(key)` returns text or raises."""
    used = []
    calls = fake_transport.calls = []
    def client_for(self, key=None):
        class Client:
            def __enter__(inner): return inner
            def __exit__(inner, *a): return False
            class models:
                @staticmethod
                def generate_content(**kw):
                    used.append(key)
                    import inspect
                    calls.append((key, kw['model']))
                    value = behaviour(key, kw['model']) if len(inspect.signature(behaviour).parameters) == 2 else behaviour(key)
                    return type('R', (), {'text': value, 'usage_metadata': None})()
        return Client()
    monkeypatch.setattr(GeminiProvider, '_get_client', client_for)
    return used


def test_second_key_takes_over_when_the_first_quota_is_spent(monkeypatch):
    def behaviour(key):
        if key == KEY1:
            raise Boom(REAL, 429)
        return 'from-second-key'
    used = fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, extra_keys=[KEY2])
    assert asyncio.run(provider.generate_text('hi')) == 'from-second-key'
    assert used == [KEY1, KEY2]
    # The spent key is remembered: later calls (even from a fresh provider object) go straight to key two.
    used.clear()
    assert asyncio.run(GeminiProvider(api_key=KEY1, extra_keys=[KEY2]).generate_text('again')) == 'from-second-key'
    assert used == [KEY2]
    status = provider.key_status()
    assert (status['configured'], status['available'], status['exhausted']) == (2, 1, 1)


def test_job_stops_only_when_every_key_is_spent(monkeypatch):
    def behaviour(key):
        raise Boom(REAL, 429)
    used = fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, extra_keys=[KEY2])
    with pytest.raises(AIQuotaExceeded, match=r'All 2 key\(s\) x 1 model\(s\)'):
        asyncio.run(provider.generate_text('hi'))
    assert used == [KEY1, KEY2]
    used.clear()
    with pytest.raises(AIQuotaExceeded):            # nothing is even attempted while both are cooling down
        asyncio.run(provider.generate_text('again'))
    assert used == []


def test_a_key_becomes_usable_again_after_its_cooldown(monkeypatch):
    from app.ai import gemini
    fake_transport(monkeypatch, lambda key: 'ok')
    provider = GeminiProvider(api_key=KEY1, extra_keys=[KEY2])
    gemini._exhausted[gemini.slot(KEY1, provider.model)] = 1.0      # cooled down long ago
    gemini._exhausted[gemini.slot(KEY2, provider.model)] = 1e15
    assert provider.live_keys() == [KEY1]


def test_duplicate_blank_and_short_keys_are_ignored():
    provider = GeminiProvider(api_key=KEY1, extra_keys=[KEY1, '', None, 'short'])
    assert provider.keys == [KEY1]
    assert asyncio.run(GeminiProvider(api_key=None, extra_keys=[KEY2]).is_available())


def test_second_key_is_stored_in_the_keychain_never_returned_by_the_api(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 1))
    headers = {'content-type': 'application/json'}
    body = client.post('/api/settings', json={'gemini_api_key': KEY1, 'gemini_api_key_2': KEY2}, headers=headers).json()
    assert body['gemini_api_key_configured'] and body['gemini_api_key_2_configured']
    assert 'gemini_api_key_2' not in body and KEY2 not in str(body)
    stored = database.get_settings()
    assert stored['gemini_api_key_2'] == KEY2                     # resolved from the (test) vault for the provider
    assert database.get_connection().execute('SELECT gemini_api_key_2 FROM settings').fetchone()[0] == 'keychain:gemini_api_key_2'
    from app.ai.router import AIRouter
    gemini, _, _ = AIRouter.get_providers(stored)
    assert gemini.keys == [KEY1, KEY2]


# --- model fallback chain ---------------------------------------------------
M1, M2, M3 = 'model-primary', 'model-second', 'model-third'


def test_models_are_tried_in_order_after_both_keys_are_spent_on_the_first(monkeypatch):
    def behaviour(key, model):
        if model == M1:
            raise Boom(REAL, 429)
        return 'answered by ' + model
    fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, model=M1, extra_keys=[KEY2], fallback_models=[M2, M3])
    assert asyncio.run(provider.generate_text('hi')) == 'answered by ' + M2
    assert fake_transport.calls == [(KEY1, M1), (KEY2, M1), (KEY1, M2)]     # both keys on M1 first, then the next model
    assert provider.last_model == M2
    fake_transport.calls.clear()
    assert asyncio.run(provider.generate_text('again')) == 'answered by ' + M2
    assert fake_transport.calls == [(KEY1, M2)]            # spent pairs are skipped, no wasted calls


def test_chain_walks_through_every_model_and_only_then_stops(monkeypatch):
    def behaviour(key, model):
        raise Boom(REAL, 429)
    fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, model=M1, extra_keys=[KEY2], fallback_models=[M2, M3])
    with pytest.raises(AIQuotaExceeded, match=r'2 key\(s\) x 3 model\(s\)'):
        asyncio.run(provider.generate_text('hi'))
    assert len(fake_transport.calls) == 6
    fake_transport.calls.clear()
    with pytest.raises(AIQuotaExceeded):
        asyncio.run(provider.generate_text('again'))
    assert fake_transport.calls == []


def test_a_model_the_account_does_not_have_is_skipped_not_fatal(monkeypatch):
    def behaviour(key, model):
        if model == M1:
            raise Boom(REAL, 429)
        if model == M2:
            raise Boom('404 NOT_FOUND. models/model-second is not found for API version v1beta', 404)
        return 'answered by ' + model
    fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, model=M1, fallback_models=[M2, M3])
    assert asyncio.run(provider.generate_text('hi')) == 'answered by ' + M3
    from app.ai import gemini
    assert M2 in gemini._unavailable
    fake_transport.calls.clear()
    asyncio.run(provider.generate_text('again'))
    assert all(model != M2 for _, model in fake_transport.calls)


def test_model_list_parsing_and_status():
    from app.ai.gemini import parse_models
    assert parse_models('a-1, b.2  a-1;bad  c_3') == ['a-1', 'b.2', 'c_3']
    assert parse_models(None) == [] and parse_models(['x', 'x', 'y']) == ['x', 'y']
    provider = GeminiProvider(api_key=KEY1, model=M1, fallback_models='model-second model-third')
    assert provider.models == [M1, M2, M3] and provider.key_status()['usable_models'] == [M1, M2, M3]


def test_router_builds_the_default_chain_unless_the_user_overrides_it():
    from app.ai.router import AIRouter
    from app.ai.gemini import DEFAULT_FALLBACK_MODELS
    base = {'gemini_api_key': KEY1, 'gemini_model': M1}
    default = AIRouter.get_providers(base)[0]
    assert default.models == [M1, *DEFAULT_FALLBACK_MODELS] and len(DEFAULT_FALLBACK_MODELS) >= 3
    assert AIRouter.get_providers({**base, 'gemini_fallback_models': 'x-1 y-2'})[0].models == [M1, 'x-1', 'y-2']
    assert AIRouter.get_providers({**base, 'gemini_fallback_models': ''})[0].models == [M1]


# --- switching keys on rate limits and rejected keys (not only daily quota) ----
RATE = '429 RESOURCE_EXHAUSTED. Resource has been exhausted (e.g. check quota). Please retry in 20s.'


def test_a_rate_limited_key_hands_the_request_to_the_other_key_immediately(monkeypatch):
    slept = []
    async def fake_sleep(seconds): slept.append(seconds)
    monkeypatch.setattr('app.ai.gemini.asyncio.sleep', fake_sleep)
    def behaviour(key):
        if key == KEY1:
            raise Boom(RATE, 429)
        return 'from-second-key'
    fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, extra_keys=[KEY2])
    assert asyncio.run(provider.generate_text('hi')) == 'from-second-key'
    assert fake_transport.calls == [(KEY1, provider.model), (KEY2, provider.model)]
    assert slept == []                                  # no waiting around while another key is free
    fake_transport.calls.clear()
    assert asyncio.run(provider.generate_text('again')) == 'from-second-key'
    assert fake_transport.calls == [(KEY2, provider.model)]    # key one is resting for a moment, key two keeps working


def test_a_rejected_key_is_skipped_on_every_model_and_the_other_key_answers(monkeypatch):
    def behaviour(key, model):
        if key == KEY1:
            raise Boom('400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.', 400)
        return 'ok ' + model
    fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, model=M1, extra_keys=[KEY2], fallback_models=[M2])
    assert asyncio.run(provider.generate_text('hi')) == 'ok ' + M1
    fake_transport.calls.clear()
    asyncio.run(provider.generate_text('again'))
    assert all(key == KEY2 for key, _ in fake_transport.calls)        # the bad key is not retried on M2 either


def test_with_a_single_key_a_short_rate_limit_is_waited_out(monkeypatch):
    slept = []
    async def fake_sleep(seconds): slept.append(seconds)
    monkeypatch.setattr('app.ai.gemini.asyncio.sleep', fake_sleep)
    state = {'n': 0}
    def behaviour(key):
        state['n'] += 1
        if state['n'] == 1:
            raise Boom(RATE, 429)
        return 'ok'
    fake_transport(monkeypatch, behaviour)
    assert asyncio.run(GeminiProvider(api_key=KEY1).generate_text('hi')) == 'ok' and slept == [20]


def test_when_every_key_is_only_rate_limited_it_is_not_reported_as_a_daily_quota_stop(monkeypatch):
    async def fake_sleep(seconds): pass
    monkeypatch.setattr('app.ai.gemini.asyncio.sleep', fake_sleep)
    def behaviour(key):
        raise Boom(RATE, 429)
    fake_transport(monkeypatch, behaviour)
    provider = GeminiProvider(api_key=KEY1, extra_keys=[KEY2])
    assert asyncio.run(provider.generate_text('hi')) is None           # callers see last_error, not "quota used up"
    assert 'rate limited' in provider.last_error


def test_connections_endpoint_shows_how_many_keys_are_usable(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    from app.ai import gemini
    client = TestClient(app, client=('127.0.0.1', 1))
    client.post('/api/settings', json={'gemini_api_key': KEY1, 'gemini_api_key_2': KEY2}, headers={'content-type': 'application/json'})
    keys = client.get('/api/vision/connections').json()['gemini']['keys']
    assert (keys['configured'], keys['available'], keys['exhausted']) == (2, 2, 0)
    default_model = keys['models'][0]
    gemini._exhausted[gemini.slot(KEY1, default_model)] = 1e15
    keys = client.get('/api/vision/connections').json()['gemini']['keys']
    assert (keys['available'], keys['exhausted']) == (1, 1)

"""Groq and NVIDIA NIM providers, and the AUTO chain that moves on when one is out of quota."""
import asyncio
import json
import httpx
import pytest
from PIL import Image
from app.ai import compat_provider as cp
from app.ai.chain import ChainProvider
from app.ai.compat_provider import GroqProvider, NvidiaNIMProvider, limit_images, prepare_image, retry_seconds
from app.ai.errors import AIQuotaExceeded
from app.ai.gemini import GeminiProvider
from app.ai.router import AIRouter

GROQ_KEY, NIM_KEY = 'gsk_' + 'a' * 40, 'nvapi-' + 'b' * 40


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    cp._cooling.clear()
    async def no_wait(seconds): pass
    monkeypatch.setattr(cp, '_sleep', no_wait)
    yield
    cp._cooling.clear()


def serve(provider_class, handler, **kw):
    provider = provider_class(**kw)
    seen = []
    def wrapped(request):
        seen.append(request)
        return handler(request)
    provider.transport = httpx.MockTransport(wrapped)
    provider.seen = seen
    return provider


def ok(text='{"ok": true}', usage=(11, 7)):
    return httpx.Response(200, json={'choices': [{'message': {'content': text}}], 'usage': {'prompt_tokens': usage[0], 'completion_tokens': usage[1]}})


def image(tmp_path, name='a.jpg', size=(64, 64)):
    path = tmp_path / name
    Image.new('RGB', size, 'red').save(path)
    return path


# --- request shape ----------------------------------------------------------
def test_groq_request_targets_its_api_with_auth_model_and_images(tmp_path):
    provider = serve(GroqProvider, lambda r: ok(), api_key=GROQ_KEY)
    assert asyncio.run(provider.analyze_images([image(tmp_path)], 'describe', {'json': True})) == '{"ok": true}'
    request = provider.seen[0]
    assert str(request.url) == 'https://api.groq.com/openai/v1/chat/completions'
    assert request.headers['authorization'] == f'Bearer {GROQ_KEY}'
    body = json.loads(request.content)
    assert body['model'] == 'qwen/qwen3.8-27b' and body['response_format'] == {'type': 'json_object'}
    parts = body['messages'][0]['content']
    assert parts[0] == {'type': 'text', 'text': 'describe'} and parts[1]['image_url']['url'].startswith('data:image/jpeg;base64,')


def test_nim_uses_its_endpoint_and_a_custom_model(tmp_path):
    provider = serve(NvidiaNIMProvider, lambda r: ok('hello'), api_key=NIM_KEY, model='nvidia/custom-vision')
    assert asyncio.run(provider.generate_text('hi', system_prompt='be brief')) == 'hello'
    request = provider.seen[0]
    assert str(request.url) == 'https://integrate.api.nvidia.com/v1/chat/completions'
    body = json.loads(request.content)
    assert body['model'] == 'nvidia/custom-vision' and body['messages'][0] == {'role': 'system', 'content': 'be brief'}


def test_unconfigured_provider_makes_no_request():
    provider = serve(GroqProvider, lambda r: ok(), api_key=None)
    assert asyncio.run(provider.is_available()) is False and asyncio.run(provider.generate_text('x')) is None
    assert provider.seen == []


def test_extra_images_are_stacked_not_dropped(tmp_path):
    paths = [image(tmp_path, f'{i}.jpg', (100, 50 + i)) for i in range(7)]
    merged = limit_images(paths, 3)
    assert len(merged) == 3
    heights = [Image.open(p).height for p in merged]
    assert sum(heights) == sum(50 + i for i in range(7))        # every pixel row is still there
    provider = serve(NvidiaNIMProvider, lambda r: ok(), api_key=NIM_KEY)
    asyncio.run(provider.analyze_images(paths, 'sheets'))
    content = json.loads(provider.seen[0].content)['messages'][0]['content']
    assert sum(1 for p in content if p['type'] == 'image_url') == 1       # NIM: one image per request


def test_oversized_images_are_downscaled_under_the_api_limit(tmp_path):
    import numpy as np
    big = tmp_path / 'big.jpg'
    Image.fromarray(np.random.randint(0, 255, (3000, 3000, 3), dtype='uint8')).save(big, quality=100)
    assert big.stat().st_size > cp.MAX_IMAGE_BYTES
    assert len(prepare_image(big)) <= cp.MAX_IMAGE_BYTES


# --- errors and quota -------------------------------------------------------
def test_retry_hints_in_every_format():
    assert retry_seconds('Please try again in 7m12.5s.') == pytest.approx(432.5)
    assert retry_seconds('Please try again in 350ms') == pytest.approx(.35)
    assert retry_seconds('try again in 1h2m3s') == 3723
    assert retry_seconds('whatever', header='17') == 17 and retry_seconds('nothing') is None


def test_daily_limit_raises_quota_and_the_provider_then_cools_down_without_network(tmp_path):
    body = {'error': {'message': 'Rate limit reached for model on tokens per day (TPD): Limit 500000. Please try again in 8h3m.'}}
    provider = serve(GroqProvider, lambda r: httpx.Response(429, json=body), api_key=GROQ_KEY)
    with pytest.raises(AIQuotaExceeded) as caught:
        asyncio.run(provider.generate_text('hi'))
    assert caught.value.retry_after > 28000 and caught.value.provider == 'groq'
    calls = len(provider.seen)
    with pytest.raises(AIQuotaExceeded):
        asyncio.run(provider.generate_text('again'))
    assert len(provider.seen) == calls


def test_payment_required_is_a_quota_stop():
    provider = serve(NvidiaNIMProvider, lambda r: httpx.Response(402, json={'detail': 'Insufficient credits'}), api_key=NIM_KEY)
    with pytest.raises(AIQuotaExceeded):
        asyncio.run(provider.generate_text('hi'))


def test_short_rate_limit_waits_then_succeeds():
    replies = [httpx.Response(429, json={'error': {'message': 'Please try again in 2s'}}), ok('fine')]
    provider = serve(GroqProvider, lambda r: replies.pop(0), api_key=GROQ_KEY)
    assert asyncio.run(provider.generate_text('hi')) == 'fine' and len(provider.seen) == 2


def test_models_without_json_mode_are_retried_plainly():
    def handler(request):
        if 'response_format' in json.loads(request.content):
            return httpx.Response(400, json={'error': {'message': 'response_format json_object is not supported'}})
        return ok('plain')
    provider = serve(NvidiaNIMProvider, handler, api_key=NIM_KEY)
    assert asyncio.run(provider.generate_text('hi', options={'json': True})) == 'plain'


def test_server_errors_retry_and_other_failures_return_none_with_the_reason():
    replies = [httpx.Response(503, text='busy'), ok('recovered')]
    provider = serve(GroqProvider, lambda r: replies.pop(0), api_key=GROQ_KEY)
    assert asyncio.run(provider.generate_text('hi')) == 'recovered'
    broken = serve(GroqProvider, lambda r: httpx.Response(401, text=f'bad key {GROQ_KEY}'), api_key=GROQ_KEY)
    assert asyncio.run(broken.generate_text('hi')) is None
    assert '401' in broken.last_error and GROQ_KEY not in broken.last_error


def test_token_usage_is_recorded_for_the_cost_ledger(isolated_app):
    from app.business import store
    provider = serve(GroqProvider, lambda r: ok(usage=(1200, 300)), api_key=GROQ_KEY)
    asyncio.run(provider.generate_text('hi'))
    summary = store.ai_usage_summary()
    assert summary['calls'] == 1 and summary['input_tokens'] == 1200 and summary['output_tokens'] == 300


# --- chain ------------------------------------------------------------------
class Fake:
    def __init__(self, result=None, quota=False, video=False):
        self.result, self.quota, self.calls = result, quota, 0
        if video:
            self.analyze_video = self._video

    async def _video(self, path, prompt): return 'video-answer'
    async def is_available(self): return True

    async def generate_text(self, prompt, system_prompt=None, options=None):
        self.calls += 1
        if self.quota:
            raise AIQuotaExceeded('fake', 60, 'spent')
        return self.result

    analyze_images = generate_text


def test_chain_moves_to_the_next_provider_when_quota_is_spent():
    first, second, third = Fake(quota=True), Fake('from-second'), Fake('from-third')
    chain = ChainProvider([('gemini', first), ('groq', second), ('nvidia', third)])
    assert asyncio.run(chain.generate_text('hi')) == 'from-second'
    assert (first.calls, second.calls, third.calls) == (1, 1, 0) and chain.last_member == 'groq'


def test_chain_also_moves_on_when_a_provider_cannot_answer():
    chain = ChainProvider([('gemini', Fake(None)), ('groq', Fake('answer'))])
    assert asyncio.run(chain.analyze_images([], 'p')) == 'answer'


def test_chain_stops_with_one_clear_error_when_every_provider_is_spent():
    chain = ChainProvider([('gemini', Fake(quota=True)), ('groq', Fake(quota=True))])
    with pytest.raises(AIQuotaExceeded, match='gemini, groq'):
        asyncio.run(chain.generate_text('hi'))


def test_video_review_is_offered_only_while_a_video_capable_member_has_quota():
    gemini = GeminiProvider(api_key='AIzaSy' + 'A' * 30)
    chain = ChainProvider([('gemini', gemini), ('groq', Fake('x'))])
    assert hasattr(chain, 'analyze_video')                 # pipelines may use whole-video review
    from app.ai import gemini as g
    for model in gemini.models:
        for key in gemini.keys:
            g._exhausted[g.slot(key, model)] = 1e15        # every Gemini key/model spent
    assert not hasattr(chain, 'analyze_video')             # pipelines fall back to the frame-based review
    assert not hasattr(ChainProvider([('groq', Fake('x'))]), 'analyze_video')


# --- router -----------------------------------------------------------------
def test_auto_builds_a_chain_from_every_connected_provider_best_first():
    settings = {'ai_provider': 'auto', 'gemini_api_key': 'AIzaSy' + 'A' * 30, 'groq_api_key': GROQ_KEY, 'nvidia_api_key': NIM_KEY,
                'local_endpoint': 'http://127.0.0.1:9'}
    provider, name = asyncio.run(AIRouter.get_active_provider(settings))
    assert isinstance(provider, ChainProvider) and provider.names == ['gemini', 'nvidia'] and name == 'gemini'   # Groq's vision failed its check


def test_auto_with_one_provider_returns_it_directly_and_explicit_choices_are_respected():
    only = {'ai_provider': 'auto', 'nvidia_api_key': NIM_KEY, 'local_endpoint': 'http://127.0.0.1:9'}
    provider, name = asyncio.run(AIRouter.get_active_provider(only))
    assert isinstance(provider, NvidiaNIMProvider) and name == 'nvidia'
    groq_only = {'ai_provider': 'auto', 'groq_api_key': GROQ_KEY, 'local_endpoint': 'http://127.0.0.1:9'}
    assert asyncio.run(AIRouter.get_active_provider(groq_only))[0] is None            # not trusted to verify in AUTO...
    assert isinstance(asyncio.run(AIRouter.get_active_provider({**groq_only, 'ai_provider': 'groq'}))[0], GroqProvider)   # ...but selectable
    both = {**only, 'nvidia_api_key': NIM_KEY, 'ai_provider': 'nvidia', 'nvidia_model': 'custom/model'}
    provider, name = asyncio.run(AIRouter.get_active_provider(both))
    assert isinstance(provider, NvidiaNIMProvider) and provider.model == 'custom/model' and name == 'nvidia'
    with pytest.raises(ValueError, match='Groq is unavailable'):
        asyncio.run(AIRouter.get_active_provider({'ai_provider': 'groq', 'local_endpoint': 'http://127.0.0.1:9'}))


def test_status_reports_the_new_providers():
    settings = {'ai_provider': 'groq', 'groq_api_key': GROQ_KEY, 'local_endpoint': 'http://127.0.0.1:9'}
    status = asyncio.run(AIRouter.get_status(settings))
    assert status['is_ready'] and status['groq_configured'] and not status['nvidia_configured']
    assert status['active_model'] == 'qwen/qwen3.8-27b'
    auto = asyncio.run(AIRouter.get_status({'ai_provider': 'auto', 'nvidia_api_key': NIM_KEY, 'local_endpoint': 'http://127.0.0.1:9'}))
    assert auto['ranking_ready'] and 'NVIDIA' in auto['status_text']


def test_keys_are_stored_in_the_keychain_and_never_returned(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    from app.core import database
    client = TestClient(app, client=('127.0.0.1', 1))
    body = client.post('/api/settings', json={'groq_api_key': GROQ_KEY, 'nvidia_api_key': NIM_KEY, 'groq_model': 'llama-x'},
                       headers={'content-type': 'application/json'}).json()
    assert body['groq_api_key_configured'] and body['nvidia_api_key_configured'] and body['groq_model'] == 'llama-x'
    assert GROQ_KEY not in str(body) and NIM_KEY not in str(body)
    stored = database.get_settings()
    assert stored['groq_api_key'] == GROQ_KEY and stored['nvidia_api_key'] == NIM_KEY
    raw = database.get_connection().execute('SELECT groq_api_key, nvidia_api_key FROM settings').fetchone()
    assert tuple(raw) == ('keychain:groq_api_key', 'keychain:nvidia_api_key')
    assert client.get('/api/status').json()['groq_configured'] is True


def test_reasoning_text_is_removed_from_answers():
    from app.ai.compat_provider import strip_reasoning
    assert strip_reasoning('<think>let me look\nhmm</think>\n{"a": 1}') == '{"a": 1}'
    assert strip_reasoning('thinking without opener...</think>{"a": 1}') == '{"a": 1}'
    assert strip_reasoning('{"plain": true}') == '{"plain": true}' and strip_reasoning(None) is None
    provider = serve(GroqProvider, lambda r: ok('<think>x</think>{"ok": 1}'), api_key=GROQ_KEY)
    assert asyncio.run(provider.generate_text('hi')) == '{"ok": 1}'


def test_network_errors_are_retried_then_reported_not_raised():
    attempts = []
    def handler(request):
        attempts.append(1)
        raise httpx.ReadTimeout('read timed out', request=request)
    provider = serve(NvidiaNIMProvider, handler, api_key=NIM_KEY)
    assert asyncio.run(provider.generate_text('hi')) is None
    assert len(attempts) == 3 and provider.last_error.startswith('ReadTimeout')
    flaky = [None]
    def recovers(request):
        if flaky:
            flaky.pop()
            raise httpx.ConnectError('reset', request=request)
        return ok('back')
    assert asyncio.run(serve(NvidiaNIMProvider, recovers, api_key=NIM_KEY).generate_text('hi')) == 'back'

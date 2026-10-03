"""Auto Shorts: rank promising videos across platforms, produce whole-video Shorts, replace failures, report honestly."""
import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from app.core import database
from app.engines import auto_shorts as eng
from app.engines.auto_shorts import AutoShorts, duration_fit, queries_for, rank_candidates, search
from app.sources.discovery import SourceDiscovery
from app.sources.web_search import WebVideoSearch

NOW = 1_800_000_000


def cand(n, platform='youtube', views=None, age=None, duration=30, **extra):
    return {'id': f'{platform}{n}', 'url': f'https://{platform}.example/v/{n}', 'title': f'Clip {n}', 'platform': platform, 'view_count': views,
            'timestamp': None if age is None else NOW - age * 86400, 'duration': duration, 'creator': 'c', **extra}


# --- ranking -----------------------------------------------------------------
def test_duration_sweet_spot():
    assert duration_fit(30) == 1.0 and duration_fit(60) == 1.0
    assert 0 < duration_fit(10) < 1 and 0 < duration_fit(120) < 1 and duration_fit(None) == .5
    assert duration_fit(150) < duration_fit(90) < duration_fit(40)


def test_more_engaging_and_fresher_videos_rank_higher_within_a_platform():
    low = cand(1, views=500, age=300); mid = cand(2, views=40_000, age=20); high = cand(3, views=900_000, age=5)
    order = [c['title'] for c in rank_candidates([low, high, mid], NOW)]
    assert order == ['Clip 3', 'Clip 2', 'Clip 1']
    assert all(c['potential_reasons'] and 0 <= c['potential'] <= 1 for c in rank_candidates([low, high, mid], NOW))


def test_view_counts_from_different_platforms_are_not_compared_and_platforms_alternate():
    youtube = [cand(i, 'youtube', views=50_000_000 - i, age=None) for i in range(4)]
    daily = [cand(i, 'dailymotion', views=900 - i, age=3) for i in range(4)]
    reddit = [cand(i, 'reddit') for i in range(2)]
    ordered = rank_candidates(youtube + daily + reddit, NOW)
    first_round = {c['platform'] for c in ordered[:3]}
    assert first_round == {'youtube', 'dailymotion', 'reddit'}         # a 50M-view YouTube clip does not crowd out the rest
    assert [c['platform'] for c in ordered[:6]].count('youtube') == 2


def test_tiny_view_counts_are_not_momentum_even_when_they_beat_their_peers():
    daily = [cand(i, 'dailymotion', views=5 + i, age=3) for i in range(3)]
    youtube = [cand(i, 'youtube', views=2_000_000 - i, age=4) for i in range(3)]
    ordered = rank_candidates(daily + youtube, NOW)
    assert ordered[0]['platform'] == 'youtube'
    assert max(c['potential'] for c in ordered if c['platform'] == 'dailymotion') < min(c['potential'] for c in ordered if c['platform'] == 'youtube')


def test_unknown_numbers_rank_below_known_strong_ones_but_are_still_offered():
    ordered = rank_candidates([cand(1, 'reddit'), cand(2, 'reddit', views=100_000, age=2)], NOW)
    assert [c['title'] for c in ordered] == ['Clip 2', 'Clip 1']


def test_a_clip_with_ideal_length_beats_an_equally_popular_unwieldy_one():
    ordered = rank_candidates([cand(1, views=10_000, age=10, duration=140), cand(2, views=10_000, age=10, duration=30)], NOW)
    assert ordered[0]['title'] == 'Clip 2'


def test_queries():
    assert queries_for('funny', None) == eng.NICHES['funny']
    assert queries_for('extreme', 'Skate Recoveries')[0].lower().startswith('skate recover')
    assert queries_for('nonsense', '') == eng.NICHES['general']


# --- search ------------------------------------------------------------------
def test_search_runs_every_platform_and_filters_out_what_cannot_be_a_source(isolated_app, monkeypatch):
    def youtube(query, *a, **k):
        return [{'id': 'a', 'url': 'https://www.youtube.com/watch?v=aaaaaaaaaaa', 'title': 'Roof gap recovery', 'duration': 25, 'view_count': 9000},
                {'id': 'b', 'url': 'https://www.youtube.com/watch?v=bbbbbbbbbbb', 'title': 'Top 10 parkour saves', 'duration': 25},
                {'id': 'c', 'url': 'https://www.youtube.com/watch?v=ccccccccccc', 'title': 'Long one', 'duration': 900},
                {'id': 'd', 'url': 'https://www.youtube.com/watch?v=ddddddddddd', 'title': 'Too short', 'duration': 3},
                {'id': 'e', 'url': 'https://www.youtube.com/watch?v=eeeeeeeeeee', 'title': 'Minecraft parkour', 'duration': 25}]
    def other(query, platform):
        if platform == 'reddit':
            raise ValueError('blocked')
        return [{'url': f'https://{platform}.com/video/1', 'title': 'Goalie save', 'duration': 12, 'view_count': 80}]
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', youtube)
    monkeypatch.setattr(WebVideoSearch, 'search', other)
    out = search(['youtube', 'dailymotion', 'reddit'], ['moment'])
    titles = sorted(c['title'] for c in out['candidates'])
    assert titles == ['Goalie save', 'Roof gap recovery']
    assert out['warnings'] == [{'platform': 'reddit', 'error': 'ValueError'}]


def test_the_cap_applies_after_filtering_so_compilations_cannot_starve_a_platform(isolated_app, monkeypatch):
    compilations = [{'id': f'l{i}', 'url': f'https://www.youtube.com/watch?v={i:011d}', 'title': f'Long compilation {i}', 'duration': 900} for i in range(40)]
    good = [{'id': f'g{i}', 'url': f'https://www.youtube.com/watch?v=g{i:010d}', 'title': f'Roof gap recovery {i}', 'duration': 25} for i in range(5)]
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', lambda q, *a, **k: compilations + good)   # good ones are NOT in the first 12
    out = search(['youtube'], ['moment'], limit_per_search=3)
    assert len(out['candidates']) == 3 and all(c['title'].startswith('Roof gap') for c in out['candidates'])


def test_search_stops_waiting_once_there_is_a_big_enough_pool(isolated_app, monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', lambda q, *a, **k: [
        {'id': f'{q}{i}', 'url': f'https://www.youtube.com/watch?v={abs(hash(q + str(i))) % 10**11:011d}', 'title': f'Wall run {i}', 'duration': 25} for i in range(10)])
    def slow(q, p): release.wait(20); return []
    monkeypatch.setattr(WebVideoSearch, 'search', slow)
    began = time.monotonic()
    out = search(['youtube', 'vimeo'], ['a', 'b', 'c'], pool_target=10, min_wait=.5)
    release.set()
    assert time.monotonic() - began < 8 and len(out['candidates']) >= 10


def test_search_skips_footage_already_used_or_judged_unsuitable(isolated_app, monkeypatch):
    from app.sources import verdicts
    verdicts.remember({'youtube:aaaaaaaaaaa'}, 'moment', 'Existing countdown')
    monkeypatch.setattr(SourceDiscovery, 'search_public_shorts', lambda q, *a, **k: [
        {'id': 'a', 'url': 'https://www.youtube.com/watch?v=aaaaaaaaaaa', 'title': 'Roof gap', 'duration': 25},
        {'id': 'b', 'url': 'https://www.youtube.com/watch?v=bbbbbbbbbbb', 'title': 'Wall run', 'duration': 25}])
    out = search(['youtube'], ['moment'])
    assert [c['title'] for c in out['candidates']] == ['Wall run']


# --- real numbers for candidates that arrive without any ------------------------
def test_unknown_youtube_numbers_are_looked_up_and_unsuitable_lengths_dropped(monkeypatch):
    rows = [cand(1, 'youtube'), cand(2, 'youtube'), cand(3, 'youtube'), cand(4, 'dailymotion', views=40)]
    for r in rows: r['duration'] = None if r['platform'] == 'youtube' else r['duration']
    data = {'https://youtube.example/v/1': {'view_count': 2_500_000, 'duration': 28, 'timestamp': NOW - 86400 * 3},
            'https://youtube.example/v/2': {'view_count': 900, 'duration': 700, 'timestamp': NOW - 86400},     # a long video
            'https://youtube.example/v/3': None}                                                                # lookup fails
    def lookup(url):
        if data[url] is None: raise RuntimeError('Sign in to confirm')
        return data[url]
    monkeypatch.setattr(eng, 'fetch_metadata', lookup)
    result = eng.enrich(rows)
    assert result == {'enriched': 2, 'failed': 1}
    assert rows[0]['view_count'] == 2_500_000 and rows[0]['duration'] == 28
    assert rows[3]['view_count'] == 40                                      # other platforms are untouched
    kept = [c for c in rows if eng.usable_length(c)]
    assert [c['url'][-1] for c in kept] == ['1', '3', '4']                  # the 700 s video is gone; the unknown one stays
    ranked = rank_candidates(kept, NOW)
    assert ranked[0]['url'].endswith('/1')                                  # 2.5M views in 3 days leads


def test_metadata_lookup_has_a_deadline(monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(eng, 'fetch_metadata', lambda url: release.wait(20) or {})
    began = time.monotonic()
    result = eng.enrich([cand(i, 'youtube') for i in range(3)], deadline=.5)
    release.set()
    assert time.monotonic() - began < 5 and result == {'enriched': 0, 'failed': 3}


# --- orchestration -----------------------------------------------------------
@pytest.fixture
def run(isolated_app, monkeypatch):
    """Fake search results and a fake production that finishes or fails per URL."""
    monkeypatch.setattr(AutoShorts, 'POLL_SECONDS', .01)
    launched, outcome = [], {}
    results = [cand(i, ['youtube', 'dailymotion'][i % 2], views=1000 * (10 - i), age=2) for i in range(8)]
    def fake_search(platforms, queries):
        return {'candidates': results, 'warnings': []}
    monkeypatch.setattr(eng, 'search', fake_search)
    monkeypatch.setattr(eng, 'enrich', lambda candidates, **kw: {'enriched': 0, 'failed': 0})
    def launch(candidate, voice, provider, mode='viral'):
        suffix = f'{len(launched):02}'
        project_id, job_id = f'proj_v_{suffix}', f'job_{suffix}'
        database.create_project(project_id, 'viral', candidate['title'], {})
        database.create_job(job_id, project_id)
        launched.append((candidate['url'], voice, provider))
        world.modes.append(mode)
        result = outcome.get(candidate['url'], 'COMPLETED')
        if result == 'COMPLETED':
            database.create_or_update_clip(f'clip_{suffix}', project_id, job_id, candidate['title'], status='READY', duration=30,
                                           video_path=f'/output/viral/clip_{suffix}.mp4')
            database.update_job(job_id, status='COMPLETED', progress=100)
        else:
            database.update_job(job_id, status='FAILED')
            database.record_job_failure(job_id, ValueError("Couldn't download this video. Sign in to confirm you're not a bot"))
        return {'title': candidate['title'], 'url': candidate['url'], 'platform': candidate['platform'], 'project_id': project_id, 'job_id': job_id,
                'status': 'QUEUED', 'progress': 0, 'stage': 'Queued', 'clips': [], 'error': None, 'potential': candidate.get('potential')}
    monkeypatch.setattr(AutoShorts, '_launch', staticmethod(launch))
    class World: pass
    world = World(); world.launched, world.outcome, world.results, world.modes = launched, outcome, results, []
    def go(count=2, **kw):
        AutoShorts.state, AutoShorts.task = {'status': 'idle'}, None
        async def scenario():
            AutoShorts.start(count, **kw)
            await AutoShorts.task
            return AutoShorts.status()
        return asyncio.run(scenario())
    world.go = go
    return world


def test_produces_the_requested_number_from_the_best_candidates(run):
    state = run.go(2, voice='pocket:alba')
    assert state['status'] == 'done' and state['made'] == 2 and '2 Shorts ready' in state['message']
    assert len(run.launched) == 2 and all(v == 'pocket:alba' for _, v, _ in run.launched)
    assert {i['status'] for i in state['items']} == {'DONE'} and all(i['clips'][0]['status'] == 'READY' for i in state['items'])
    assert len({u.split('//')[1].split('.')[0] for u, _, _ in run.launched}) == 2           # platforms are mixed


def test_a_failed_download_is_replaced_by_the_next_best_video(run):
    first = rank_candidates(run.results)[0]['url']
    run.outcome[first] = 'FAILED'
    state = run.go(2)
    assert state['status'] == 'done' and state['made'] == 2
    assert [i['status'] for i in state['items']].count('FAILED') == 1 and len(run.launched) == 3
    failed = next(i for i in state['items'] if i['status'] == 'FAILED')
    assert 'sign-in' in (failed['error'] or '').lower() or 'Sign in' in (failed['error'] or '')


def test_it_gives_up_after_a_bounded_number_of_attempts_and_says_so(run):
    for c in run.results: run.outcome[c['url']] = 'FAILED'
    state = run.go(2)
    assert state['status'] == 'failed' and state['made'] == 0
    assert len(run.launched) == 2 * AutoShorts.MAX_ATTEMPTS_FACTOR                            # never an unbounded loop
    assert 'No Short could be produced' in state['message']


def test_nothing_found_is_a_clear_failure(isolated_app, monkeypatch):
    monkeypatch.setattr(eng, 'enrich', lambda candidates, **kw: {})
    monkeypatch.setattr(eng, 'search', lambda platforms, queries: {'candidates': [], 'warnings': [{'platform': 'reddit', 'error': 'ValueError'}]})
    AutoShorts.state, AutoShorts.task = {'status': 'idle'}, None
    async def scenario():
        AutoShorts.start(1)
        await AutoShorts.task
        return AutoShorts.status()
    state = asyncio.run(scenario())
    assert state['status'] == 'failed' and 'No usable videos' in state['error']


def test_only_one_run_at_a_time(run):
    async def scenario():
        AutoShorts.state, AutoShorts.task = {'status': 'idle'}, None
        first = AutoShorts.start(1)
        second = AutoShorts.start(3)
        await AutoShorts.task
        return first, second
    first, second = asyncio.run(scenario())
    assert first is second or second['wanted'] == first['wanted']


# --- the real launcher -------------------------------------------------------
def test_launch_creates_a_whole_video_voice_over_production(isolated_app, monkeypatch):
    from app.core.queue import JobEngine
    submitted = []
    # Patch the live instance: an earlier test can leave an instance attribute that would shadow a class-level patch.
    monkeypatch.setattr(JobEngine.get_instance(), 'submit_job', lambda job_id: submitted.append(job_id))
    async def scenario():
        return AutoShorts._launch({'url': 'https://youtube.com/watch?v=abc', 'title': 'Roof gap recovery', 'platform': 'youtube',
                                   'creator': 'Alice', 'view_count': 5000, 'duration': 22, 'potential': .8, 'potential_reasons': '5,000 views'},
                                  'pocket:marius', 'auto')
    item = asyncio.run(scenario())
    project = database.get_project(item['project_id'])
    data = project['input_data']
    assert project['mode'] == 'viral' and submitted == [item['job_id']]
    assert data['whole_video'] is True and data['force_commentary'] is True and data['captions'] is True and data['layout'] == 'smart'
    assert data['video_url'] == 'https://youtube.com/watch?v=abc' and data['default_voice'] == 'pocket:marius'


# --- API ---------------------------------------------------------------------
def test_api_validates_and_reports(isolated_app, monkeypatch):
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 5000))
    headers = {'content-type': 'application/json'}
    assert client.get('/api/auto-shorts/options').json()['platforms'] == ['youtube', 'dailymotion', 'reddit', 'vimeo']
    assert client.post('/api/auto-shorts', json={'count': 9}, headers=headers).status_code == 422
    assert client.post('/api/auto-shorts', json={'platforms': ['myspace']}, headers=headers).status_code == 422
    assert client.post('/api/auto-shorts', json={'voice': 'robot-voice'}, headers=headers).status_code == 400
    AutoShorts.state, AutoShorts.task = {'status': 'idle'}, None
    assert client.get('/api/auto-shorts/status').json()['status'] == 'idle'
    assert TestClient(app, client=('198.51.100.7', 1)).get('/api/auto-shorts/status').status_code == 403


# --- the two modes ----------------------------------------------------------
def launch_item(mode):
    from app.core.queue import JobEngine
    monkeypatch_target = JobEngine.get_instance()
    original = monkeypatch_target.submit_job
    monkeypatch_target.submit_job = lambda job_id: None
    try:
        async def scenario():
            return AutoShorts._launch({'url': 'https://youtube.com/watch?v=abc', 'title': 'Soldier surprises his mother', 'platform': 'youtube',
                                       'creator': 'Alice', 'view_count': 5000, 'duration': 40}, 'pocket:alba', 'auto', mode)
        item = asyncio.run(scenario())
    finally:
        del monkeypatch_target.submit_job          # restore the class method
    return database.get_project(item['project_id']), item


def test_viral_mode_adds_commentary_and_no_music(isolated_app):
    project, _ = launch_item('viral')
    data = project['input_data']
    assert data['force_commentary'] is True and not data.get('no_narration') and not data.get('music') and not data.get('heading')
    assert project['title'].startswith('Auto Short')


def test_emotional_mode_adds_a_heading_and_sad_music_and_no_voice_over(isolated_app):
    project, _ = launch_item('emotional')
    data = project['input_data']
    assert data['no_narration'] is True and data['force_commentary'] is False
    assert data['heading'] is True and data['music'] == 'sad' and data['style'] == 'emotional'
    assert data['whole_video'] is True and data['captions'] is True and data['layout'] == 'smart'
    assert project['title'].startswith('Emotional Short')


def test_emotional_mode_searches_emotional_topics_and_prefers_longer_stories():
    assert queries_for('extreme', None, 'emotional') == eng.NICHES['emotional']
    assert queries_for('extreme', 'soldier homecoming', 'emotional')[0].lower().startswith('soldier homecoming')
    assert queries_for('extreme', None, 'viral') == eng.NICHES['extreme']
    assert duration_fit(70, eng.SWEET_SPOTS['emotional']) == 1.0 and duration_fit(70, eng.SWEET_SPOTS['viral']) < 1.0
    ordered = rank_candidates([cand(1, views=9000, age=5, duration=12), cand(2, views=9000, age=5, duration=45)], NOW, mode='emotional')
    assert ordered[0]['title'] == 'Clip 2'


def test_the_chosen_mode_reaches_every_production(run):
    state = run.go(2, mode='emotional')
    assert state['mode'] == 'emotional' and run.modes == ['emotional', 'emotional']
    assert run.go(1)['mode'] == 'viral'


def test_api_accepts_the_mode(isolated_app):
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 5000))
    headers = {'content-type': 'application/json'}
    assert client.get('/api/auto-shorts/options').json()['modes'] == ['viral', 'emotional', 'sad_visual']
    assert client.post('/api/auto-shorts', json={'mode': 'sad-only'}, headers=headers).status_code == 422


def test_sad_visual_mode_removes_source_sound_and_uses_only_heading_and_music(isolated_app):
    project, _ = launch_item('sad_visual')
    data = project['input_data']
    assert data['no_narration'] is True and data['force_commentary'] is False and data['visuals_only'] is True and data['mute_source'] is True
    assert data['heading'] is True and data['music'] == 'sad' and data['style'] == 'sad_visual' and data['whole_video'] is True
    assert project['title'].startswith('Sad visual Short')

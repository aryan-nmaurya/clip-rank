"""Money and learning use recorded data only, and refuse to over-learn from thin or lucky results."""
import math
import time
import pytest
from fastapi.testclient import TestClient
from app.business import dashboard, learning, store
from app.core import database


@pytest.fixture
def biz(isolated_app):
    return isolated_app


# --- ledger -----------------------------------------------------------------
def test_dashboard_with_no_records_shows_zero_not_invented_income(biz):
    data = dashboard.build(channel=None)
    assert data['revenue']['total'] == 0 and data['net']['amount'] == 0 and data['costs']['recorded'] == 0
    assert data['growth']['tracked_views'] is None and data['growth']['subscribers_gained_tracked'] is None
    assert data['monetization']['subscribers'] is None and 'guarantees nothing' in data['monetization']['note']


def test_net_is_recorded_revenue_minus_recorded_costs_only(biz):
    store.add_entry('revenue', 'platform', 120.5, 'YouTube Studio September payout')
    store.add_entry('revenue', 'sponsorship', 300, 'Invoice 17, signed brand deal')
    store.add_entry('cost', 'storage', 9.99, 'Extra drive')
    store.add_entry('cost', 'cloud_ai', 4, 'Gemini invoice')
    data = dashboard.build(channel=None)
    assert data['revenue']['total'] == 420.5 and data['sponsorship']['revenue'] == 300
    assert data['costs']['recorded'] == 13.99 and data['net']['amount'] == 406.51


@pytest.mark.parametrize('args', [
    ('revenue', 'platform', -1, 'refund'), ('revenue', 'bogus', 1, 'x note'), ('cost', 'platform', 1, 'wrong list'),
    ('revenue', 'platform', 5, 'no'), ('revenue', 'platform', float('nan'), 'not a number'),
    ('revenue', 'platform', 5, 'dated in the future', '2999-01-01'), ('revenue', 'platform', 5, 'bad date here', '13/45/2025')])
def test_invalid_or_projected_entries_are_rejected(biz, args):
    with pytest.raises(ValueError):
        store.add_entry(*args)
    assert store.entries() == []


def test_ai_cost_appears_only_after_you_configure_prices(biz):
    store.record_ai_usage('gemini', 'flash', 2_000_000, 500_000)
    summary = store.ai_usage_summary()
    assert summary['input_tokens'] == 2_000_000 and summary['estimated_cost'] is None and not summary['priced']
    store.set_config({'ai_input_price_per_million': 0.10, 'ai_output_price_per_million': 0.40})
    assert store.ai_usage_summary()['estimated_cost'] == pytest.approx(0.4)
    assert dashboard.build(channel=None)['costs']['recorded'] == pytest.approx(0.4)


def test_config_is_validated(biz):
    for bad in ({'ypp_status': 'rich'}, {'currency': 'dollars'}, {'ai_input_price_per_million': -3}, {'unknown': 1}):
        with pytest.raises(ValueError):
            store.set_config(bad)
    assert store.set_config({'ypp_status': 'applied', 'currency': 'EUR'})['ypp_status'] == 'applied'


# --- affiliates -------------------------------------------------------------
def test_affiliate_requires_https_topics_and_disclosure(biz):
    for args in (('Acme', 'http://acme.test/ref', ['shoes'], 'Affiliate link: I may earn a commission.'),
                 ('Acme', 'https://acme.test/ref', [], 'Affiliate link: I may earn a commission.'),
                 ('Acme', 'https://acme.test/ref', ['shoes'], 'ad'), ('', 'https://acme.test/ref', ['shoes'], 'Affiliate link: I may earn a commission.')):
        with pytest.raises(ValueError):
            store.add_affiliate(*args)


def test_affiliate_link_is_inserted_only_when_content_relates(biz):
    store.add_affiliate('Acme Grip', 'https://acme.test/ref?id=1', ['parkour shoes', 'climbing chalk'], 'Affiliate link: I may earn a commission.')
    assert store.matching_affiliate_block('A runner nails a wall flip on the rooftop') == ''
    block = store.matching_affiliate_block('Close call: his parkour shoes lose grip on the rail')
    assert 'I may earn a commission' in block and 'https://acme.test/ref?id=1' in block
    program = store.affiliates()[0]
    store.set_affiliate_enabled(program['id'], False)
    assert store.matching_affiliate_block('parkour shoes everywhere') == ''


def test_unrelated_entertainment_description_never_contains_an_affiliate_link(biz, monkeypatch):
    from app.publishing import youtube
    store.add_affiliate('Acme Grip', 'https://acme.test/ref', ['climbing chalk'], 'Affiliate link: I may earn a commission.')
    database.create_project('p', 'ranking', 'Ranking parkour saves')
    database.create_job('j', 'p')
    database.create_or_update_clip('c', 'p', 'j', 'Parkour save', status='READY')
    assert 'acme.test' not in youtube.description_preview('c')['description']


# --- learning ---------------------------------------------------------------
def video(views, **creative):
    return {'video_id': f"v{len(creative)}{views}", 'views': views, 'creative': creative}


def history(n_a, views_a, n_b, views_b, extra=()):
    return [{'video_id': f'a{i}', 'views': views_a, 'creative': {'engine': 'ranking', 'duration_band': '20–30s'}} for i in range(n_a)] + \
           [{'video_id': f'b{i}', 'views': views_b, 'creative': {'engine': 'viral', 'duration_band': '30–45s'}} for i in range(n_b)] + list(extra)


def test_no_learning_from_too_few_videos():
    result = learning.analyze(history(3, 5000, 3, 500))
    assert not result['ready'] and result['adjustments'] == [] and 'will not learn' in result['message']


def test_clear_pattern_produces_bounded_explained_adjustments():
    result = learning.analyze(history(6, 20000, 6, 1000))
    assert result['ready']
    best = next(a for a in result['adjustments'] if a['dimension'] == 'engine' and a['value'] == 'ranking')
    worst = next(a for a in result['adjustments'] if a['dimension'] == 'engine' and a['value'] == 'viral')
    assert 0 < best['weight'] <= learning.MAX_WEIGHT and -learning.MAX_WEIGHT <= worst['weight'] < 0
    assert best['sample_size'] == 6 and 'median reach' in best['reason'] and 'vs channel median' in best['reason']


def test_one_viral_outlier_does_not_change_strategy():
    rows = history(5, 1000, 5, 1000, extra=[{'video_id': 'lucky', 'views': 50_000_000, 'creative': {'engine': 'viral', 'duration_band': '30–45s'}}])
    result = learning.analyze(rows)
    assert result['ready']
    assert all(abs(a['weight']) < 0.1 for a in result['adjustments']), result['adjustments']


def test_groups_below_the_minimum_sample_are_ignored():
    rows = history(7, 8000, 7, 800) + [{'video_id': 'x', 'views': 99999, 'creative': {'engine': 'movie', 'duration_band': '≤20s'}},
                                        {'video_id': 'y', 'views': 99999, 'creative': {'engine': 'movie', 'duration_band': '≤20s'}}]
    values = {a['value'] for a in learning.analyze(rows)['adjustments']}
    assert 'movie' not in values and '≤20s' not in values


def test_a_dimension_with_a_single_value_teaches_nothing():
    rows = [{'video_id': f'v{i}', 'views': 100 * (i + 1), 'creative': {'engine': 'ranking'}} for i in range(10)]
    assert learning.analyze(rows)['adjustments'] == []


def test_snapshots_use_the_right_window_and_prefer_the_latest_window(biz):
    now = time.time()
    for hours, views in ((3, 10), (30, 40), (100, 90), (200, 300)):
        window = learning.record_snapshot('vid', now - hours * 3600, {'views': views, 'creative': {'engine': 'ranking'}}, now=now)
    assert window == '7d'
    learning.record_snapshot('vid', now - 3 * 3600, {'views': 12}, now=now)   # a later early-window reading must not replace 7d
    chosen = learning.best_snapshots()
    assert len(chosen) == 1 and chosen[0]['window'] == '7d' and chosen[0]['views'] == 300


def test_refresh_persists_adjustments_with_reasons(biz):
    for row in history(6, 20000, 6, 1000):
        learning.record_snapshot(row['video_id'], time.time() - 8 * 86400, {'views': row['views'], 'creative': row['creative']})
    learning.refresh()
    stored = learning.current_adjustments()
    assert stored and all(a['reason'] for a in stored) and all(abs(a['weight']) <= learning.MAX_WEIGHT for a in stored)


def test_creative_metadata_records_the_choices_behind_a_video():
    clip = {'id': 'c1', 'duration': 24.0}
    project = {'mode': 'ranking', 'title': 'Ranking Parkour', 'input_data': {'layout': 'fit'}, 'result_data': {
        'verified_topic': 'Parkour saves', 'variants': [{'clip_id': 'c1', 'hook': 'Parkour saves?', 'timeline': [{'speech': {'voice': 'alba'}}]}]}}
    meta = learning.creative_metadata(clip, project)
    assert meta['duration_band'] == '20–30s' and meta['narration'] == 'narrated' and meta['voice'] == 'alba'
    assert meta['hook_type'] == 'question' and meta['ranking'] is True and meta['engine'] == 'ranking'


# --- API --------------------------------------------------------------------
def test_business_api_is_local_and_validates(biz):
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 5000))
    headers = {'content-type': 'application/json'}
    assert client.post('/api/business/ledger', json={'kind': 'revenue', 'category': 'platform', 'amount': 12.5, 'note': 'Studio payout'}, headers=headers).status_code == 200
    assert client.post('/api/business/ledger', json={'kind': 'revenue', 'category': 'nope', 'amount': 1, 'note': 'bad category'}, headers=headers).status_code == 400
    assert client.get('/api/business/ledger').json()['entries'][0]['amount'] == 12.5
    assert client.post('/api/business/config', json={'ypp_status': 'approved'}, headers=headers).json()['ypp_status'] == 'approved'
    assert TestClient(app, client=('198.51.100.9', 1)).get('/api/business/dashboard').status_code == 403
    assert client.delete('/api/business/ledger/9999', headers=headers).status_code == 404

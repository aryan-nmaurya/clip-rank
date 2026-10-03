"""Relaxed/Balanced/Strict change how sure the AI must be - never the objective checks on the file."""
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from app.ai.production_director import ProductionDirector
from app.ai.ranking_verifier import RankingVerifier
from app.studio import store, visual_discovery as vd
from app.studio.director import ContentDirector
from app.studio.strictness import CRITICAL_FINAL_CHECKS, LEVELS, level, production_level

pytestmark = pytest.mark.usefixtures('studio')


@pytest.fixture
def studio(isolated_app):
    store.init_studio()
    return isolated_app


def test_levels_get_progressively_more_permissive():
    s, b, r = LEVELS['strict'], LEVELS['balanced'], LEVELS['relaxed']
    assert s.min_confidence > b.min_confidence > r.min_confidence
    assert s.second_review and not b.second_review and not r.second_review
    assert not s.final_critical_only and b.final_critical_only and r.final_critical_only
    assert s.dimension_min > b.dimension_min > r.dimension_min


def test_default_is_relaxed_and_unreadable_settings_fall_back_to_strict(studio):
    assert level().name == 'relaxed' and production_level().name == 'relaxed'
    assert store.profile().discovery_strictness == 'relaxed' and store.profile().production_strictness == 'relaxed'


def test_without_the_studio_tables_behaviour_is_the_original_strict_one(tmp_path, monkeypatch):
    from app.core import database
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'bare.db')      # no studio tables at all
    assert production_level().name == 'strict'


def moments(n=1):
    return [{'start': 0.0, 'end': 10.0} for _ in range(n)]


def verdict(**over):
    base = {'id': 0, 'matches_topic': True, 'complete_action': True, 'already_ranked': False, 'graphic_injury': False,
            'topic_relevance': .72, 'confidence': .72, 'observed_action': 'A runner recovers on the rail', 'topic_evidence': 'visible recovery',
            'label': 'Rail recovery', 'commentary': 'He nearly falls then catches the rail.', 'event_start': 1.0, 'payoff_time': 5.0,
            'event_end': 8.0, 'score': 70, 'reason': 'Clear visible recovery'}
    return {**base, **over}


class Say:
    def __init__(self, payload): self.payload = payload
    async def analyze_images(self, images, prompt, **kw): return json.dumps(self.payload)
    async def generate_text(self, prompt, **kw): return json.dumps(self.payload)


def evaluate(item, **kw):
    return asyncio.run(RankingVerifier.evaluate(Say({'moments': [item]}), 'fx', moments(), 'sheet.jpg', 'parkour', **kw))


def test_certainty_bar_follows_the_level():
    assert evaluate(verdict()) == []                                                          # 72%: strict (default) says no
    assert evaluate(verdict(), min_confidence=LEVELS['relaxed'].min_confidence)               # relaxed accepts
    assert evaluate(verdict(topic_relevance=.5, confidence=.5), min_confidence=LEVELS['relaxed'].min_confidence) == []   # but not a guess


def test_lenient_mode_trims_instead_of_discarding_and_re_anchors_the_event():
    messy = verdict(commentary=' '.join(['word'] * 20), label='x', event_start=-3, payoff_time=99, event_end=100)
    assert evaluate(messy, min_confidence=.65) == []                                           # strict: dropped
    kept = evaluate(messy, min_confidence=.65, lenient=True)[0]
    assert len(kept['commentary'].split()) == 12 and len(kept['label'].split()) >= 2
    assert 0 <= kept['event_start'] < kept['payoff_time'] <= kept['event_end'] <= 10


def test_graphic_injury_and_wrong_topic_are_rejected_at_every_level():
    for level_name in LEVELS.values():
        assert evaluate(verdict(confidence=.99, topic_relevance=.99, graphic_injury=True), min_confidence=level_name.min_confidence, lenient=True) == []
        assert evaluate(verdict(confidence=.99, topic_relevance=.99, matches_topic=False), min_confidence=level_name.min_confidence, lenient=True) == []


def review(observed, check, topic='parkour moments', level=None):
    calls = iter([observed, check])
    class P:
        async def analyze_images(self, *a, **k): return json.dumps(next(calls))
    return asyncio.run(RankingVerifier.review(P(), 'fx', {'label': 'Rail recovery', 'commentary': 'x'}, 'sheet.jpg', topic, level=level))


OBS = {'observed_action': 'A runner slips on the rail then recovers', 'outcome': 'uncertain', 'complete_action': True, 'confidence': .7, 'reason': 'r'}
CHK = {'matches_topic': True, 'complete_action': True, 'label_matches': False, 'commentary_matches': False, 'confidence': .7, 'topic_evidence': 'visible', 'reason': 'ok'}


def test_relaxed_review_tolerates_uncertain_outcome_and_label_wording_strict_does_not():
    assert not review(OBS, CHK)['passed']                                         # strict default
    assert review(OBS, CHK, level=LEVELS['relaxed'])['passed']
    assert not review(OBS, CHK, level=LEVELS['balanced'])['passed']              # balanced: still needs a decided outcome and labels
    assert not review(OBS, CHK, topic='parkour fails', level=LEVELS['relaxed'])['passed']   # a 'fail' topic still needs a visible failure


def final_json(**over):
    base = {k: True for k in ('topic_matches', 'hook_honest', 'payoffs_complete', 'rank_order_correct', 'escalation_valid', 'narration_grounded',
                              'captions_readable', 'framing_safe', 'production_finished', 'no_graphic_injury', 'commentary_preserves_payoff')}
    return {**base, 'confidence': .7, 'reason': 'The runner clears two rails and lands softly.', 'issues': [],
            'observations': [{'rank': None, 'time': 1.0, 'visible_event': 'Runner clears rail'}], **over}


TIMELINE = [{'rank': None, 'timeline_start': 0, 'duration': 10}]


def final(payload, level=None):
    return asyncio.run(ProductionDirector.final_review(Say(payload), 'fx', 'v.mp4', [], 'parkour', TIMELINE, level=level))


def test_final_review_relaxed_ignores_soft_style_checks_but_never_critical_ones():
    soft_fail = final_json(hook_honest=False, escalation_valid=False, narration_grounded=False)
    assert not final(soft_fail)['passed']                                          # strict
    assert final(soft_fail, LEVELS['relaxed'])['passed']
    assert final(final_json(issues=['slightly tight caption']), LEVELS['relaxed'])['passed']
    assert not final(final_json(issues=['slightly tight caption']))['passed']
    for check in CRITICAL_FINAL_CHECKS:                                            # every critical check still blocks, at every level
        for lv in LEVELS.values():
            assert not final(final_json(**{check: False}), lv)['passed'], (check, lv.name)
    assert not final(final_json(confidence=.5), LEVELS['relaxed'])['passed']      # and the model must still be reasonably sure


def test_discovery_readiness_by_level(studio):
    item = {'opportunity_type': 'visual_moment', 'real_world_action': True, 'topic_matches': True, 'moment_verified': True,
            'source_url': 'https://example.org/v', 'creator': 'Someone',
            'dimensions': {k: 68 for k in ContentDirector.KEY_DIMENSIONS}}
    profile = store.profile().model_copy(update={'rights_policy': 'user_managed'})
    assert not ContentDirector.qualified(item, profile)                                       # original strict bar: every score >= 80
    assert not ContentDirector.qualified(item, profile, level=LEVELS['strict'])
    assert not ContentDirector.qualified(item, profile, level=LEVELS['balanced'])
    assert ContentDirector.qualified(item, profile, level=LEVELS['relaxed'])


def test_scoring_bar_follows_the_level():
    d = {k: 90 for k in ContentDirector.WEIGHTS}
    d['clarity'] = 60
    value = {'real_world_action': True, 'topic_matches': True, 'one_second_interest': False, 'context_needed': False, 'confidence': .7,
             'reason': 'A runner slips then recovers on the rail with a clear payoff.', 'dimensions': d}
    with pytest.raises(ValueError):
        asyncio.run(vd.VisualDiscovery.score(Say(value), {'observed_action': 'a', 'start': 0, 'payoff_time': 1, 'end': 2}, 's', 'c', 't'))
    assert asyncio.run(vd.VisualDiscovery.score(Say(value), {'observed_action': 'a', 'start': 0, 'payoff_time': 1, 'end': 2}, 's', 'c', 't', LEVELS['relaxed']))
    value['real_world_action'] = False                         # a video game is never acceptable
    with pytest.raises(ValueError):
        asyncio.run(vd.VisualDiscovery.score(Say(value), {'observed_action': 'a', 'start': 0, 'payoff_time': 1, 'end': 2}, 's', 'c', 't', LEVELS['relaxed']))


def test_search_phrases_are_broad_enough_to_find_footage():
    assert vd.QUERIES['parkour_freerunning'] == 'insane parkour moment'
    assert not any(w in ' '.join(vd.QUERIES.values()) for w in ('goalkeeper epic save', 'parkour fail'))


def test_strictness_api_sets_scope_and_lists_what_never_relaxes(studio):
    from app.api.server import app
    client = TestClient(app, client=('127.0.0.1', 5000))
    headers = {'content-type': 'application/json'}
    assert client.get('/api/studio/strictness').json()['discovery'] == 'relaxed'
    body = client.post('/api/studio/strictness', json={'level': 'strict', 'scope': 'discovery'}, headers=headers).json()
    assert (body['discovery'], body['production']) == ('strict', 'relaxed')
    body = client.post('/api/studio/strictness', json={'level': 'balanced'}, headers=headers).json()
    assert (body['discovery'], body['production']) == ('balanced', 'balanced')
    assert any('graphic injury' in x for x in body['always_enforced']) and any('black or frozen' in x for x in body['always_enforced'])
    assert client.post('/api/studio/strictness', json={'level': 'anything'}, headers=headers).status_code == 422

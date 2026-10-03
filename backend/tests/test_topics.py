import pytest
from fastapi.testclient import TestClient
from app.pipelines.ranking import topics


def test_catalog_has_twenty_of_each_and_no_duplicates():
    assert len(topics.BROAD) == 20 and len(topics.NARROW) == 20
    names = [t['topic'].lower() for t in topics.BROAD + topics.NARROW]
    assert len(set(names)) == 40


def test_every_catalog_topic_classifies_as_its_own_breadth():
    for item in topics.BROAD:
        assert topics.analyze(item['topic'])['breadth'] == 'broad', item
    for item in topics.NARROW:
        assert topics.analyze(item['topic'])['breadth'] in ('narrow', 'medium'), item


@pytest.mark.parametrize('topic,breadth', [('Craziest Parkour Saves', 'narrow'), ('Top 5 Impossible Goalkeeper Saves', 'narrow'),
                                           ('Parkour fails', 'narrow'), ('Insane Parkour Moments', 'broad'),
                                           ('Funny cat moments', 'broad'), ('Football saves', 'narrow')])
def test_breadth_of_typical_topics(topic, breadth):
    assert topics.analyze(topic)['breadth'] == breadth


def test_narrow_topics_explain_themselves_and_offer_a_broader_one():
    result = topics.analyze('Craziest Parkour Saves')
    assert 'saves' in result['reasons'][0] and result['broader'] == 'Insane Parkour Moments'
    assert topics.analyze('Insane Parkour Moments')['broader'] is None
    assert topics.analyze('Impossible Goalkeeper Saves')['broader'] == 'Insane Goalkeeper Moments'


def test_broader_suggestions_are_themselves_broad():
    for item in topics.NARROW:
        suggestion = topics.analyze(item['topic'])['broader']
        assert suggestion and topics.analyze(suggestion)['breadth'] == 'broad', (item, suggestion)


def test_filter_endpoint():
    from app.api.server import app
    client = TestClient(app)
    assert len(client.get('/api/ranking/topics').json()['topics']) == 40
    broad = client.get('/api/ranking/topics?breadth=broad').json()['topics']
    assert len(broad) == 20 and {t['breadth'] for t in broad} == {'broad'}
    assert client.get('/api/ranking/topics?breadth=medium').status_code == 400
    assert client.get('/api/ranking/topics/analyze', params={'topic': 'Parkour fails'}).json()['breadth'] == 'narrow'

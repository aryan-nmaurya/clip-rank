import asyncio
import json
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from app.ai.router import AIRouter
from app.ai.ranking_verifier import RankingVerifier
from app.ai.local_provider import LocalProvider
from app.api.server import app
from app.core.database import create_project, create_job, get_project
from app.pipelines.ranking.ranking_pipeline import RankingPipeline
from app.pipelines.ranking.topic_parser import TopicParser
from app.sources.discovery import SourceDiscovery


def decision(idx=0, **overrides):
    return {'id':idx,'matches_topic':True,'complete_action':True,'already_ranked':False,'graphic_injury':False,
            'topic_relevance':.97,'confidence':.96,'score':70,'label':'Runner misses landing',
            'commentary':'The runner misses the landing and falls.',
            'observed_action':'A failed parkour landing is visible','topic_evidence':'The runner falls after missing the rail',
            'event_start':1,'payoff_time':3,'event_end':5,
            'reason':'The failed landing and outcome are visible',**overrides}


class Reply:
    def __init__(self, value): self.value=value
    async def analyze_images(self,images,prompt):
        self.prompt=prompt
        return json.dumps(self.value) if self.value is not None else None


def test_wrong_topic_high_score_cannot_win_and_omitted_rows_are_not_reused():
    moments=[{'start':0,'end':7,'title':'Parkour fails'},{'start':7,'end':14,'title':'Untrusted title'},
             {'start':14,'end':21,'title':'Parkour fails'}]
    provider=Reply({'moments':[decision(0,matches_topic=False,score=99),decision(1,score=65,event_start=8,payoff_time=10,event_end=12)]})
    result=asyncio.run(RankingVerifier.evaluate(provider,'test',moments,Path('unused.jpg'),'Parkour fails'))
    assert len(result)==1 and result[0]['start']==7
    assert result[0]['label']=='Runner misses landing'
    assert 'Untrusted title' not in provider.prompt
    assert result[0]['verified_topic']=='Parkour fails'


@pytest.mark.parametrize('overrides',[
    {'topic_relevance':None},{'confidence':float('nan')},{'confidence':.6},
    {'matches_topic':'true'},{'complete_action':False},{'already_ranked':True},{'graphic_injury':True},{'graphic_injury':None},
    {'label':''},{'commentary':''},{'topic_evidence':''},
])
def test_uncertain_or_incomplete_topic_evidence_is_rejected(overrides):
    result=asyncio.run(RankingVerifier.evaluate(Reply({'moments':[decision(**overrides)]}),
        'test',[{'start':0,'end':7,'title':'Parkour fails'}],Path('unused.jpg'),'Parkour fails'))
    assert result==[]


def test_missing_topic_score_does_not_default_to_a_match():
    item=decision()
    del item['topic_relevance']
    assert asyncio.run(RankingVerifier.evaluate(Reply({'moments':[item]}),'test',
        [{'start':0,'end':7}],Path('unused.jpg'),'Parkour fails'))==[]


def test_unavailable_vision_never_uses_motion_scores():
    with pytest.raises(ValueError,match='cannot use unverified footage'):
        asyncio.run(RankingVerifier.evaluate(Reply(None),'test',[{'start':0,'end':7}],Path('unused.jpg'),'Parkour fails'))


def test_label_mismatch_fails_independent_cut_review():
    review=asyncio.run(RankingVerifier.review(Reply({'matches_topic':True,'complete_action':True,
        'label_matches':False,'commentary_matches':True,'confidence':.99,
        'topic_evidence':'A successful landing is visible','reason':'Label claims a failure that is not visible'}),
        'test',{'label':'Runner fails vault','commentary':'The runner falls.'},Path('unused.jpg'),'Parkour fails'))
    assert not review['passed']


def test_disconnected_ranking_stops_before_downloads(isolated_app,monkeypatch):
    async def no_ai(*args,**kwargs): return None,'fallback'
    monkeypatch.setattr(AIRouter,'get_active_provider',no_ai)
    def no_download(*args,**kwargs): raise AssertionError('Unverified ranking must not download footage')
    monkeypatch.setattr(SourceDiscovery,'discover_candidate_videos',no_download)
    create_project('no_vision','ranking','Parkour fails')
    create_job('no_vision_job','no_vision')
    with pytest.raises(ValueError,match='connected vision model'):
        asyncio.run(RankingPipeline.run('no_vision_job','no_vision','Parkour fails',3,{},lambda *a:None))
    assert get_project('no_vision')['status']=='FAILED'
    assert not list(isolated_app['ranking'].glob('*.mp4'))


def test_cut_review_failure_cannot_render(isolated_app,footage,ranking_vision,monkeypatch):
    original=ranking_vision.analyze_images
    async def wrong_label(images,prompt):
        if prompt.startswith('Independently check'):
            return json.dumps({'matches_topic':True,'complete_action':True,'label_matches':False,
                'commentary_matches':False,'confidence':.99,'topic_evidence':'The label does not describe this scene',
                'reason':'Wrong label and commentary'})
        return await original(images,prompt)
    monkeypatch.setattr(ranking_vision,'analyze_images',wrong_label)
    create_project('bad_labels','ranking','Test patterns')
    create_job('bad_labels_job','bad_labels')
    with pytest.raises(ValueError,match='Only 0 individual clips'):
        asyncio.run(RankingPipeline.run('bad_labels_job','bad_labels','Test patterns',3,
            {'source_files':[str(p) for p in footage],'segment_duration':3},lambda *a:None))
    project=get_project('bad_labels')
    assert project['status']=='FAILED'
    assert len(project['result_data']['rejected_sources'])==3
    assert not list(isolated_app['ranking'].glob('*.mp4'))


@pytest.mark.parametrize('capabilities,expected',[(['completion'],False),(['completion','vision'],True)])
def test_local_setup_checks_vision_without_downloading_models(monkeypatch,capabilities,expected):
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/api/tags': return httpx.Response(200,json={'models':[{'name':'qwen3-vl:4b'}]})
        if request.url.path=='/api/show': return httpx.Response(200,json={'capabilities':capabilities})
        raise AssertionError('Model download or generation must not be requested during connection discovery')
    client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client(transport=httpx.MockTransport(handler),**kwargs))
    assert asyncio.run(LocalProvider().is_available()) is expected
    assert calls==[('GET','/api/tags'),('POST','/api/show')]


def test_api_blocks_unverified_ranking_and_reports_disconnected_setup(isolated_app,monkeypatch):
    async def no_ai(*args,**kwargs): return None,'fallback'
    monkeypatch.setattr(AIRouter,'get_active_provider',no_ai)
    with TestClient(app) as client:
        response=client.post('/api/projects/ranking',json={'topic':'Parkour fails','count':3})
        assert response.status_code==400 and 'vision model' in response.json()['detail']
        test=client.post('/api/vision/test',json={'provider':'gemini','gemini_api_key':''})
        assert test.status_code==200 and test.json()['passed'] is False
        assert client.get('/api/vision/connections').json()['local']['vision_capable'] is False


def test_topic_parser_keeps_the_actual_subject_and_outcome():
    assert TopicParser.parse_topic('Ranking Top 5 Parkour fails')==('Parkour fails',5)
    assert TopicParser.parse_topic('Cat fails while trying to reach the top shelf')[0]=='Cat fails while trying to reach the top shelf'

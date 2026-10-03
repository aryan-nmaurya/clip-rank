import asyncio
import json
from unittest.mock import Mock
import pytest
from app.studio import store
from app.studio.models import ChannelProfile,PILLARS,CHANNEL_NAME,LEGACY_PILLARS
from app.studio.director import ContentDirector
from app.studio.policy import RightsPolicyEngine,OriginalityEngine


@pytest.fixture
def visual(isolated_app):
    store.init_studio();return isolated_app


def moment(i=0,category='parkour_freerunning',score=90,context=False):
    return store.put_opportunity({'topic':f'Visible parkour event {i}','category':category,
        'source_url':f'https://www.youtube.com/watch?v=fixture{i:04}','creator':'Explicit test creator',
        'verified_topic':'parkour fail','moment':{'start':2.,'end':12.,'payoff_time':8.,'observed_action':'A visible failed landing'},
        'moment_verified':True,'one_second_interest':True,'context_needed':context,
        'dimensions':{k:score for k in ContentDirector.WEIGHTS},'reason':'A clear jump leads to a visible surprising missed landing.'})


def test_default_migration_keeps_activation_and_voice_preferences(visual):
    assert store.profile().name==CHANNEL_NAME and tuple(store.profile().pillars)==PILLARS
    assert store.profile().rights_policy=='user_managed'
    raw=ChannelProfile(name='AI & Future Tech',pillars=list(LEGACY_PILLARS),enabled=False,auto_publish=False,tts_engine='edge').model_dump()
    with store.get_connection() as c:c.execute('UPDATE studio_config SET data=?',(json.dumps(raw),))
    store.init_studio();p=store.profile()
    assert p.name==CHANNEL_NAME and not p.enabled and not p.auto_publish and p.tts_engine=='edge'
    store.init_studio();assert store.profile()==p


def test_moment_identity_and_score_ignore_views_and_wrong_niches(visual):
    a=moment(1);b=moment(2,score=85)
    a['view_count']=5000;b['view_count']=5000000
    assert ContentDirector.rank([b,a])[0]['id']==a['id']
    other={**a,'moment':{**a['moment'],'start':15.,'end':25.}}
    assert store.put_opportunity(other)['id']!=a['id']
    assert not ContentDirector.rank([{**a,'one_second_interest':False}])
    assert not ContentDirector.rank([{**a,'category':'ai_tools'}])


def test_format_follows_footage_and_semantically_related_pool(visual):
    items=[moment(i) for i in range(10)]
    assert ContentDirector.choose_format(items[0],items)=='ranking'
    assert ContentDirector.choose_format(moment(10,score=96),items)=='viral_clip'
    assert ContentDirector.choose_format(moment(11,context=True),[])=='commentary'
    unrelated=[{**item,'verified_topic':'goalkeeper save'} for item in items[1:]]
    assert ContentDirector.choose_format(items[0],[items[0],*unrelated])=='viral_clip'
    assert ContentDirector.choose_format(items[0],[items[0]]*5)=='viral_clip'


def test_no_quota_fill_and_configured_owner_rights_policy(visual):
    item=moment(score=95)
    plan=ContentDirector.plan([item,moment(1,score=30)])
    assert len(plan['candidates'])==1 and plan['candidates'][0]['format']=='viral_clip'
    assets=ContentDirector.assets(item)
    assert assets[0]['license']=='unknown'
    assert RightsPolicyEngine.evaluate(assets,'user_managed')['passed']
    assert not RightsPolicyEngine.evaluate(assets,'user_managed')['rights_verified']
    assert not RightsPolicyEngine.evaluate(assets)['passed']
    strict=ChannelProfile(rights_policy='documented_permission')
    assert not ContentDirector.eligible(item,strict)


def test_visual_originality_requires_actual_commentary_not_just_captions():
    record={'hook':'Watch the landing','timeline':[{'narration':'Watch how he recovers after losing his footing.',
        'speech':{'words':[{'word':'Watch'}]}}],'qc':{'captions_checked':True},'final_review':{'passed':True}}
    assert OriginalityEngine.evaluate_visual([record])['passed']
    record['timeline'][0]['speech']=None
    assert not OriginalityEngine.evaluate_visual([record])['passed']


def test_visual_rating_rejects_unbounded_or_unclear_scores(visual):
    from app.studio.visual_discovery import VisualDiscovery
    class Provider:
        async def analyze_images(self,*args):
            return json.dumps({'real_world_action':True,'topic_matches':True,'one_second_interest':True,'context_needed':False,'confidence':.96,
                'reason':'The visible jump has a fast surprising outcome and clear landing.',
                'dimensions':{k:90 for k in ContentDirector.WEIGHTS}})
    m=moment()['moment']
    scored=asyncio.run(VisualDiscovery.score(Provider(),m,'explicit-fixture','parkour_freerunning','parkour fail'))
    assert scored['dimensions']['visual_payoff']==90
    class Invalid:
        async def analyze_images(self,*args):
            scored['dimensions']['clarity']=30;return json.dumps(scored)
    with pytest.raises(ValueError,match='interest'):
        asyncio.run(VisualDiscovery.score(Invalid(),m,'explicit-fixture','parkour_freerunning','parkour fail'))


@pytest.mark.parametrize('format',['viral_clip','commentary','ranking'])
def test_autonomous_formats_use_real_footage_engines_and_retain_gates(visual,monkeypatch,format):
    from app.studio.production import StudioProduction
    from app.pipelines.viral.viral_pipeline import ViralPipeline
    from app.pipelines.ranking.ranking_pipeline import RankingPipeline
    from app.core import database
    from app.media.ffmpeg_core import FFmpegCore
    items=[moment(i,score=95) for i in range(10 if format=='ranking' else 1)]
    store.enqueue(items[0]['id'],format)
    task=store.claim('visual-worker')
    async def fake_run(job,project,*args):
        settings=args[-2];assert settings['force_commentary'] is True
        assert set(settings['source_urls'])=={item['source_url'] for item in items}
        assert settings['source_urls'][0]==items[0]['source_url']
        records=[]
        for index in range(2 if format=='ranking' else 1):
            clip=f'fixture_{index}';path=visual['ranking']/(clip+'.mp4');path.write_bytes(b'explicit pipeline output fixture')
            database.create_or_update_clip(clip,project,job,'A verified parkour payoff',status='READY',video_path='/output/ranking/'+path.name,duration=9)
            records.append({'clip_id':clip,'hook':'Watch his foot','script':{'hook':'Watch his foot'},
                'timeline':[{'narration':'Watch his foot before the landing goes wrong.',
                    'speech':{'words':[{'word':'Watch'}]}}],
                'qc':{'passed':True,'captions_checked':True},'final_review':{'passed':True,'narration_grounded':True,'confidence':.95}})
        database.update_project(project,status='COMPLETED',result_data={'production_qc_passed':True,
            'variants' if format=='ranking' else 'moments':records})
    monkeypatch.setattr(ViralPipeline,'run',fake_run);monkeypatch.setattr(RankingPipeline,'run',fake_run)
    monkeypatch.setattr(FFmpegCore,'validate_output',lambda *args:{'width':1080})
    result=asyncio.run(StudioProduction.run(task))
    assert result['quality_gate']['passed'] and result['checks']['originality']['passed']
    assert result['checks']['rights']['rights_verified'] is False
    assert result['visual_structure']=='real verified footage' and result['metadata']['category_id']=='17'
    assert store.task(task['id'])['status']=='PUBLISH_READY'


def test_visual_discovery_rejects_gameplay_and_unverified_old_cards(visual):
    from app.studio.visual_discovery import VisualDiscovery
    class Gameplay:
        async def analyze_images(self,*args):
            return json.dumps({'real_world_action':False,'topic_matches':True,
                'one_second_interest':True,'context_needed':False,'confidence':.99,
                'reason':'A virtual goalkeeper saves the ball inside a soccer video game.',
                'dimensions':{k:95 for k in ContentDirector.WEIGHTS}})
    item=moment()
    with pytest.raises(ValueError,match='interest'):
        asyncio.run(VisualDiscovery.score(Gameplay(),item['moment'],'fixture','epic_saves','goalkeeper epic save'))
    old={**item,'opportunity_type':'visual_moment'}
    assert not ContentDirector.eligible(old) and not ContentDirector.rank([old])
    real={**old,'real_world_action':True,'topic_matches':True}
    assert ContentDirector.eligible(real) and ContentDirector.rank([real])


def test_production_rejects_weak_discovery_candidate_before_rendering(visual):
    from fastapi.testclient import TestClient
    from app.api.server import app
    item=moment(12,score=50)
    with TestClient(app,client=('127.0.0.1',50000)) as client:
        response=client.post('/api/studio/produce',json={'opportunity_id':item['id'],'format':'auto'})
    assert response.status_code==400 and 'quality threshold' in response.json()['detail']
    assert not store.tasks()


def test_standalone_script_enforces_originality_before_speech_synthesis():
    from app.ai.production_director import ProductionDirector
    class ShortLine:
        async def analyze_images(self,*args,**kwargs):
            return json.dumps({'complete_story':True,'confidence':.99,'title':'Unexpected parkour landing',
                'hook':'Watch his shoes','narration':'Watch this','observed_action':'A failed landing',
                'cuts':[{'start':0,'end':8}]})
    with pytest.raises(ValueError,match='five grounded words'):
        asyncio.run(ProductionDirector.plan_viral(ShortLine(),'fixture',{'start':0,'end':8},'fixture',[],force_commentary=True))

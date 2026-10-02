import asyncio
import json
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.core import database
from app.studio import store
from app.studio.models import ChannelProfile,now,LEGACY_PILLARS,CHANNEL_NAME
from app.studio.director import ContentDirector,PerformanceAnalyzer,RankingEngine
from app.studio.policy import RightsPolicyEngine,QualityGate,OriginalityEngine
from app.studio.research import primary_url,ResearchEngine,OpportunityDiscovery
from app.studio.writer import BudgetProvider,RetentionWriter
from app.studio.worker import StudioWorker


@pytest.fixture
def studio(isolated_app):
    store.init_studio()
    return isolated_app


def item(i=0,category='robotics'):
    return store.put_opportunity({'topic':f'Researched robot demonstration {i}','category':category,
        'source_url':f'https://news.mit.edu/robot-{i}','opportunity_type':'emerging',
        'rights_status':'research_only','dimensions':{'freshness':90,'audience_fit':90,'novelty':90,
        'visual_potential':90,'originality_potential':90,'explanation_potential':90,'ranking_potential':80,
        'evergreen_value':70,'competition':30}})


def research():
    return {'source_url':'https://news.mit.edu/robots','publisher':'news.mit.edu','evidence':[
        {'id':'e1','text':'The researchers created a robot that uses sensors to detect objects and a controller to adjust its movement.'}]}


def script():
    lines=[
        'How does a robot decide where to move? The researchers start with information from its sensors.',
        'Those sensors detect objects around the robot. The team describes a controller that uses this information to adjust movement.',
        'Think of the controller as the connection between sensing and action. This diagram explains that relationship, using the reported design.',
        'The useful idea here is the connection: detect the surroundings, process that information, and adjust the movement rather than moving blindly.'
    ]
    return {'title':'How a robot turns sensing into movement','summary':'An original explanation of the reported robot design.',
        'original_contributions':['Explains the sensor-controller relationship','Conceptual input-to-action diagrams'],
        'beats':[{'role':role,'headline':headline,'narration':line,'evidence':[{'id':'e1','quote':research()['evidence'][0]['text']}],
                  'diagram':{'nodes':['Sensors detect objects','Controller processes input','Movement adjusts']}}
                 for role,headline,line in zip(['hook','context','explanation','payoff'],['From sensing to action','Detect the surroundings','Connect sensing and control','Adjust the next move'],lines)]}


def test_default_profile_and_no_unattended_activation(studio):
    p=store.profile()
    assert p.name==CHANNEL_NAME and p.production_target==3 and p.publication_limit==2
    assert not p.enabled and not p.auto_publish and p.tts_engine=='pocket'
    with pytest.raises(ValueError): ChannelProfile(timezone='bad/timezone')


def test_atomic_claim_idempotent_plan_and_pause(studio):
    store.save_profile(ChannelProfile(name='Legacy test channel',pillars=list(LEGACY_PILLARS)))
    candidates=[item(1,'robotics'),item(2,'engineering'),item(3,'ai_tools'),item(4,'robotics')]
    plan=ContentDirector.plan(candidates)
    assert len(plan['candidates'])==3 and len({x['category'] for x in plan['candidates']})==3
    assert ContentDirector.plan(candidates)==plan and len(store.tasks())==3
    assert store.claim('paused-worker',allow_daily=False) is None
    a=store.claim('worker-a');b=store.claim('worker-b')
    assert a['id']!=b['id']
    store.update(a['id'],lease_until=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())
    store.claim('worker-c')
    assert store.task(a['id'])['attempts']==2


def test_concurrent_daily_plans_do_not_enqueue_extra_candidates(studio):
    store.save_profile(ChannelProfile(name='Legacy test channel',pillars=list(LEGACY_PILLARS)))
    from concurrent.futures import ThreadPoolExecutor
    sets=[[item(i+offset,category) for i,category in enumerate(['robotics','engineering','ai_tools'])] for offset in (10,20)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        plans=list(pool.map(ContentDirector.plan,sets))
    assert plans[0]==plans[1]
    assert len(store.tasks())==3


def test_durable_workspace_survives_startup_until_upload(studio):
    import os,time
    from app.storage.manager import StorageManager
    identifier=store.enqueue(item(70)['id'])['id']
    folder=StorageManager.get_job_temp_dir(identifier)
    (folder/'voice'/'evidence.txt').write_text('saved production evidence')
    old=time.time()-48*3600;os.utime(folder,(old,old))
    StorageManager.startup_cleanup(12)
    assert folder.exists()
    store.update(identifier,status='ANALYTICS_PENDING')
    StorageManager.startup_cleanup(12)
    assert not folder.exists()


def test_restart_retry_ceiling_is_terminal(studio):
    identifier=store.enqueue(item(71)['id'])['id']
    with database.get_connection() as c:
        c.execute("UPDATE studio_tasks SET status='PROCESSING',attempts=3,lease_until='2020-01-01' WHERE id=?",(identifier,))
    assert store.claim('worker-recovery') is None
    assert store.task(identifier)['status']=='FAILED'
    task=store.task(identifier)
    assert database.get_project(task['project_id'])['status']=='FAILED'
    assert database.get_job(identifier)['status']=='FAILED'


def test_quantitative_marketing_claim_needs_attribution(studio):
    value=script()
    value['beats'][0]['narration']='This new robot completes the sensing task five times faster and saves time for every developer.'
    value['beats'][1]['narration']='This robot processes input 5.5 times faster than an earlier version while continuing to track every object nearby.'
    with pytest.raises(ValueError,match='Attribute quantitative'): RetentionWriter.validate(value,research())


def test_pairwise_finalist_can_move_from_third_to_first():
    dimensions={key:80 for key in RankingEngine.WEIGHTS}
    candidates=[{'id':str(i),'dimensions':{**dimensions,'topic_relevance':90-i*10},'evidence':'Supported robotics demonstration'} for i in range(3)]
    class Provider:
        async def generate_text(self,prompt):
            choices=json.loads(prompt.split('\n')[-1]);winner=max(choices,key=lambda item:int(item['id']))
            return json.dumps({'winner_id':winner['id'],'reason':'The demonstrated mechanism provides a more surprising engineering payoff than the compared alternative.'})
    result=asyncio.run(RankingEngine.rerank(candidates,Provider()))
    assert [item['id'] for item in result]==['2','1','0']


def test_worker_does_not_dump_previous_daily_plans(studio,monkeypatch):
    p=ChannelProfile(enabled=True,auto_publish=True,tts_engine='edge');store.save_profile(p)
    candidate=item(77);task=store.enqueue(candidate['id'],day='2020-01-01',publish_at='2020-01-01')
    store.update(task['id'],status='PUBLISH_READY',clip_id='saved',checkpoint={'result':{'quality_gate':{'passed':True,'score':99},'metadata':{}}})
    database.update_project(task['project_id'],result_data={'quality_gate':{'passed':True}})
    monkeypatch.setattr('app.studio.worker.youtube.connection_status',lambda:{'connected':True})
    monkeypatch.setattr('app.studio.worker.youtube.get_upload',lambda _:None)
    upload=Mock();monkeypatch.setattr('app.studio.worker.youtube.queue_upload',upload)
    StudioWorker().publish_due();upload.assert_not_called()


def test_unknown_or_noncommercial_rights_block_publication(studio):
    base={'source_url':'https://example.org/video','source_type':'downloaded','creator':'Creator','license':'unknown',
          'rights_status':'unknown','fetched_at':now()}
    assert not RightsPolicyEngine.evaluate([base])['passed']
    assert not RightsPolicyEngine.evaluate([{**base,'license':'cc-by-nc','rights_status':'commercial_use_permitted','license_evidence_url':'https://example.org/license'}])['passed']
    assert RightsPolicyEngine.evaluate([{**base,'source_type':'cliprank_generated','creator':'ClipRank','license':'original'}])['passed']
    checks={key:{'passed':True} for key in QualityGate.REQUIRED};scores={key:85 for key in ('hook','clarity','pacing','original_contribution','audience_fit')}
    assert QualityGate.evaluate(checks,scores)['passed']
    checks['rights']['passed']=False
    assert not QualityGate.evaluate(checks,scores)['passed']


def test_research_primary_sources_no_local_or_credentials():
    assert primary_url('https://news.mit.edu/robot')
    assert not primary_url('http://news.mit.edu/robot')
    assert not primary_url('https://news.mit.edu.evil.example/robot')
    assert not primary_url('https://secret@news.mit.edu/robot')
    assert not primary_url('https://127.0.0.1/robot')


def test_script_rejects_invented_evidence_and_insufficient_originality(studio):
    valid=RetentionWriter.validate(script(),research())
    assert len(valid['beats'])==4
    valid['beats'][0]['evidence'][0]['quote']='An invented unsupported numerical specification.'
    with pytest.raises(ValueError,match='invalid quote'): RetentionWriter.validate(valid,research())
    assert not OriginalityEngine.evaluate({'beats':[]},[])['passed']


def test_cloud_budget_and_local_only(studio):
    provider=Mock()
    async def generate(*args,**kwargs): return 'actual response'
    provider.generate_text=generate
    budget=BudgetProvider(provider,'gemini',limit=1)
    assert asyncio.run(budget.generate_text('prompt'))=='actual response'
    with pytest.raises(ValueError,match='budget'): asyncio.run(budget.generate_text('again'))


def test_learning_does_not_overreact_or_invent_metrics(studio):
    with database.get_connection() as c:
        c.execute('INSERT INTO performance VALUES(?,?,?)',('one',json.dumps({'category':'robotics','averageViewPercentage':140}),now()))
    memory=PerformanceAnalyzer.intelligence()
    assert memory['performance_patterns']['robotics']['priority_adjustment']==0 and memory['revenue'] is None


def test_api_secrets_redacted_and_foreign_origins_rejected(studio):
    from app.api.server import app
    database.update_settings({'gemini_api_key':'private-fixture-key'})
    with TestClient(app,client=('127.0.0.1',50000)) as client:
        settings=client.get('/api/settings').json()
        assert settings['gemini_api_key_configured'] and 'private-fixture-key' not in json.dumps(settings)
        assert client.get('/api/studio/dashboard').status_code==200
        assert client.post('/api/studio/profile',json=ChannelProfile().model_dump(),headers={'Origin':'https://malicious.example'}).status_code==403
        assert client.post('/api/studio/profile',json={**ChannelProfile().model_dump(),'shell_command':'bad'}).status_code==422


def test_publication_limit_and_exceptional_third(studio,monkeypatch):
    profile=ChannelProfile(name='Legacy test channel',pillars=list(LEGACY_PILLARS),enabled=True,auto_publish=True,tts_engine='edge',publication_limit=3)
    store.save_profile(profile)
    candidates=[item(i,category) for i,category in enumerate(['robotics','engineering','ai_tools'])]
    ContentDirector.plan(candidates)
    for index,task in enumerate(store.tasks()):
        result={'quality_gate':{'passed':True,'score':[94,91,85][index]},'metadata':{'title':'A supported technology title'}}
        store.update(task['id'],status='PUBLISH_READY',clip_id=task['id']+'_clip',publish_at='2020-01-01T00:00:00+00:00',checkpoint={'result':result})
        database.update_project(task['project_id'],result_data=result)
    monkeypatch.setattr('app.studio.worker.youtube.connection_status',lambda:{'connected':True})
    uploads=Mock();monkeypatch.setattr('app.studio.worker.youtube.queue_upload',uploads)
    StudioWorker().publish_due()
    assert uploads.call_count==2
    StudioWorker().publish_due()
    assert uploads.call_count==2
    # Private copyright staging may upload both shortlisted candidates; public release has its own spacing gate.
    first=next(t for t in store.tasks() if t['status']=='UPLOADING')
    checkpoint=first['checkpoint'];checkpoint['last_upload_attempt']=(datetime.now(timezone.utc)-timedelta(hours=7)).isoformat()
    store.update(first['id'],status='COMPLETE',checkpoint=checkpoint)
    StudioWorker().publish_due()
    assert uploads.call_count==2
    StudioWorker().publish_due()
    assert uploads.call_count==2  # The third score is below the exceptional threshold.


def test_caption_long_technology_name_preserves_words_and_safe_layout(tmp_path,monkeypatch):
    from app.media.captions import CaptionRenderer
    from app.core import runtime
    monkeypatch.setattr(runtime,'run_process',lambda args:None)
    words=[{'word':w,'start':i*.5,'end':(i+1)*.5} for i,w in enumerate(['like','FoundationPose','allow'])]
    path=tmp_path/'technology.ass'
    CaptionRenderer.write_ass(path,[{'start':0,'end':1.5,'words':words}],0,2)
    CaptionRenderer.render_ass_track(path,2)
    assert 'FOUNDATIONPOSE' in path.read_text()
    assert list((tmp_path/'technology_frames').glob('caption_*.png'))


def test_real_original_graphics_mp4_and_strict_qc(studio,monkeypatch):
    from app.studio.production import StudioProduction
    from app.ai.router import AIRouter
    from app.tts.voice_engine import TTSEngine
    from app.core.runtime import run_process
    from app.media.ffmpeg_core import FFmpegCore
    store.save_profile(ChannelProfile(name='Legacy test channel',pillars=list(LEGACY_PILLARS),tts_engine='edge'))
    opportunity=item(55);task=store.enqueue(opportunity['id']);task=store.claim('test-worker')
    monkeypatch.setattr(ResearchEngine,'collect',lambda _:research())
    class Provider:
        async def generate_text(self,prompt):
            if prompt.startswith('Independently audit'):
                return json.dumps({'passed':True,'issues':[],'beats':[{'index':i,'supported':True,'marketing_attributed':True,'reason':'Explicit test primary evidence contains sensor inputs and controlled movement.'} for i in range(4)],
                    'scores':{key:90 for key in ('hook','clarity','pacing','original_contribution','audience_fit')}})
            return json.dumps(script())
        async def analyze_images(self,images,prompt):
            timeline=json.loads(prompt.split('\n')[-1])['timeline']
            return json.dumps({'passed':True,'confidence':.99,'reason':'Explicit fixture review observes original sensor-controller diagrams, mobile captions and the reported movement explanation.',
                'issues':[],'observations':[{'index':i,'time':beat['timeline_start']+.5,'description':'An original three-node sensor-controller diagram with actual timed captions.'} for i,beat in enumerate(timeline)]})
    async def active(*args,**kwargs): return Provider(),'local'
    monkeypatch.setattr(AIRouter,'require_ranking_provider',active)
    async def speech(text,path,voice):
        path.parent.mkdir(parents=True,exist_ok=True)
        words=[{'word':w,'start':i*.33,'end':(i+1)*.33} for i,w in enumerate(text.split())]
        duration=len(words)*.33+.12
        run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'sine=frequency=600:duration={duration}:sample_rate=48000',str(path)])
        return {'text':text,'words':words,'duration':duration,'segments':[{'text':text,'start':0,'end':words[-1]['end'],'words':words}]}
    monkeypatch.setattr(TTSEngine,'synthesize_timed',speech)
    result=asyncio.run(StudioProduction.run(task))
    project=database.get_project(task['project_id'])
    assert result['quality_gate']['passed'] and database.clip_passed_production_qc(project['clips'][0])
    assert store.task(task['id'])['status']=='PUBLISH_READY'
    output=studio['viral']/(task['id']+'_final.mp4')
    assert FFmpegCore.validate_output(output,result['moments'][0]['qc']['duration'])['width']==1080
    assert not (studio['temp']/task['id']).exists()
    assert store.task(task['id'])['checkpoint']['result']['script']

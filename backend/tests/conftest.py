import sys
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def test_secret_vault(monkeypatch):
    """Tests never read or write the user's OS credentials."""
    from app.core.secrets import SecretVault
    values={}
    monkeypatch.setattr(SecretVault,'ready',lambda:True)
    monkeypatch.setattr(SecretVault,'get',lambda name:values.get(name))
    monkeypatch.setattr(SecretVault,'set',lambda name,value:values.update({name:value}))


@pytest.fixture(autouse=True)
def test_no_background_worker(monkeypatch):
    """API unit tests explicitly drive worker state, never launch real media/cloud jobs."""
    from app.studio.worker import StudioWorker
    import asyncio
    async def idle(self): await asyncio.Event().wait()
    monkeypatch.setattr(StudioWorker,'run',idle)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    from app.core import database
    from app.core import config
    from app.storage import manager
    from app.pipelines.ranking import ranking_pipeline
    from app.pipelines.viral import viral_pipeline
    from app.api import routes
    outputs = tmp_path / "output"
    viral = outputs / "viral"
    ranking = outputs / "ranking"
    temp = tmp_path / "temp"
    projects = tmp_path / "projects"
    for folder in (viral, ranking, temp, projects):
        folder.mkdir(parents=True)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config,"STORAGE_DIR",tmp_path)
    monkeypatch.setattr(config,"OUTPUT_STORAGE_DIR",outputs)
    monkeypatch.setattr(manager, "TEMP_STORAGE_DIR", temp)
    monkeypatch.setattr(manager, "VIRAL_OUTPUT_DIR", viral)
    monkeypatch.setattr(manager, "RANKING_OUTPUT_DIR", ranking)
    monkeypatch.setattr(ranking_pipeline, "RANKING_OUTPUT_DIR", ranking)
    monkeypatch.setattr(viral_pipeline, "VIRAL_OUTPUT_DIR", viral)
    monkeypatch.setattr(routes, "PROJECTS_STORAGE_DIR", projects)
    monkeypatch.setattr(routes, "TEMP_STORAGE_DIR", temp)
    database.init_db()
    return {"root": tmp_path, "viral": viral, "ranking": ranking, "temp": temp, "projects": projects}


@pytest.fixture
def footage(tmp_path):
    """Explicit test-only moving video with audible source tone."""
    from app.core.runtime import run_process
    paths = []
    for i in range(3):
        path = tmp_path / f"source_{i}.mp4"
        run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                     f"testsrc2=size={640 + i * 16}x360:rate=15:duration=6", "-f", "lavfi", "-i",
                     f"sine=frequency={300 + i * 100}:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
        paths.append(path)
    return paths


@pytest.fixture
def ranking_vision(monkeypatch):
    """Explicit fake vision decisions for test-only pattern footage; no cloud calls."""
    import json
    from app.ai.router import AIRouter
    from app.tts.voice_engine import TTSEngine
    from app.transcription.transcriber import Transcriber
    from app.core.runtime import run_process
    async def fake_timed(text,path,voice=None):
        duration=max(.8,len(text.split())*.15)
        path.parent.mkdir(parents=True,exist_ok=True)
        run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'sine=frequency=900:duration={duration}:sample_rate=48000',str(path)])
        words=[{'word':w,'start':i*.15,'end':(i+1)*.15} for i,w in enumerate(text.split())]
        return {'duration':duration,'text':text,'words':words,
                'segments':[{'start':0,'end':words[-1]['end'],'text':text,'words':words}]}
    monkeypatch.setattr(TTSEngine,'synthesize_timed',fake_timed)
    monkeypatch.setattr(Transcriber,'transcribe',lambda _: {'available':True,'segments':[],'text':''})
    class Provider:
        async def generate_text(self,prompt,**kwargs):
            evidence=json.loads(prompt.split('\n')[-1])
            plans=[]
            from app.ai.production_director import ProductionDirector
            subject=next(iter(ProductionDirector.topical_roots(evidence['topic'])),'Patterns')
            for variant,cuts in evidence['verified_cuts'].items():
                hook=f'{subject} gets stranger.' if variant=='A' else f'{subject}. Watch that next change.'
                plans.append({'name':variant,'hook':hook,'entries':[{'source_id':m['source_id'],'rank':m['assigned_rank'],
                    'narration':hook if i==0 else ('Watch those colors.' if variant=='A' else 'Look at the shapes.'),
                    'reason':'Explicit fixture narration'} for i,m in enumerate(cuts)]})
            return json.dumps({'variants':plans})
        async def analyze_images(self, images, prompt, **kwargs):
            if prompt.startswith('COMPARATIVE RANKING'):
                evidence=json.loads(prompt.split('\n')[1])
                return json.dumps({'rankings':[{'id':m['id'],'score':100-m['id'],
                    'reason':'Explicit comparative fixture: the earlier indexed moving pattern has the strongest test payoff.'} for m in evidence['candidates']]})
            if prompt.startswith('STANDALONE STORY'):
                evidence=json.loads(prompt.split('\n')[-1])
                return json.dumps({'complete_story':True,'confidence':.99,'title':'Moving colored shapes',
                    'hook':'Watch the colors change','narration':'Colored shapes move across the screen',
                    'observed_action':'Colored shapes move','cuts':[evidence['window']]})
            if prompt.startswith('FINAL RENDER'):
                return json.dumps({key:True for key in ('topic_matches','hook_honest','payoffs_complete','rank_order_correct',
                    'escalation_valid','narration_grounded','captions_readable','framing_safe','production_finished','no_graphic_injury','commentary_preserves_payoff')}|
                    {'confidence':.99,'reason':'Explicit fixture review: colored shapes move continuously with timed captions and changing rank graphics.',
                     'observations':[{'rank':m.get('rank'),'time':m['timeline_start']+.2,'visible_event':'Colored shapes visibly move across the frame'}
                      for m in json.loads(prompt.split('\n')[1])['timeline']], 'issues':[]})
            if '\n' not in prompt:
                return json.dumps({'suitable_raw':True,'already_ranked':False,'compilation':False,'reason':'Individual test footage'})
            evidence=json.loads(prompt.split('\n')[-1])
            if 'candidates' in evidence:
                return json.dumps({'moments':[{'id':m['id'],'matches_topic':True,'complete_action':True,
                    'already_ranked':False,'graphic_injury':False,'topic_relevance':.96,'confidence':.96,'score':80,
                    'label':'Moving color pattern','commentary':'Colored shapes move through the frame.',
                    'observed_action':'Color patterns move','topic_evidence':'The moving pattern is visibly present',
                    'event_start':m['start']+.2,'payoff_time':m['start']+1,'event_end':m['end']-.2,
                    'reason':'Complete visible pattern motion'} for m in evidence['candidates']]})
            return json.dumps({'matches_topic':True,'complete_action':True,'label_matches':True,
                'commentary_matches':True,'confidence':.96,'topic_evidence':'Moving colored pattern remains visible',
                'reason':'Cut and labels agree'})
    provider=Provider()
    async def connected(*args, **kwargs): return provider,'fixture'
    monkeypatch.setattr(AIRouter,'get_active_provider',connected)
    return provider

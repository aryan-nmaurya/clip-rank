import asyncio
import json
from unittest.mock import Mock

import pytest

from app.ai.production_director import ProductionDirector
from app.ai.gemini import GeminiProvider
from app.core import database
from app.media.captions import CaptionRenderer
from app.media.production_qc import ProductionQC
from app.storage.manager import StorageManager


def pool():
    return [dict(source_id=str(i),score=100-i,start=0,end=8,event_start=2,payoff_time=4,event_end=5,
                 label='Rail jump',observed_action='Foot misses rail',topic_evidence='Jump and failed landing') for i in range(12)]


def test_variants_keep_payoffs_and_use_alternative_sources():
    a,b=(ProductionDirector.cuts(pool(),5,k) for k in ('A','B'))
    assert [m['assigned_rank'] for m in a] == [5,4,3,2,1]
    assert a[-1]['source_id']=='0' and b[-1]['source_id']=='1'
    assert {m['source_id'] for m in a}.isdisjoint(m['source_id'] for m in b)
    assert all(m['end']==m['verified_end'] for m in a+b)
    assert sum(m['end']-m['start'] for m in a) < sum(m['end']-m['start'] for m in b)
    assert all(m['start']<=m['event_start']<m['payoff_time']<=m['event_end']<=m['end'] for m in a+b)


def test_duplicate_scripts_cannot_be_published():
    class Provider:
        async def generate_text(self,prompt,**kwargs):
            evidence=json.loads(prompt.split('\n')[-1])
            return json.dumps({'variants':[{'name':k,'hook':'Parkour fails get worse','entries':[
                {'source_id':m['source_id'],'rank':m['assigned_rank'],'narration':'Parkour fails get worse' if i==0 else 'Watch the rail'}
                for i,m in enumerate(cuts)]} for k,cuts in evidence['verified_cuts'].items()]})
    with pytest.raises(ValueError,match='Duplicate hooks'):
        asyncio.run(ProductionDirector.plan(Provider(),'test',pool(),5,'Parkour fails'))


def test_missing_or_inaccurate_word_times_reject():
    with pytest.raises(ValueError,match='exactly match'):
        ProductionQC.validate_words([],3,'Watch the landing')
    with pytest.raises(ValueError,match='exactly match'):
        ProductionQC.validate_words([{'start':0,'end':1,'word':'Run'}],3,'Jump')
    with pytest.raises(ValueError,match='finite'):
        ProductionQC.validate_words([{'start':0,'end':float('nan'),'word':'Jump'}],3)
    with pytest.raises(ValueError,match='overlapping'):
        ProductionQC.validate_words([{'start':0,'end':1,'word':'Watch'},{'start':.5,'end':1.2,'word':'this'}],3)


def test_caption_estimates_are_not_used(tmp_path):
    with pytest.raises(ValueError,match='actual word timestamps'):
        CaptionRenderer.write_ass(tmp_path/'speech.ass',[{'start':0,'end':2,'text':'Watch this'}],0,3)


def test_no_dialogue_story_cannot_repeat_narration(tmp_path):
    class Provider:
        async def analyze_images(self,*args,**kwargs):
            return json.dumps({'complete_story':True,'confidence':.98,'title':'Rail jump fail','hook':'Watch his foot','narration':'Watch his foot miss the rail',
                'observed_action':'Jump misses rail','cuts':[{'start':0,'end':3},{'start':4,'end':7}]})
    with pytest.raises(ValueError,match='continuous cut'):
        asyncio.run(ProductionDirector.plan_viral(Provider(),'test',{'start':0,'end':8},tmp_path/'sheet.jpg',[]))


def test_cleanup_cannot_follow_paths_or_links_outside_job_root(isolated_app,tmp_path):
    sentinel=tmp_path/'outside'/'keep.txt'
    sentinel.parent.mkdir(); sentinel.write_text('keep')
    (isolated_app['temp']/'escaped').symlink_to(sentinel.parent,target_is_directory=True)
    assert StorageManager.cleanup_job_temp('escaped') is False
    assert StorageManager.cleanup_job_temp('..') is False
    with pytest.raises(ValueError): StorageManager.get_job_temp_dir('escaped')
    with pytest.raises(ValueError): StorageManager.get_job_temp_dir('../outside')
    assert sentinel.read_text() == 'keep'


def test_ready_is_insufficient_without_both_qc_checks(isolated_app):
    database.create_project('p','ranking','Parkour fails')
    database.create_job('j','p')
    database.create_or_update_clip('c','p','j','Parkour fails',status='READY')
    clip=database.get_clip('c')
    assert not database.clip_passed_production_qc(clip)
    database.update_project('p',result_data={'production_qc_passed':True,'variants':[{'clip_id':'c','qc':{'passed':True},'final_review':{'passed':False}}]})
    assert not database.clip_passed_production_qc(clip)
    database.update_project('p',result_data={'production_qc_passed':True,'variants':[{'clip_id':'c','qc':{'passed':True},'final_review':{'passed':True}}]})
    assert database.clip_passed_production_qc(clip)


def test_gemini_transport_closes_sync_client_and_redacts_errors(monkeypatch):
    provider=GeminiProvider('example-secret-key')
    client=Mock(); context=Mock()
    context.__enter__=Mock(return_value=client); context.__exit__=Mock(return_value=False)
    client.models.generate_content.return_value.text='{"passed":true}'
    monkeypatch.setattr(provider,'_get_client',lambda key=None:context)
    assert asyncio.run(provider.generate_text('Review',options={'json':True})) == '{"passed":true}'
    context.__exit__.assert_called_once()
    client.models.generate_content.side_effect=RuntimeError('failed with example-secret-key')
    assert asyncio.run(provider.generate_text('Review')) is None
    assert 'example-secret-key' not in provider.last_error


@pytest.mark.parametrize('color,error',[('black','black section'),('red','frozen section')])
def test_actual_black_and_frozen_exports_reject(tmp_path,color,error):
    from app.core.runtime import run_process
    path=tmp_path/'bad.mp4'
    run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color=c={color}:s=1080x1920:r=30:d=2',
        '-f','lavfi','-i','sine=frequency=440:duration=2:sample_rate=48000','-c:v','libx264','-preset','ultrafast',
        '-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-shortest',str(path)])
    with pytest.raises(ValueError,match=error):
        ProductionQC.inspect(path,2,tmp_path/'qc',[],False)


def test_stereo_audio_is_measured_without_downmix_gain(tmp_path):
    from app.core.runtime import run_process
    path=tmp_path/'stereo.mp4'
    run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=1080x1920:r=30:d=2',
        '-f','lavfi','-i','sine=frequency=440:duration=2:sample_rate=48000',
        '-af','volume=6,pan=stereo|c0=c0|c1=c0','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',
        '-c:a','aac','-ar','48000','-shortest',str(path)])
    qc=ProductionQC.inspect(path,2,tmp_path/'qc',[],False)
    assert qc['passed'] and .7<qc['peak_amplitude']<.98


def test_failed_generation_keeps_workspace_until_repair(isolated_app,monkeypatch):
    from app.ai.router import AIRouter
    from app.pipelines.viral.viral_pipeline import ViralPipeline
    async def unavailable(*args,**kwargs): return None,'none'
    monkeypatch.setattr(AIRouter,'get_active_provider',unavailable)
    database.create_project('p','viral','Test failure');database.create_job('j','p')
    folder=StorageManager.get_job_temp_dir('j');(folder/'keep-for-repair.txt').write_text('source evidence')
    with pytest.raises(ValueError,match='connected vision'):
        asyncio.run(ViralPipeline.run('j','p','https://example.org/video.mp4',1,{},lambda *args:None))
    assert (folder/'keep-for-repair.txt').is_file()


def test_final_review_requires_specific_time_stamped_footage_evidence(tmp_path):
    flags={k:True for k in ('topic_matches','hook_honest','payoffs_complete','rank_order_correct','escalation_valid',
                           'narration_grounded','captions_readable','framing_safe','production_finished','no_graphic_injury','commentary_preserves_payoff')}
    class Provider:
        response=flags|{'confidence':.99,'reason':'Observed evidence','issues':[]}
        async def analyze_images(self,*args,**kwargs): return json.dumps(self.response)
    provider=Provider();timeline=[{'rank':1,'timeline_start':0,'duration':5}]
    review=lambda:asyncio.run(ProductionDirector.final_review(provider,'fixture',tmp_path/'final.mp4',[],'Parkour fails',timeline))
    assert not review()['passed']
    provider.response=flags|{'confidence':.99,'reason':'The athlete jumps toward the rail, misses with the left foot and drops to the grass.',
        'observations':[{'rank':1,'time':2,'visible_event':'The left foot misses the rail and the athlete drops onto grass.'}],'issues':[]}
    assert review()['passed']
    provider.response['observations'][0]['time']=20
    assert not review()['passed']


def test_final_frame_uses_video_end_when_aac_has_a_longer_tail(tmp_path):
    from app.core.runtime import run_process
    from app.media.ffmpeg_core import FFmpegCore
    path=tmp_path/'audio-tail.mp4'
    run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=red:s=640x360:r=30:d=1.5',
        '-f','lavfi','-i','sine=duration=2:sample_rate=48000','-c:v','libx264','-pix_fmt','yuv420p',
        '-color_range','tv','-c:a','aac','-ar','48000',str(path)])
    info=FFmpegCore.get_video_info(path)
    assert info['duration']>info['video_duration']+.4
    frame=FFmpegCore.extract_frame(path,tmp_path/'last.jpg',info['duration']-.05)
    from PIL import Image
    with Image.open(frame) as image:
        r,g,b=image.getpixel((320,180))
        assert r>200 and g<30 and b<30


def test_gemini_temporary_failure_retries_and_closes_each_client(monkeypatch):
    class Busy(RuntimeError): code=503
    provider=GeminiProvider('example-secret-key')
    client=Mock();context=Mock()
    context.__enter__=Mock(return_value=client);context.__exit__=Mock(return_value=False)
    client.models.generate_content.side_effect=[Busy('busy'),Mock(text='Recovered')]
    monkeypatch.setattr(provider,'_get_client',lambda key=None:context)
    delays=[]
    async def immediate(delay):delays.append(delay)
    monkeypatch.setattr('app.ai.gemini.asyncio.sleep',immediate)
    assert asyncio.run(provider.generate_text('Review'))=='Recovered'
    assert client.models.generate_content.call_count==2 and context.__exit__.call_count==2
    assert delays==[1]


def test_comparative_ranking_rejects_ties_and_preserves_source_cuts(tmp_path):
    class Provider:
        scores=[80,80]
        async def analyze_images(self,*args,**kwargs):
            return json.dumps({'rankings':[{'id':i,'score':score,'reason':'The visible rail collision has a clearer, more surprising payoff than the other clip.'} for i,score in enumerate(self.scores)]})
    provider=Provider();candidates=pool()[:2]
    with pytest.raises(ValueError,match='distinct'):
        asyncio.run(ProductionDirector.rank_pool(provider,'fixture',candidates,[],'Parkour fails'))
    provider.scores=[85,95]
    ranked=asyncio.run(ProductionDirector.rank_pool(provider,'fixture',candidates,[],'Parkour fails'))
    assert [m['score'] for m in ranked]==[85,95]
    assert [(m['source_id'],m['start'],m['end']) for m in ranked]==[(m['source_id'],m['start'],m['end']) for m in candidates]
    assert all(m['ranking_reason'] for m in ranked)


def test_hook_explicitly_names_the_topic():
    assert ProductionDirector.topical_roots('Funny cat moments')=={'cat'}
    assert ProductionDirector.topical_roots('Parkour fails')=={'parkour'}
    class Provider:
        async def generate_text(self,prompt,**kwargs):
            evidence=json.loads(prompt.split('\n')[-1])
            return json.dumps({'variants':[{'name':k,'hook':'Witness these gravity blunders','entries':[
                {'source_id':m['source_id'],'rank':m['assigned_rank'],'narration':'Witness these gravity blunders' if i==0 else 'Watch the rail'}
                for i,m in enumerate(cuts)]} for k,cuts in evidence['verified_cuts'].items()]})
    with pytest.raises(ValueError,match='explicitly name'):
        asyncio.run(ProductionDirector.plan(Provider(),'fixture',pool(),5,'Parkour fails'))


def test_focused_rewrite_rejects_spoilers_then_accepts_a_grounded_cue():
    class Provider:
        lines=iter(['Roof jump fall','Now look between those rooftops'])
        async def generate_text(self,*args,**kwargs):return json.dumps({'line':next(self.lines)})
    moment={'observed_action':'An athlete attempts a jump between rooftops and lands on a lower ledge.',
            'topic_evidence':'Both buildings, the gap and the missed landing are visible.'}
    line=asyncio.run(ProductionDirector.rewrite_tease(Provider(),moment,'A',6,'Outcome was spoken too early'))
    assert line=='Now look between those rooftops'
    with pytest.raises(ValueError):ProductionDirector.validate_line('Your short spoken tease',6)


def test_repair_only_the_hook_that_omits_the_subject():
    class Provider:
        repairs=0
        async def generate_text(self,prompt,**kwargs):
            if prompt.startswith('Repair ONE opening hook'):
                self.repairs+=1
                return json.dumps({'hook':'Parkour gets unpredictable'})
            evidence=json.loads(prompt.split('\n')[-1])
            return json.dumps({'variants':[{'name':key,'hook':'Parkour gets wild' if key=='A' else 'These jumps get wild',
                'entries':[{'source_id':m['source_id'],'rank':m['assigned_rank'],
                    'narration':'Look at those shoes' if key=='A' else 'Keep watching that rail'} for m in cuts]}
                for key,cuts in evidence['verified_cuts'].items()]})
    provider=Provider()
    plans=asyncio.run(ProductionDirector.plan(provider,'fixture',pool(),5,'Parkour fails'))
    assert provider.repairs==1
    assert plans['A']['hook']=='Parkour gets wild'
    assert plans['B']['hook']=='Parkour gets unpredictable'
    assert plans['B']['moments'][0]['narration_text']==plans['B']['hook']

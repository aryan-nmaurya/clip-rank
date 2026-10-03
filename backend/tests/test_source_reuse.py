import asyncio
import json
import pytest
from app.core import database
from app.sources.reuse import SourceReuse,source_keys,ranges,file_fingerprint
from app.ai.production_director import ProductionDirector
from app.media.production_qc import ProductionQC


def job(project,identifier):
    database.create_project(project,'viral','Fixture')
    database.create_job(identifier,project)


def test_youtube_aliases_match_and_sha_detects_reuploaded_file(tmp_path):
    links=['https://youtube.com/watch?v=abc123','https://youtu.be/abc123?t=2','https://www.youtube.com/shorts/abc123']
    assert all('youtube:abc123' in source_keys({'url':url}) for url in links)
    path=tmp_path/'fixture';path.write_bytes(b'unique source')
    first=source_keys({'source_sha256':file_fingerprint(path),'url':links[0]})
    assert first.intersection(source_keys({'source_sha256':file_fingerprint(path),'url':'https://example.org/reupload'}))


def test_distinct_windows_allowed_but_repeat_and_same_project_retry_blocked(isolated_app):
    job('p1','j1');job('p2','j2')
    source={'source_id':'original'};keys=sorted(source_keys(source))
    SourceReuse.claim('p1','j1',[{'keys':keys,'start':0,'end':12}])
    assert SourceReuse.overlaps(source,1,11,'p1')
    assert not SourceReuse.overlaps(source,12,24,'p2')
    SourceReuse.claim('p2','j2',[{'keys':keys,'start':12,'end':24}])
    database.create_job('j3','p1')
    with pytest.raises(ValueError,match='already appears'):
        SourceReuse.claim('p1','j3',[{'keys':keys,'start':0,'end':12}])


def test_ranking_reserves_entire_source_and_atomic_pair_rejects_duplicates(isolated_app):
    job('p1','j1');job('p2','j2');keys=['youtube:original']
    SourceReuse.claim('p1','j1',[{'keys':keys,'start':0,'end':4,'whole_source':True}])
    assert SourceReuse.overlaps({'url':'https://youtu.be/original'},20,40)
    with pytest.raises(ValueError,match='already appears'):
        SourceReuse.claim('p2','j2',[{'keys':['youtube:new'],'start':0,'end':12,'whole_source':True},
            {'keys':['youtube:new'],'start':15,'end':27,'whole_source':True}])
    assert 'youtube:new' not in SourceReuse.used_keys()
    SourceReuse.release('j1')
    assert not SourceReuse.used_keys()


def test_approved_legacy_results_and_deleted_project_keep_history(isolated_app):
    job('p','j')
    result={'production_qc_passed':True,'variants':[{'moments':[{'source_id':'x','url':'https://youtu.be/x','start':0,'end':5}]}]}
    database.update_project('p',result_data=result)
    assert 'youtube:x' in SourceReuse.used_keys('p')
    # Movie formats are independent scenes and retain their existing editorial policy.
    assert ranges({'moments':[{'format':'DIALOGUE','source_id':'x','start':0,'end':12}]})==[]
    job('other','otherjob')
    SourceReuse.claim('other','otherjob',[{'keys':['youtube:retained'],'start':0,'end':12}])
    with database.get_connection() as connection:
        connection.execute('DELETE FROM jobs WHERE id=?',('otherjob',))
    assert 'youtube:retained' in SourceReuse.used_keys()


@pytest.mark.parametrize('duration',[0,6,10,10.09,float('nan')])
def test_short_exports_are_rejected(duration):
    with pytest.raises(ValueError,match='longer than 10'):ProductionQC.require_duration(duration)
    assert ProductionQC.require_duration(10.1)


def test_speech_failure_forces_a_new_hook_without_changing_approved_variant():
    pool=[dict(source_id=str(i),score=100-i,start=0,end=8,event_start=1,payoff_time=4,event_end=6,
        label='Wall jump',observed_action='Runner attempts a jump beside a wall',topic_evidence='The jump and landing are visible') for i in range(6)]
    class Provider:
        async def generate_text(self,prompt,**kw):
            if 'Repair ONE opening hook' in prompt:return json.dumps({'hook':'Brutal parkour attempts'})
            evidence=json.loads(prompt.split('\n')[-1])
            return json.dumps({'variants':[{'name':variant,'hook':'Parkour jumps gone wrong' if variant=='A' else 'Unexpected parkour moments',
                'entries':[{'source_id':m['source_id'],'rank':m['assigned_rank'],'narration':'Watch that wall closely'} for m in cuts]}
                for variant,cuts in evidence['verified_cuts'].items()]})
    provider=Provider()
    first=asyncio.run(ProductionDirector.plan(provider,'fixture',pool,3,'Parkour fails'))
    repaired=asyncio.run(ProductionDirector.plan(provider,'fixture',pool,3,'Parkour fails',fixed_plans={'B':first['B']},
        failed_lines=[{'variant':'A','source_id':first['A']['moments'][0]['source_id'],'text':first['A']['hook'],'reason':'Speech was recognized as Parker'}]))
    assert repaired['A']['hook']=='Brutal parkour attempts'
    assert repaired['B'] is first['B']


def test_short_speech_does_not_trim_away_visual_context(monkeypatch,tmp_path):
    import numpy as np
    import app.media.moments as module
    monkeypatch.setattr(module.FFmpegCore,'get_video_info',lambda _: {'duration':12.})
    frames=np.random.default_rng(2).integers(0,255,(24,90,160),dtype=np.uint8)
    monkeypatch.setattr(module,'run_process',lambda *a,**kw:frames.tobytes())
    candidates=module.MomentAnalyzer.analyze(tmp_path/'source.mp4',count=1,target_duration=12,
        minimum_duration=10.1,segments=[{'start':2.,'end':9.}])
    assert len(candidates)==1 and candidates[0]['start']==0 and candidates[0]['end']==12


def test_coverage_windows_keep_full_attempt_in_short_sources(monkeypatch,tmp_path):
    import numpy as np
    import app.media.moments as module
    monkeypatch.setattr(module.FFmpegCore,'get_video_info',lambda _: {'duration':6.})
    frames=np.random.default_rng(2).integers(0,255,(12,90,160),dtype=np.uint8)
    monkeypatch.setattr(module,'run_process',lambda *a,**kw:frames.tobytes())
    candidates=module.MomentAnalyzer.analyze(tmp_path/'source.mp4',count=8,target_duration=12,
        coverage=True,minimum_duration=1.)
    assert len(candidates)==1 and candidates[0]['start']==0 and candidates[0]['end']==6

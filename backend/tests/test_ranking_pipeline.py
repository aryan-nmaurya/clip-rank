import asyncio
import numpy as np
from PIL import Image
from app.core.database import create_project, create_job, get_project
from app.pipelines.ranking.ranking_pipeline import RankingPipeline
from app.pipelines.ranking.scorer import RankingScorer
from app.media.ffmpeg_core import FFmpegCore


def test_best_moment_is_number_one():
    result = RankingScorer.score_and_order([{"score": 95, "id": "best"}, {"score": 40, "id": "last"}, {"score": 70, "id": "middle"}], 3)
    assert [m["assigned_rank"] for m in result] == [3, 2, 1]
    assert result[-1]["id"] == "best"


def test_ranking(isolated_app, ranking_footage, ranking_vision, rendered_watermarks):
    create_project("ranking_test", "ranking", "Test countdown", {"count": 3})
    create_job("ranking_job", "ranking_test")
    asyncio.run(RankingPipeline.run("ranking_job", "ranking_test", "Test moments", 3,
                {"ai_provider": "auto", "source_files": [str(p) for p in ranking_footage], "segment_duration": 3,
                 "watermark_enabled":True,"watermark_text":"@RankBrand"}, lambda *a: None))
    project = get_project("ranking_test")
    assert project["status"] == "COMPLETED"
    assert len(project["clips"]) == 2
    assert rendered_watermarks == ['@RankBrand','@RankBrand']
    assert project['result_data']['production_qc_passed'] is True
    assert project['result_data']['variants'][0]['hook'] != project['result_data']['variants'][1]['hook']
    a,b=project['result_data']['variants']
    assert {m['source_id'] for m in a['moments']}.isdisjoint(m['source_id'] for m in b['moments'])
    assert all(c['duration']>10 for c in project['clips'])
    assert [m["assigned_rank"] for m in project["result_data"]["moments"]] == [3, 2, 1]
    assert project["result_data"]["topic_verified"] is True
    assert all(m['label']=='Moving color pattern' and m['final_review']['passed'] for m in project['result_data']['moments'])
    clip = project["clips"][0]
    path = isolated_app["ranking"] / (clip["id"] + ".mp4")
    info = FFmpegCore.validate_output(path, clip['duration'])
    assert (info['width'],info['height'],info['sample_rate']) == (1080,1920,48000)
    assert info["has_audio"]
    preview = isolated_app["ranking"] / (clip["id"] + "_preview.jpg")
    with Image.open(preview) as frame:
        pixels = np.array(frame)
        assert pixels[400:900, 200:600].std() > 30  # visible footage, not black canvas
    assert not (isolated_app["temp"] / "ranking_job").exists()


def test_ranking_retains_approved_variant_when_other_needs_repair(isolated_app,ranking_footage,ranking_vision,monkeypatch):
    from app.ai.production_director import ProductionDirector
    original=ProductionDirector.final_review
    reviews=[]
    async def review(*args,**kwargs):
        path=args[2]
        variant=path.parent.parent.name
        reviews.append(variant)
        if variant=='B' and reviews.count('B')==1:
            return {'passed':False,'reason':'Repair only this variant'}
        return await original(*args,**kwargs)
    monkeypatch.setattr(ProductionDirector,'final_review',review)
    create_project('pair_repair','ranking','Test countdown',{'count':3})
    create_job('pair_repair_job','pair_repair')
    asyncio.run(RankingPipeline.run('pair_repair_job','pair_repair','Test moments',3,
        {'ai_provider':'auto','source_files':[str(p) for p in ranking_footage],'segment_duration':3},lambda *args:None))
    project=get_project('pair_repair')
    assert project['status']=='COMPLETED' and len(project['clips'])==2
    assert reviews==['A','B','B']
    assert project['result_data']['production_attempts']==2
    assert project['result_data']['production_qc_passed']


def test_unreadable_candidate_does_not_abort_other_verified_sources(isolated_app,ranking_footage,ranking_vision,monkeypatch):
    from app.sources.discovery import SourceDiscovery
    def sources(*args,**kwargs):
        return [{'id':'broken','source_id':'broken','title':'Unavailable candidate','file_path':str(isolated_app['temp']/'absent.mp4'),'duration':6},
            *[{'id':str(i),'source_id':str(i),'title':'Raw pattern','file_path':str(p),'duration':6,'url':None} for i,p in enumerate(ranking_footage)]]
    monkeypatch.setattr(SourceDiscovery,'discover_candidate_videos',sources)
    create_project('source_recovery','ranking','Recover candidates',{'count':3})
    create_job('source_recovery_job','source_recovery')
    asyncio.run(RankingPipeline.run('source_recovery_job','source_recovery','Test moments',3,
        {'ai_provider':'auto','segment_duration':3},lambda *args:None))
    project=get_project('source_recovery')
    assert project['status']=='COMPLETED' and len(project['clips'])==2
    assert project['result_data']['rejected_sources'][0]['title']=='Unavailable candidate'
    assert project['result_data']['production_qc_passed'] is True

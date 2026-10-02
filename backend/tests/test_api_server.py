from fastapi.testclient import TestClient
from app.api.server import app
from app.api.routes import job_engine
from app.core.database import get_project


def test_api(isolated_app, monkeypatch, footage, ranking_vision):
    submitted = []
    monkeypatch.setattr(job_engine, "submit_job", submitted.append)
    with TestClient(app,client=('127.0.0.1',50000)) as client:
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/diagnostics").status_code == 200
        assert client.post("/api/projects/viral", data={"count": 3}).status_code == 400
        assert client.post("/api/projects/viral", data={"video_url": "not a url"}).status_code == 400
        assert client.post("/api/projects/viral", data={"video_url": "https://example.org/video.mp4", "count": 0}).status_code == 400
        response = client.post("/api/projects/viral", data={"video_url": "https://example.org/video.mp4", "count": 1})
        assert response.status_code == 200
        project = get_project(response.json()["project_id"])
        assert project["input_data"]["video_source"] == "https://example.org/video.mp4"
        assert len(submitted) == 1
        assert client.post("/api/projects/ranking", json={"topic": "cats", "count": 2}).status_code == 422
        assert client.post("/api/projects/ranking", json={"topic": "cats", "source_urls": ["https://example.org/1.mp4"]}).status_code == 400
        response = client.post('/api/projects/ranking', json={
            'topic':'Cats','count':3,'source_platforms':['youtube','dailymotion'],
            'voice':'en-GB-RyanNeural'})
        assert response.status_code == 200
        ranking = get_project(response.json()['project_id'])
        assert ranking['input_data']['default_voice'] == 'en-GB-RyanNeural'
        assert ranking['input_data']['source_platforms'] == ['youtube','dailymotion']
        assert ranking['input_data']['narration'] is True
        assert client.post('/api/projects/ranking', json={'topic':'Cats','voice':'bad-voice'}).status_code == 400
        assert client.post('/api/projects/ranking', json={'topic':'Cats','source_platforms':[]}).status_code == 422
        assert client.get('/api/speech/preview/bad-voice').status_code == 400
        with footage[0].open('rb') as source:
            response = client.post("/api/projects/viral", data={"count": 1}, files={"video_file": ("../../source.mp4", source, "video/mp4")})
        assert response.status_code == 200
        project = get_project(response.json()["project_id"])
        from pathlib import Path
        path = Path(project["input_data"]["video_source"])
        assert path.is_relative_to(isolated_app["projects"])
        assert path.is_file()
        assert client.get("/").status_code == 200


def test_media_and_download_require_final_qc(isolated_app):
    from app.core import database
    database.create_project('p','ranking','Parkour fails');database.create_job('j','p')
    database.create_or_update_clip('c','p','j','Parkour fails',status='READY',video_path='/output/ranking/c.mp4',preview_path='/output/ranking/c_preview.jpg')
    (isolated_app['ranking']/'c.mp4').write_bytes(b'isolated-api-media-fixture')
    (isolated_app['ranking']/'c_preview.jpg').write_bytes(b'isolated-preview-fixture')
    with TestClient(app) as client:
        assert client.get('/output/ranking/c.mp4').status_code==404
        assert client.get('/output/ranking/c_preview.jpg').status_code==404
        assert client.get('/api/clips/c/download').status_code==409
        assert client.get('/api/projects/p').json()['clips']==[]
        database.update_project('p',result_data={'production_qc_passed':True,'variants':[{'clip_id':'c','qc':{'passed':True},'final_review':{'passed':True}}]})
        assert client.get('/output/ranking/c.mp4').content==b'isolated-api-media-fixture'
        assert client.get('/output/ranking/c_preview.jpg').status_code==200
        assert client.get('/api/clips/c/download').status_code==200
        assert len(client.get('/api/projects/p').json()['clips'])==1
        assert client.get('/output/ranking/debug.mp4').status_code==404

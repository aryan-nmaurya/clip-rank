import json
import sqlite3

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.api.server import app
from app.core import database
from app.core.runtime import run_process
from app.media.production_qc import ProductionQC
from app.media.watermark import Watermark


def test_existing_settings_migrate_and_switch_preserves_brand(isolated_app):
    # Recreate the settings schema a prior installation had, then start twice.
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute('ALTER TABLE settings DROP COLUMN watermark_enabled')
        conn.execute('ALTER TABLE settings DROP COLUMN watermark_text')
    database.init_db()
    assert database.get_settings()['watermark_enabled'] is False
    assert database.get_settings()['watermark_text'] == ''
    with TestClient(app, client=('127.0.0.1',50000)) as client:
        assert client.post('/api/settings', json={'watermark_enabled':True}).status_code == 400
        saved=client.post('/api/settings',json={'watermark_enabled':True,'watermark_text':' @MyChannel '})
        assert saved.status_code == 200
        assert saved.json()['watermark_enabled'] is True
        assert saved.json()['watermark_text'] == '@MyChannel'
        assert client.post('/api/settings',json={'watermark_enabled':False}).status_code == 200
        assert client.get('/api/settings').json()['watermark_text'] == '@MyChannel'
        assert client.post('/api/settings',json={'watermark_enabled':True}).status_code == 200
        for invalid in ({'watermark_text':'line\nbreak'}, {'watermark_text':'x'*61},
                        {'watermark_text':None}, {'watermark_enabled':'false'}):
            assert client.post('/api/settings',json=invalid).status_code == 400
        assert client.get('/api/settings').json()['watermark_text'] == '@MyChannel'
    database.init_db()
    assert database.get_settings()['watermark_enabled'] is True


def test_final_mp4_has_half_opacity_brand_for_entire_duration_and_off_removes_it(tmp_path):
    source=tmp_path/'source.mp4'
    run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0x303030:s=1080x1920:r=15:d=2',
                 '-f','lavfi','-i','sine=frequency=440:duration=2:sample_rate=48000',
                 '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)])
    text="@ClipRank: 50% 'brand'"
    graphic=Watermark.render(tmp_path/'expected.png',1080,1920,text)
    rgba=np.array(Image.open(graphic))
    assert rgba[:,:,3].max() == 128
    ys,xs=np.where(rgba[:,:,3]>0)
    assert xs.min()>70 and xs.max()<650 and ys.min()>400 and ys.max()<500
    white=(rgba[:,:,3]==128)&np.all(rgba[:,:,:3]==255,axis=2)
    assert white.sum()>100
    plain=ProductionQC.master_audio(source,tmp_path/'off.mp4',{'watermark_enabled':False,'watermark_text':text})
    branded=ProductionQC.master_audio(source,tmp_path/'on.mp4',{'watermark_enabled':True,'watermark_text':text})
    for time in (.05,1,1.85):
        def frame(path):
            raw=run_process(['ffmpeg','-v','error','-ss',str(time),'-i',str(path),'-frames:v','1',
                             '-pix_fmt','rgb24','-f','rawvideo','-'])
            return np.frombuffer(raw,dtype=np.uint8).reshape(1920,1080,3).astype(float)
        original,result=frame(plain),frame(branded)
        opacity=np.mean((result[white]-original[white])/(255-original[white]))
        assert .44 < opacity < .56
        assert np.mean(np.abs(result[800:1100]-original[800:1100]))<2
    probe=json.loads(run_process(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(branded)]))
    video,audio=probe['streams']
    assert (video['codec_name'],video['width'],video['height']) == ('h264',1080,1920)
    assert audio['codec_name']=='aac' and audio['sample_rate']=='48000'
    assert abs(float(probe['format']['duration'])-2)<.08

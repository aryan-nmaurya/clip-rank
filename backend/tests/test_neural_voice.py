import asyncio
import numpy as np
import pytest
from app.tts.voice_engine import TTSEngine
from app.core.runtime import run_process
from app.media.reframer import VideoReframer


def test_neural_service_failure_never_falls_back_to_system_voice(tmp_path, monkeypatch):
    import edge_tts
    class Unavailable:
        def __init__(self,*args,**kwargs): pass
        async def save(self,path): raise RuntimeError('Speech service unavailable')
    monkeypatch.setattr(edge_tts,'Communicate',Unavailable)
    path=tmp_path/'voice.wav'
    with pytest.raises(ValueError, match='Neural narration is unavailable'):
        asyncio.run(TTSEngine.synthesize('At number three.',path,'en-US-GuyNeural'))
    assert not path.exists()
    assert not path.with_suffix('.mp3').exists()


def test_old_voice_choices_migrate_to_neural_voices(isolated_app):
    from app.core import database
    database.update_settings({'default_voice':'Daniel'})
    database.init_db()
    assert database.get_settings()['default_voice'] == 'en-GB-RyanNeural'
    assert TTSEngine.resolve_voice('Samantha') == 'en-US-AriaNeural'
    with pytest.raises(ValueError): TTSEngine.resolve_voice('unrecognized-voice')


def test_source_audio_restores_after_commentary(tmp_path,footage):
    voice=tmp_path/'voice.wav'
    run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=1000:duration=2',str(voice)])
    out=VideoReframer.reframe_to_vertical(footage[0],tmp_path/'narrated.mp4',duration=5,narration_wav=voice)
    data=run_process(['ffmpeg','-v','error','-i',str(out),'-vn','-ar','8000','-ac','1','-f','f32le','-'])
    samples=np.frombuffer(data,dtype=np.float32)
    def source_power(start):
        window=samples[int(start*8000):int((start+1)*8000)]
        spectrum=np.abs(np.fft.rfft(window))
        frequency=np.fft.rfftfreq(len(window),1/8000)
        return spectrum[np.abs(frequency-300)<5].max()
    assert source_power(3) > source_power(.5)*3

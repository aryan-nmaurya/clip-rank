import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pytest
import yaml
from app.tts.pocket import PocketTTS
from app.tts.voice_engine import TTSEngine


@pytest.fixture
def installed_assets(isolated_app):
    directory=PocketTTS.directory();directory.mkdir(parents=True)
    for name in ('model.safetensors','tokenizer.json','alba.safetensors','marius.safetensors','javert.safetensors'):
        (directory/name).write_bytes(b'explicit test-only asset')
    (directory/'english.yaml').write_text(yaml.safe_dump({'weights_path':str(directory/'model.safetensors'),
        'flow_lm':{'lookup_table':{'tokenizer_path':str(directory/'tokenizer.json')}}}))
    (directory/'manifest.json').write_text(json.dumps({'config':'english.yaml',
        'voices':{name:name+'.safetensors' for name in ('alba','marius','javert')}}))
    return directory


def test_shared_engine_dispatches_pocket_without_online_fallback(monkeypatch,tmp_path):
    import edge_tts
    edge=Mock();monkeypatch.setattr(edge_tts,'Communicate',edge)
    async def offline(text,output,voice):
        assert voice=='marius'
        raise ValueError('Pocket TTS model not installed')
    monkeypatch.setattr(PocketTTS,'synthesize_timed',offline)
    with pytest.raises(ValueError,match='Pocket TTS model'):
        asyncio.run(TTSEngine.synthesize_timed('Watch the landing.',tmp_path/'voice.wav','pocket:marius'))
    edge.assert_not_called()
    assert TTSEngine.resolve_voice(None)=='pocket:alba'
    with pytest.raises(ValueError): TTSEngine.resolve_voice('pocket:../../outside')


def test_numbers_are_spoken_faithfully_and_wrong_speech_rejected():
    from app.tts.text import spoken_numbers,equivalent_spoken_text,speech_consistent
    assert spoken_numbers('Version 5.0 is 5.5 times faster.')=='Version five point zero is five point five times faster.'
    assert equivalent_spoken_text('At number 3, it improves by 20%.','At number three, it improves by twenty percent.')
    assert not equivalent_spoken_text('It is 6.5 times faster.','It is 5.5 times faster.')
    assert not equivalent_spoken_text('It improves.','It improves by twenty percent.')
    assert not speech_consistent('The team says it can work and moves 6 times faster than the older robot.','The team says it can work and moves 5 times faster than the older robot.')
    assert not speech_consistent('The team says it can work on this task with the new robotics software.','The team says it cannot work on this task with the new robotics software.')


def test_pocket_asset_manifest_rejects_remote_models_and_path_escape(installed_assets):
    PocketTTS.assets()
    path=installed_assets/'english.yaml'
    data=yaml.safe_load(path.read_text());data['weights_path']='https://example.org/model.safetensors'
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError,match='local'): PocketTTS.assets()
    data['weights_path']='../../outside.safetensors';path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError,match='managed model storage'): PocketTTS.assets()


def test_voice_licenses_and_upload_attribution(monkeypatch):
    from app.publishing import youtube
    assert PocketTTS.provenance('marius')['voice_license']=='CC0'
    assert PocketTTS.provenance('alba')['voice_license']=='CC BY 4.0'
    with pytest.raises(ValueError): PocketTTS.provenance('cosette')
    monkeypatch.setattr(youtube.database,'get_project',lambda _:{'result_data':{'voice_provenance':[PocketTTS.provenance('alba')]}})
    credits=youtube._credits({'id':'approved','project_id':'project'})
    assert 'Kyutai Pocket TTS' in credits and 'Alba MacKenna' in credits


def test_pocket_cpu_cache_normalization_and_alignment_failure(installed_assets,monkeypatch):
    from app.tts import pocket
    from app.media.ffmpeg_core import FFmpegCore
    import soundfile as sf
    # Explicit unit fixture only: production calls the real Pocket TTS model.
    class Audio:
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return np.sin(np.arange(24000*2)*2*np.pi*600/24000).astype(np.float32)*.08
    class Model:
        sample_rate=24000
        loads=0
        @classmethod
        def load_model(cls,config): cls.loads+=1;return cls()
        def get_state_for_audio_prompt(self,path): return {'calls':0}
        def generate_audio(self,state,text):
            assert state['calls']==0  # Every generation gets a fresh voice state.
            state['calls']+=1
            return Audio()
    monkeypatch.setitem(sys.modules,'pocket_tts',SimpleNamespace(TTSModel=Model))
    monkeypatch.setattr(pocket,'_model',None);monkeypatch.setattr(pocket,'_signature',None);monkeypatch.setattr(pocket,'_states',{})
    def measured(path,text,duration): return [{'word':w,'start':i*.3,'end':(i+1)*.3} for i,w in enumerate(text.split())]
    monkeypatch.setattr(pocket,'align_words',measured)
    for number in range(2):
        path=installed_assets/f'test_{number}.wav'
        speech=PocketTTS._synthesize('Watch the landing.',path,'alba')
        samples,rate=sf.read(path)
        assert rate==48000 and np.isfinite(samples).all()
        assert speech['words'] and speech['provenance']['engine']=='pocket-tts'
        assert not path.with_suffix('.raw.wav').exists()
    assert Model.loads==1
    def bad(*args): raise ValueError('Speech does not match narration')
    monkeypatch.setattr(pocket,'align_words',bad)
    rejected=installed_assets/'rejected.wav'
    with pytest.raises(ValueError,match='match narration'): PocketTTS._synthesize('Watch the landing.',rejected,'alba')
    assert not rejected.exists()


def test_speech_status_endpoint_is_local_only(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    with TestClient(app,client=('127.0.0.1',1234)) as client:
        result=client.get('/api/speech/status')
        assert result.status_code==200 and not result.json()['ready']
        assert client.get('/api/speech/status',headers={'Origin':'https://foreign.example'}).status_code==403


def test_pocket_retries_rejected_audio_once_without_relaxing_checks(monkeypatch,tmp_path):
    from app.tts.alignment import NarrationAlignmentError
    monkeypatch.setattr(PocketTTS,'status',lambda:{'ready':True})
    call=Mock(side_effect=[NarrationAlignmentError('Different spoken number'),{'words':[]}])
    monkeypatch.setattr(PocketTTS,'_synthesize',call)
    result=asyncio.run(PocketTTS.synthesize_timed('Number five.',tmp_path/'voice.wav'))
    assert call.call_count==2 and result['synthesis_attempts']==2
    call.reset_mock();call.side_effect=NarrationAlignmentError('Different spoken number')
    with pytest.raises(NarrationAlignmentError,match='Different spoken number'):
        asyncio.run(PocketTTS.synthesize_timed('Number five.',tmp_path/'voice.wav'))
    assert call.call_count==2
    call.reset_mock();call.side_effect=RuntimeError('Media encoder failed')
    with pytest.raises(RuntimeError,match='encoder'):
        asyncio.run(PocketTTS.synthesize_timed('Number five.',tmp_path/'voice.wav'))
    assert call.call_count==1

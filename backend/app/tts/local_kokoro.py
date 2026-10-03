"""Optional installed Kokoro runtime. No model/network downloads are performed here."""
import importlib.util
import os
import re
import threading
from pathlib import Path
from app.core.config import STORAGE_DIR
from app.core.runtime import run_blocking, run_process
from app.media.ffmpeg_core import FFmpegCore
from app.media.production_qc import ProductionQC

PROFILES={'Curious':'af_heart','Energetic':'am_fenrir','Tech Curious':'af_heart','Tech Energetic':'am_fenrir','Documentary':'bm_george','Fast Explainer':'af_bella',
          'Fast Entertainment':'am_fenrir','Cinematic':'bm_george','Suspense':'bm_george','Playful':'af_bella','Neutral':'af_heart'}
_lock=threading.Lock()


class LocalKokoro:
    @staticmethod
    def paths():
        directory=STORAGE_DIR/'models'/'kokoro'
        return (Path(os.environ.get('CLIPRANK_KOKORO_MODEL',str(directory/'kokoro-v1.0.onnx'))),
                Path(os.environ.get('CLIPRANK_KOKORO_VOICES',str(directory/'voices-v1.0.bin'))))

    @classmethod
    def status(cls):
        model,voices=cls.paths()
        asr=list((STORAGE_DIR/'models').glob('models--Systran--faster-whisper-base/snapshots/*/model.bin'))
        runtime=all(importlib.util.find_spec(name) for name in ('kokoro_onnx','soundfile','faster_whisper'))
        return {'ready':bool(runtime and model.is_file() and voices.is_file() and asr),
                'runtime_installed':bool(runtime),'model_present':model.is_file(),'voices_present':voices.is_file(),
                'alignment_model_present':bool(asr),'downloads_performed':False,
                'message':'Connect installed Kokoro ONNX/voices and a cached Whisper alignment model. No model is downloaded automatically.'}

    @classmethod
    async def synthesize_timed(cls,text,output,profile='Tech Curious'):
        if not cls.status()['ready']:
            raise ValueError('Local narration is not connected. Install the optional worker runtime and connect existing Kokoro model/voices; or explicitly select online neural narration.')
        return await run_blocking(cls._synthesize,text,output,profile)

    @classmethod
    def _synthesize(cls,text,output,profile):
        from kokoro_onnx import Kokoro
        import soundfile as sf
        from faster_whisper import WhisperModel
        model,voices=cls.paths()
        raw=output.with_suffix('.raw.wav')
        output.parent.mkdir(parents=True,exist_ok=True)
        with _lock:
            samples,rate=Kokoro(str(model),str(voices)).create(text,voice=PROFILES[profile],speed=1.0,lang='en-us')
            sf.write(str(raw),samples,rate)
            run_process(['ffmpeg','-v','error','-y','-i',str(raw),'-af','loudnorm=I=-16:TP=-2:LRA=8','-ar','48000','-ac','1',str(output)])
            raw.unlink(missing_ok=True)
            snapshot=next((STORAGE_DIR/'models').glob('models--Systran--faster-whisper-base/snapshots/*/model.bin')).parent
            aligner=WhisperModel(str(snapshot),device='cpu',compute_type='int8',local_files_only=True)
            stream,_=aligner.transcribe(str(output),language='en',word_timestamps=True,initial_prompt=text,beam_size=5)
            words=[{'word':w.word.strip(),'start':float(w.start),'end':float(w.end)} for s in stream for w in (s.words or [])]
        duration=FFmpegCore.get_video_info(output)['duration']
        ProductionQC.validate_words(words,duration,text)
        return {'text':text,'duration':duration,'words':words,
                'segments':[{'start':words[0]['start'],'end':words[-1]['end'],'text':text,'words':words}],
                'engine':'kokoro + cached Whisper word alignment','voice_profile':profile}

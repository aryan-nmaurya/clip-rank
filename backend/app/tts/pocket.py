"""Pocket TTS runs inside ClipRank on CPU with explicitly installed local assets."""
import importlib.util
import json
import threading
from pathlib import Path
from app.core import config
from app.core.runtime import run_blocking,run_process,check_cancelled
from app.media.ffmpeg_core import FFmpegCore
from app.tts.alignment import snapshot,align_words,NarrationAlignmentError
from app.tts.text import spoken_numbers

VOICES={'alba':'Alba · conversational','marius':'Marius · energetic','javert':'Javert · narrator'}
PROFILES={'Curious':'alba','Energetic':'marius','Tech Curious':'alba','Tech Energetic':'marius','Documentary':'javert','Fast Explainer':'alba',
          'Fast Entertainment':'marius','Cinematic':'javert','Suspense':'javert','Playful':'alba','Neutral':'alba'}
MODEL_CREDIT='Voice generated with Kyutai Pocket TTS (CC BY 4.0): https://huggingface.co/kyutai/pocket-tts-without-voice-cloning'
VOICE_CREDITS={'alba':'Alba MacKenna voice, CC BY 4.0: https://huggingface.co/kyutai/tts-voices',
               'marius':'Marius voice donation, CC0: https://huggingface.co/kyutai/tts-voices',
               'javert':'Javert voice donation, CC0: https://huggingface.co/kyutai/tts-voices'}
_lock=threading.Lock()
_model=None
_signature=None
_states={}


class PocketTTS:
    @staticmethod
    def directory(): return config.STORAGE_DIR/'models'/'pocket-tts'

    @classmethod
    def assets(cls):
        root=cls.directory().resolve()
        manifest=json.loads((root/'manifest.json').read_text())
        def local(value):
            if not isinstance(value,str) or '://' in value: raise ValueError('Pocket TTS assets must be installed local files.')
            path=(root/value).resolve()
            if not path.is_relative_to(root) or not path.is_file(): raise ValueError('A Pocket TTS asset is missing from managed model storage.')
            return path
        yaml_path=local(manifest['config'])
        import yaml
        settings=yaml.safe_load(yaml_path.read_text())
        # An upstream YAML may otherwise initiate its own HTTP/Hugging Face download.
        weights=local(settings['weights_path'])
        tokenizer=local(settings['flow_lm']['lookup_table']['tokenizer_path'])
        for section in (settings,settings.get('flow_lm',{}),settings.get('mimi',{})):
            for key in ('weights_path','weights_path_without_voice_cloning'):
                if section.get(key): local(section[key])
        voices={name:local(manifest['voices'][name]) for name in VOICES}
        return yaml_path,weights,tokenizer,voices

    @classmethod
    def status(cls):
        runtime=all(importlib.util.find_spec(name) is not None for name in ('pocket_tts','torch','soundfile','faster_whisper','yaml'))
        error=None
        try: cls.assets();present=True
        except (OSError,ValueError,KeyError,TypeError,ImportError): present=False
        aligned=snapshot() is not None
        ready=runtime and present and aligned
        if not runtime: error='Install ClipRank backend requirements to enable Pocket TTS.'
        elif not present: error='Install Pocket TTS assets with backend/setup_pocket_tts.py. Generation never downloads models.'
        elif not aligned: error='Connect the cached Whisper base model for synchronized captions.'
        return {'ready':bool(ready),'runtime_installed':runtime,'model_present':present,'alignment_model_present':aligned,
                'engine':'pocket-tts','device':'cpu','voices':list(VOICES),'message':error or 'Pocket TTS is ready on your Mac.','automatic_downloads':False}

    @staticmethod
    def provenance(voice):
        if voice not in VOICES: raise ValueError('Choose an installed Pocket TTS voice.')
        return {'engine':'pocket-tts','runtime_version':'3.3.0','voice':voice,'voice_license':'CC BY 4.0' if voice=='alba' else 'CC0',
                'credits':[MODEL_CREDIT,VOICE_CREDITS[voice]],'language':'en','device':'cpu'}

    @classmethod
    async def synthesize_timed(cls,text,output,voice='alba'):
        if voice not in VOICES: raise ValueError('Choose Alba, Marius or Javert for Pocket TTS.')
        if not text.strip(): raise ValueError('Narration text is empty.')
        if not cls.status()['ready']: raise ValueError(cls.status()['message'])
        # Neural synthesis is stochastic. Retry a rejected waveform once; both
        # attempts must independently pass the same caption checks.
        for attempt in range(2):
            try:
                result=await run_blocking(cls._synthesize,text,Path(output),voice)
                result['synthesis_attempts']=attempt+1
                return result
            except NarrationAlignmentError:
                if attempt==1: raise

    @classmethod
    def _synthesize(cls,text,output,voice):
        global _model,_signature,_states
        from pocket_tts import TTSModel
        import soundfile as sf
        import copy
        yaml_path,weights,tokenizer,voices=cls.assets()
        signature=tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in (yaml_path,weights,tokenizer,*voices.values()))
        raw=output.with_suffix('.raw.wav')
        output.parent.mkdir(parents=True,exist_ok=True)
        try:
            while not _lock.acquire(timeout=.25): check_cancelled()
            try:
                check_cancelled()
                if _model is None or _signature!=signature:
                    _model=TTSModel.load_model(config=str(yaml_path));_signature=signature;_states={}
                if voice not in _states: _states[voice]=_model.get_state_for_audio_prompt(str(voices[voice]))
                audio=_model.generate_audio(copy.deepcopy(_states[voice]),spoken_numbers(text))
                check_cancelled()
                sf.write(str(raw),audio.detach().cpu().numpy(),_model.sample_rate)
            finally: _lock.release()
            run_process(['ffmpeg','-v','error','-y','-i',str(raw),'-af','loudnorm=I=-16:TP=-2:LRA=8','-ar','48000','-ac','1',str(output)])
            duration=FFmpegCore.get_video_info(output)['duration']
            words=align_words(output,text,duration)
            caption_text=' '.join(word['word'] for word in words)
            result={'text':caption_text,'original_text':text,'duration':duration,'words':words,
                    'segments':[{'start':words[0]['start'],'end':words[-1]['end'],'text':caption_text,'words':words}],
                    'engine':'pocket-tts + measured Whisper word alignment','voice':voice,'provenance':cls.provenance(voice)}
            output.with_suffix('.json').write_text(json.dumps(result))
            return result
        except BaseException:
            output.unlink(missing_ok=True);output.with_suffix('.json').unlink(missing_ok=True)
            raise
        finally: raw.unlink(missing_ok=True)

"""Natural neural speech; never silently substitute a robotic system voice."""
import asyncio
import json
import re
from pathlib import Path
from app.core.runtime import run_blocking, run_process
from app.media.ffmpeg_core import FFmpegCore

DEFAULT_VOICE = "pocket:alba"
VOICES = {
    "pocket:alba": "Pocket TTS · Alba",
    "pocket:marius": "Pocket TTS · Marius",
    "pocket:javert": "Pocket TTS · Javert",
    "en-US-GuyNeural": "Guy · US narrator",
    "en-US-AriaNeural": "Aria · US conversational",
    "en-GB-RyanNeural": "Ryan · British narrator",
    "en-IN-PrabhatNeural": "Prabhat · Indian English",
    "en-IN-NeerjaNeural": "Neerja · Indian English",
}
LEGACY_VOICES = {"Samantha": "en-US-AriaNeural", "Ava": "en-US-AriaNeural",
                 "Daniel": "en-GB-RyanNeural", "Alex": "en-US-GuyNeural", "Auto": DEFAULT_VOICE}


class TTSEngine:
    @classmethod
    def validate_ready(cls, voice=None):
        """Check the installed connection without rejecting a job on a test utterance.

        Real narration is synthesized and independently caption-checked in the
        production retry loop. A three-word ASR probe is not a connection test.
        """
        import importlib.util
        voice = cls.resolve_voice(voice)
        if voice.startswith('pocket:'):
            from app.tts.pocket import PocketTTS
            status = PocketTTS.status()
            if not status['ready']:
                raise ValueError(status['message'])
        elif importlib.util.find_spec('edge_tts') is None:
            raise ValueError('Install backend requirements to enable neural narration.')
        return voice

    @staticmethod
    def resolve_voice(voice):
        voice = LEGACY_VOICES.get(voice, voice) or DEFAULT_VOICE
        if voice not in VOICES:
            raise ValueError("Choose a supported narration voice.")
        return voice

    @classmethod
    async def synthesize(cls, text: str, output_wav: Path, voice: str = DEFAULT_VOICE) -> float:
        return (await cls.synthesize_timed(text, output_wav, voice))['duration']

    @classmethod
    async def synthesize_timed(cls, text: str, output_wav: Path, voice: str = DEFAULT_VOICE):
        voice = cls.resolve_voice(voice)
        if voice.startswith('pocket:'):
            from app.tts.pocket import PocketTTS
            return await PocketTTS.synthesize_timed(text,output_wav,voice.split(':',1)[1])
        try:
            import edge_tts
        except ImportError as exc:
            raise ValueError("Install backend requirements to enable neural narration.") from exc
        if not text.strip():
            raise ValueError("Narration text is empty.")
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        mp3 = output_wav.with_suffix(".mp3")
        words = []
        try:
            async with asyncio.timeout(60):
                communicate = edge_tts.Communicate(text, voice, rate="+0%", pitch="+0Hz", boundary='WordBoundary')
                with mp3.open('wb') as stream:
                    async for chunk in communicate.stream():
                        if chunk['type'] == 'audio':
                            stream.write(chunk['data'])
                        elif chunk['type'] == 'WordBoundary':
                            words.append({'word': chunk['text'], 'start': chunk['offset'] / 10_000_000,
                                          'end': (chunk['offset'] + chunk['duration']) / 10_000_000})
            if not words:
                raise ValueError('Speech service did not return word boundaries.')
            await run_blocking(run_process, ["ffmpeg", "-v", "error", "-y", "-i", str(mp3),
                               "-af", f"atrim=end={words[-1]['end']+.12:.4f},loudnorm=I=-16:TP=-1.5:LRA=8", "-ar", "48000", "-ac", "1", str(output_wav)], timeout=60)
            duration = await run_blocking(lambda: FFmpegCore.get_video_info(output_wav)["duration"])
            if duration < .2 or output_wav.stat().st_size < 100:
                raise ValueError("Neural speech returned empty audio.")
            normalize = lambda value: re.findall(r'\w+', value.lower())
            if normalize(' '.join(w['word'] for w in words)) != normalize(text):
                raise ValueError('Speech service returned incomplete word timings; narration cannot be captioned accurately.')
            for word in words:
                if not 0 <= word['start'] < word['end'] <= duration + .12:
                    raise ValueError('Speech word timestamps exceed the narration audio.')
                word['end'] = min(duration, word['end'])
            result = {'duration': duration, 'text': text, 'words': words,
                      'segments': [{'start': words[0]['start'], 'end': words[-1]['end'], 'text': text, 'words': words}],
                      'engine':'edge-neural-word-timed','voice':voice}
            output_wav.with_suffix('.json').write_text(json.dumps(result))
            return result
        except asyncio.CancelledError:
            output_wav.unlink(missing_ok=True)
            raise
        except Exception as exc:
            output_wav.unlink(missing_ok=True)
            raise ValueError("Neural narration is unavailable. Check your internet connection and retry. " + str(exc)[-220:]) from exc
        finally:
            mp3.unlink(missing_ok=True)

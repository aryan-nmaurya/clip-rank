import logging
import threading
from pathlib import Path
from app.core.config import STORAGE_DIR
from app.core.runtime import check_cancelled

logger = logging.getLogger("ai_shorts.transcription")
_lock = threading.Lock()
_model = None


class Transcriber:
    @staticmethod
    def transcribe(audio_path: Path):
        """Only real recognized speech is returned. Failure never creates subtitles."""
        global _model
        try:
            from faster_whisper import WhisperModel
            with _lock:
                check_cancelled()
                if _model is None:
                    _model = WhisperModel("base", device="cpu", compute_type="int8",
                                          download_root=str(STORAGE_DIR / "models"))
                stream, info = _model.transcribe(str(audio_path), beam_size=5,
                                                 word_timestamps=True, vad_filter=True)
                segments = []
                for segment in stream:
                    check_cancelled()
                    if segment.no_speech_prob > .75 or not segment.text.strip():
                        continue
                    segments.append({"start": round(float(segment.start), 3), "end": round(float(segment.end), 3),
                                     "text": segment.text.strip(),
                                     "words": [{"start": float(w.start), "end": float(w.end), "word": w.word.strip()}
                                               for w in (segment.words or [])]})
            return {"text": " ".join(s["text"] for s in segments), "segments": segments,
                    "language": info.language, "available": True}
        except InterruptedError:
            raise
        except Exception as exc:
            logger.warning("Real transcription unavailable: %s", exc)
            return {"text": "", "segments": [], "available": False,
                    "warning": "Speech transcription unavailable; captions were omitted. Install backend requirements and allow the Whisper model download."}

import logging
import threading
import math
from pathlib import Path
from app.core.config import STORAGE_DIR
from app.core.runtime import check_cancelled

logger = logging.getLogger("ai_shorts.transcription")
_lock = threading.Lock()
_model = None


class Transcriber:
    @staticmethod
    def normalize_words(words):
        """Group collapsed/overlapping ASR tokens using their measured boundaries.

        Whisper can assign zero duration to a syllable or punctuation. Keep its
        text in an adjacent caption group instead of inventing a new timestamp
        or passing an invalid zero-duration word to the renderer.
        """
        result=[];leading=[]
        for raw in words:
            word=str(raw.get('word','')).strip()
            if not word: continue
            start,end=raw['start'],raw['end']
            if not all(type(t) in (int,float) and math.isfinite(t) for t in (start,end)) or not 0<=start<=end:
                raise ValueError('Source speech has invalid measured word boundaries.')
            if start==end:
                if result:
                    result[-1]['word']+=' '+word
                    result[-1]['end']=max(result[-1]['end'],end)
                else: leading.append(word)
                continue
            value={'word':' '.join([*leading,word]),'start':start,'end':end};leading=[]
            if result and start<result[-1]['end']:
                result[-1]['word']+=' '+value['word']
                result[-1]['end']=max(result[-1]['end'],end)
            else: result.append(value)
        if leading: raise ValueError('Recognized speech has no usable measured duration.')
        return result

    @staticmethod
    def words_in_window(words,start,end):
        # Timing tolerance may admit a word immediately outside a cut. Require
        # actual overlap before clamping, or both boundaries can collapse to 0.
        return [{**w,'start':max(0,w['start']-start),'end':min(end-start,w['end']-start)}
                for w in Transcriber.normalize_words(words)
                if start-.03<=w['start'] and w['end']<=end+.03
                and w['end']>start and w['start']<end]

    @staticmethod
    def transcribe(audio_path: Path, *, vad_filter=True, language=None, condition_on_previous_text=True):
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
                    word_timestamps=True, vad_filter=vad_filter, language=language,
                    condition_on_previous_text=condition_on_previous_text)
                segments = []
                for segment in stream:
                    check_cancelled()
                    if segment.no_speech_prob > .75 or not segment.text.strip():
                        continue
                    segments.append({"start": round(float(segment.start), 3), "end": round(float(segment.end), 3),
                                     "text": segment.text.strip(),
                                     "words": Transcriber.normalize_words([
                                         {"start": float(w.start), "end": float(w.end), "word": w.word.strip()}
                                         for w in (segment.words or [])])})
            return {"text": " ".join(s["text"] for s in segments), "segments": segments,
                    "language": info.language, "available": True}
        except InterruptedError:
            raise
        except Exception as exc:
            logger.warning("Real transcription unavailable: %s", exc)
            return {"text": "", "segments": [], "available": False,
                    "warning": "Speech transcription unavailable; captions were omitted. Install backend requirements and allow the Whisper model download."}

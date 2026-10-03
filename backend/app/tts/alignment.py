"""Measured word timings from an existing local Whisper model; no downloads."""
import threading
import json
from app.core import config
from app.core.runtime import check_cancelled
from app.media.production_qc import ProductionQC
from app.tts.text import equivalent_spoken_text,speech_consistent,spoken_numbers

_lock=threading.Lock()
_model=None
_loaded_path=None


class NarrationAlignmentError(ValueError):
    """Generated speech cannot support accurate captions; rewrite or retry it."""


def snapshot():
    files=sorted((config.STORAGE_DIR/'models').glob('models--Systran--faster-whisper-base/snapshots/*/model.bin'))
    return files[-1].parent if files else None


def align_words(path,text,duration):
    global _model,_loaded_path
    from faster_whisper import WhisperModel
    directory=snapshot()
    if not directory: raise ValueError('Connect the cached Whisper base model for accurate Pocket TTS captions.')
    with _lock:
        check_cancelled()
        if _model is None or _loaded_path!=directory:
            _model=WhisperModel(str(directory),device='cpu',compute_type='int8',local_files_only=True)
            _loaded_path=directory
        # Independently recognize the waveform. Even a glossary containing the
        # opening word (e.g. "Watch") can make Whisper treat that word as already
        # spoken and omit it, then hallucinate extra speech in trailing silence.
        # These files contain only synthesized speech, often starting at sample
        # zero. VAD can discard a quiet opening word in a short TTS beat and
        # make Whisper hallucinate an ending. Decode the complete waveform;
        # source-footage transcription still uses VAD for noisy recordings.
        stream,_=_model.transcribe(str(path),language='en',word_timestamps=True,initial_prompt=None,
                                   condition_on_previous_text=False,vad_filter=False,beam_size=5)
        words=[]
        for segment in stream:
            check_cancelled()
            words.extend({'word':w.word.strip(),'start':float(w.start),'end':float(w.end)} for w in (segment.words or []))
        actual=' '.join(w['word'] for w in words)
        diagnostic={'expected_text':text,'duration':duration,'recognized_text':actual,'words':words}
        path.with_suffix('.alignment.json').write_text(json.dumps(diagnostic,indent=2))
        # ASR can mis-spell an acronym or a quiet function word. In that case
        # align the known narration against the ACTUAL waveform, rather than
        # copying an ASR typo into the captions or estimating word durations.
        if not equivalent_spoken_text(actual,text):
            if not speech_consistent(actual,text):
                raise NarrationAlignmentError('Recognized speech does not match the narration. Rewrite the narration in simpler spoken language.')
            from faster_whisper.audio import decode_audio,pad_or_trim
            from faster_whisper.tokenizer import Tokenizer
            from faster_whisper.transcribe import merge_punctuations
            waveform=decode_audio(str(path),sampling_rate=16000)
            features=_model.feature_extractor(waveform)
            frames=features.shape[-1]-1
            if frames>_model.feature_extractor.nb_max_frames:
                raise NarrationAlignmentError('Split narration into beats under thirty seconds for accurate alignment.')
            tokenizer=Tokenizer(_model.hf_tokenizer,_model.model.is_multilingual,task='transcribe',language='en')
            encoded=_model.encode(pad_or_trim(features))
            timings=_model.find_alignment(tokenizer,[tokenizer.encode(' '+spoken_numbers(text))],encoded,frames)[0]
            merge_punctuations(timings,'\"\'“¿([{-','\"\'.。,，!！?？:：”)]}、')
            timed=[w for w in timings if w['word'].strip()]
            probabilities=[float(w['probability']) for w in timed]
            diagnostic['forced_alignment']=[{**w,'tokens':list(w['tokens']),'start':float(w['start']),
                'end':float(w['end']),'probability':float(w['probability'])} for w in timed]
            path.with_suffix('.alignment.json').write_text(json.dumps(diagnostic,indent=2))
            if not probabilities or min(probabilities)<.08 or sum(probabilities)/len(probabilities)<.5:
                raise NarrationAlignmentError('Actual audio alignment confidence is too low. Rewrite or regenerate the narration.')
            words=[{'word':w['word'].strip(),'start':float(w['start']),'end':float(w['end'])} for w in timed]
            try: ProductionQC.validate_words(words,duration,spoken_numbers(text))
            except ValueError as exc: raise NarrationAlignmentError('Measured caption alignment failed: '+str(exc)) from exc
    path.with_suffix('.alignment.json').write_text(json.dumps({'expected_text':text,'duration':duration,'recognized_text':actual,'words':words},indent=2))
    try:
        ProductionQC.validate_words(words,duration,' '.join(w['word'] for w in words))
    except ValueError as exc:
        raise NarrationAlignmentError('Generated speech could not be captioned accurately. Rewrite complex abbreviations and decimal numbers as natural spoken words. '+str(exc)) from exc
    return words

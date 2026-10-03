"""Short TTS beats must be checked against unbiased, measured recognition."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.tts import alignment


def recognizer(monkeypatch, tmp_path, text):
    import faster_whisper
    directory=tmp_path/'cached-model'
    directory.mkdir()
    monkeypatch.setattr(alignment,'snapshot',lambda:directory)
    monkeypatch.setattr(alignment,'_model',None)
    monkeypatch.setattr(alignment,'_loaded_path',None)
    def transcribe(path, **options):
        assert options['initial_prompt'] is None
        assert options['vad_filter'] is False
        assert options['condition_on_previous_text'] is False
        assert options['word_timestamps'] is True
        words=[SimpleNamespace(word=w,start=i*.2,end=(i+1)*.2) for i,w in enumerate(text.split())]
        return iter([SimpleNamespace(words=words)]),None
    model=SimpleNamespace(transcribe=Mock(side_effect=transcribe))
    loader=Mock(return_value=model)
    monkeypatch.setattr(faster_whisper,'WhisperModel',loader)
    return loader,model,directory


def test_short_speech_keeps_opening_word_without_prompt_or_vad(monkeypatch,tmp_path):
    loader,model,directory=recognizer(monkeypatch,tmp_path,'Watch the landing.')
    for name in ('first','second'):
        path=tmp_path/(name+'.wav')
        words=alignment.align_words(path,'Watch the landing.',1.44)
        assert [w['word'] for w in words]==['Watch','the','landing.']
        assert words[0]['start']==0 and words[-1]['end']<1.44
        report=json.loads(path.with_suffix('.alignment.json').read_text())
        assert report['recognized_text']=='Watch the landing.'
    loader.assert_called_once_with(str(directory),device='cpu',compute_type='int8',local_files_only=True)
    assert model.transcribe.call_count==2


@pytest.mark.parametrize('actual,expected',[
    ('the landing. Thank you.','Watch the landing.'),
    ('Watch the landing. Thank you.','Watch the landing.'),
    ('Number four is the biggest fall.','Number five is the biggest fall.'),
    ('He can make this landing.','He cannot make this landing.'),
])
def test_incorrect_speech_is_still_rejected(monkeypatch,tmp_path,actual,expected):
    recognizer(monkeypatch,tmp_path,actual)
    with pytest.raises(alignment.NarrationAlignmentError,match='does not match'):
        alignment.align_words(tmp_path/'rejected.wav',expected,4)


def test_equivalent_spoken_rank_uses_measured_boundaries(monkeypatch,tmp_path):
    recognizer(monkeypatch,tmp_path,'Number five.')
    words=alignment.align_words(tmp_path/'rank.wav','Number 5.',1)
    assert [w['word'] for w in words]==['Number','five.']
    assert [(w['start'],w['end']) for w in words]==[(0,.2),(.2,.4)]

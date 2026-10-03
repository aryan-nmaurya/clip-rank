"""Sad-visuals mode: pictures, a headline and sad music only. The source sound is removed; nothing is spoken or captioned."""
import asyncio
import numpy as np
from app.core import config
from app.core.database import create_job, create_project, get_project
from app.core.runtime import run_process
from app.engines import auto_shorts
from app.media.ffmpeg_core import FFmpegCore
from app.media.reframer import VideoReframer
from app.pipelines.viral.viral_pipeline import ViralPipeline
from app.tts.voice_engine import TTSEngine


def sad_visual(name, footage):
    create_project(name, 'viral', 'Sad visual', {})
    create_job(name + '_job', name)
    settings = {'ai_provider': 'auto', 'whole_video': True, 'no_narration': True, 'heading': True, 'music': 'sad', 'style': 'sad_visual',
                'visuals_only': True, 'mute_source': True, 'layout': 'smart'}
    asyncio.run(ViralPipeline.run(name + '_job', name, str(footage[0]), 1, settings, lambda *a: None))
    return get_project(name)


def band_energy(video, lo, hi):
    raw = run_process(['ffmpeg', '-v', 'error', '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'])
    x = np.frombuffer(raw, dtype=np.float32)[16000 * 3:16000 * 15]
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / 16000)
    return float(spectrum[(freqs >= lo) & (freqs <= hi)].sum() / spectrum.sum())


def test_the_mode_is_registered_with_queries_and_a_duration_range():
    assert 'sad_visual' in auto_shorts.MODES and auto_shorts.SWEET_SPOTS['sad_visual'][0] < auto_shorts.SWEET_SPOTS['sad_visual'][1]
    assert auto_shorts.queries_for('general', None, 'sad_visual') == auto_shorts.NICHES['sad_visual']


def test_the_mute_option_drops_the_source_sound(tmp_path):
    src = tmp_path / 'src.mp4'
    run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=6', '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=6',
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(src)])
    def tone(out):
        raw = run_process(['ffmpeg', '-v', 'error', '-i', str(out), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'])
        x = np.frombuffer(raw, dtype=np.float32)
        return float(np.sqrt((x ** 2).mean()))
    kept = VideoReframer.reframe_to_vertical(src, tmp_path / 'kept.mp4', start=0, duration=6)
    muted = VideoReframer.reframe_to_vertical(src, tmp_path / 'muted.mp4', start=0, duration=6, mute_source=True)
    assert tone(kept) > .02 and tone(muted) < .001
    assert FFmpegCore.get_video_info(muted)['has_audio']


def test_a_sad_visual_short_has_heading_and_music_but_no_voice_no_captions_no_source_sound(isolated_app, long_footage, ranking_vision, monkeypatch):
    spoken, transcribed = [], []
    original = TTSEngine.synthesize_timed
    async def spy(text, path, voice=None):
        spoken.append(text)
        return await original(text, path, voice)
    monkeypatch.setattr(TTSEngine, 'synthesize_timed', spy)
    from app.transcription.transcriber import Transcriber
    real = Transcriber.transcribe
    monkeypatch.setattr(Transcriber, 'transcribe', staticmethod(lambda *a, **k: transcribed.append(1) or real(*a, **k)))
    project = sad_visual('sad_visual_one', long_footage)
    assert project['status'] == 'COMPLETED' and project['clips'][0]['duration'] > 23
    assert spoken == [] and transcribed == []
    beat = project['result_data']['moments'][0]['timeline'][0]
    assert beat['speech'] is None and beat['heading'] and beat['music']['provenance']['generated'] is True
    path = config.STORAGE_DIR / project['clips'][0]['video_path'].lstrip('/')
    assert band_energy(path, 280, 320) < .05      # the 300 Hz tone from the source footage is gone
    info = FFmpegCore.get_video_info(path)
    assert (info['width'], info['height']) == (1080, 1920) and info['has_audio']

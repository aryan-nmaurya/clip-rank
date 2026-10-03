"""Emotional mode: a headline over the whole video, sad music under the original audio, no voice-over."""
import asyncio
import numpy as np
import pytest
from app.core import config
from app.core.database import create_job, create_project, get_project
from app.core.runtime import run_process
from app.media import music_gen
from app.media.ffmpeg_core import FFmpegCore
from app.pipelines.viral.viral_pipeline import ViralPipeline
from app.tts.voice_engine import TTSEngine


def emotional(name, footage, **extra):
    create_project(name, 'viral', 'Emotional', {})
    create_job(name + '_job', name)
    settings = {'ai_provider': 'auto', 'whole_video': True, 'no_narration': True, 'heading': True, 'style': 'emotional', 'layout': 'smart', **extra}
    asyncio.run(ViralPipeline.run(name + '_job', name, str(footage[0]), 1, settings, lambda *a: None))
    return get_project(name)


def band_energy(video, lo, hi):
    raw = run_process(['ffmpeg', '-v', 'error', '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'])
    x = np.frombuffer(raw, dtype=np.float32)[16000 * 3:16000 * 15]
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / 16000)
    return float(spectrum[(freqs >= lo) & (freqs <= hi)].sum() / spectrum.sum())


def clip_path(project):
    return config.STORAGE_DIR / project['clips'][0]['video_path'].lstrip('/')


def test_emotional_short_has_heading_no_voice_over_and_audible_music(isolated_app, long_footage, ranking_vision, monkeypatch, tmp_path):
    spoken = []
    original = TTSEngine.synthesize_timed
    async def spy(text, path, voice=None):
        spoken.append(text)
        return await original(text, path, voice)
    monkeypatch.setattr(TTSEngine, 'synthesize_timed', spy)
    with_music = emotional('emo_music', long_footage, music='sad')
    assert with_music['status'] == 'COMPLETED' and with_music['clips'][0]['duration'] > 23
    assert spoken == [], 'emotional mode must not synthesize any voice-over'
    beat = with_music['result_data']['moments'][0]['timeline'][0]
    assert beat['speech'] is None and beat['heading'] == with_music['result_data']['moments'][0]['script']['hook']
    provenance = beat['music']['provenance']
    assert provenance['generated'] is True and provenance['license'] == 'original' and provenance['rights_status'] == 'commercial_use_permitted'
    other = tmp_path / 'long_other.mp4'                                # different footage with the same 300 Hz source tone (a reused source is refused)
    run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=642x360:rate=15:duration=24', '-f', 'lavfi', '-i', 'sine=frequency=300:duration=24',
                 '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(other)])
    plain = emotional('emo_plain', [other])                            # same edit without music
    low = (90, 280)                                                    # where the A-minor pad lives; the 300 Hz source tone sits just above
    assert band_energy(clip_path(with_music), *low) > band_energy(clip_path(plain), *low) * 3
    info = FFmpegCore.get_video_info(clip_path(with_music))
    assert (info['width'], info['height']) == (1080, 1920) and info['has_audio']


def test_the_heading_is_visible_for_the_whole_video_not_just_the_first_seconds(isolated_app, long_footage, ranking_vision):
    project = emotional('emo_heading', long_footage, music='sad')
    path = clip_path(project)
    def top_ink(t):     # the heading card sits in the top band; measure how much of it is bright text
        raw = run_process(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', str(path), '-frames:v', '1', '-vf', 'crop=1080:420:0:60,scale=270:105,format=gray',
                           '-f', 'rawvideo', '-'])
        return float((np.frombuffer(raw, dtype=np.uint8) > 200).mean())
    early, late = top_ink(1.0), top_ink(20.0)
    assert early > .02 and late > .02, (early, late)


def test_music_comes_from_the_licensed_library_first(tmp_path, monkeypatch):
    from app.pipelines.movie.music import CinematicMusicDirector
    track = {'title': 'My licensed sad piano'}
    called = []
    monkeypatch.setattr(CinematicMusicDirector, 'select', classmethod(lambda cls, mood: called.append(mood) or track))
    def prepare(cls, chosen, folder, payoff, duration):
        called.append((chosen['title'], duration))
        return tmp_path / 'lib.wav', {'track_name': chosen['title'], 'license': 'CC BY 4.0', 'provenance': {}}
    monkeypatch.setattr(CinematicMusicDirector, 'prepare', classmethod(prepare))
    path, info = music_gen.sad_bed(tmp_path / 'job', 31.0)
    assert called == ['emotional', ('My licensed sad piano', 31.0)] and info['generated'] is False and info['source'] == 'licensed library'


def test_a_broken_library_never_stops_a_short(tmp_path, monkeypatch):
    from app.pipelines.movie.music import CinematicMusicDirector
    monkeypatch.setattr(CinematicMusicDirector, 'select', classmethod(lambda cls, mood: (_ for _ in ()).throw(ValueError('library unreadable'))))
    path, info = music_gen.sad_bed(tmp_path / 'job', 12.0)
    assert info['generated'] is True and abs(FFmpegCore.get_video_info(path)['duration'] - 12.0) < .2


def test_generated_music_is_deterministic_and_cached(tmp_path):
    command = music_gen.build_loop_command(tmp_path / 'a.wav')
    assert command == music_gen.build_loop_command(tmp_path / 'a.wav') and command[0] == 'ffmpeg'
    first = music_gen.generate_loop(tmp_path / 'cache')
    stamp = first.stat().st_mtime_ns
    assert music_gen.generate_loop(tmp_path / 'cache') == first and first.stat().st_mtime_ns == stamp
    assert music_gen.LOOP_SECONDS == 32.0 and FFmpegCore.get_video_info(first)['duration'] > 32


def test_the_bed_has_a_gentle_start_and_a_fade_out(tmp_path):
    bed, _ = music_gen.sad_bed(tmp_path / 'job', 20.0)
    raw = run_process(['ffmpeg', '-v', 'error', '-i', str(bed), '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'])
    x = np.frombuffer(raw, dtype=np.float32)
    rms = lambda part: float(np.sqrt((part ** 2).mean()))
    assert rms(x[:4000]) < rms(x[16000 * 8:16000 * 10]) * .5 and rms(x[-4000:]) < rms(x[16000 * 8:16000 * 10]) * .5


def test_music_ducks_under_spoken_words(tmp_path):
    from app.media.reframer import VideoReframer
    silent = tmp_path / 'src.mp4'
    run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=8', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                 '-t', '8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(silent)])
    bed, _ = music_gen.sad_bed(tmp_path / 'job', 8.0)
    out = VideoReframer.reframe_to_vertical(silent, tmp_path / 'out.mp4', start=0, duration=8, music_wav=bed,
                                            protected_regions=[{'start': 3.0, 'end': 5.0}], music_level=.24)
    raw = run_process(['ffmpeg', '-v', 'error', '-i', str(out), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'])
    x = np.frombuffer(raw, dtype=np.float32)
    rms = lambda a, b: float(np.sqrt((x[int(a * 16000):int(b * 16000)] ** 2).mean()))
    assert rms(3.4, 4.6) < rms(1.5, 2.7) * .6 and rms(6.0, 7.0) > rms(3.4, 4.6)

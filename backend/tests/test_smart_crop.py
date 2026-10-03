"""Landscape footage is cropped to a subject-following 9:16 window; vertical footage stays whole."""
import numpy as np
import pytest
from app.core.runtime import run_process
from app.media import smart_crop
from app.media.ffmpeg_core import FFmpegCore
from app.media.reframer import VideoReframer
from app.pipelines.movie.framing import MovieReframing


def source(path, size, seconds=6):
    run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=30:duration={seconds}',
                 '-f', 'lavfi', '-i', f'sine=frequency=300:duration={seconds}', '-c:v', 'libx264', '-preset', 'ultrafast',
                 '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)])
    return path


def frame(video, t=2.0):
    raw = run_process(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', str(video), '-frames:v', '1', '-vf', 'scale=108:192',
                       '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    return np.frombuffer(raw, dtype=np.uint8).reshape(192, 108, 3).astype(float)


def test_landscape_is_detected_by_aspect_ratio():
    assert smart_crop.is_landscape({'width': 1920, 'height': 1080})
    assert not smart_crop.is_landscape({'width': 1080, 'height': 1920})
    assert not smart_crop.is_landscape({'width': 1000, 'height': 1000})


def test_landscape_gets_a_tracked_window_with_the_canvas_aspect_ratio(tmp_path):
    video = source(tmp_path / 'wide.mp4', '1280x720')
    info = FFmpegCore.get_video_info(video)
    framing, fallback = smart_crop.decide(video, info, 0, 5, 1080, 1459)       # Ranking canvas (title bands)
    assert framing['layout'] == 'tracked' and fallback == 'fit'
    assert framing['crop_width'] == round(720 * 1080 / 1459)                    # exact aspect: no stretching
    assert all(0 <= p['x'] <= 1280 - framing['crop_width'] for p in framing['points'])
    full, _ = smart_crop.decide(video, info, 0, 5, 1080, 1920)
    assert full['crop_width'] == round(720 * 9 / 16)


def test_portrait_source_is_kept_whole(tmp_path):
    video = source(tmp_path / 'tall.mp4', '720x1280')
    assert smart_crop.decide(video, FFmpegCore.get_video_info(video), 0, 5, 1080, 1920) == (None, 'fit')


def test_untrackable_landscape_is_centre_cropped_not_letterboxed(tmp_path, monkeypatch):
    video = source(tmp_path / 'wide.mp4', '1280x720')
    def broken(*args, **kwargs): raise ValueError('A mobile crop tracking sample could not be decoded.')
    monkeypatch.setattr(MovieReframing, 'plan', staticmethod(broken))
    assert smart_crop.decide(video, FFmpegCore.get_video_info(video), 0, 5, 1080, 1920) == (None, 'fill')


def gradient_source(path, seconds=5):
    """Luma rises left to right, so how much of that range appears on screen shows how much of the width was kept."""
    run_process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c=gray:s=1280x720:r=30:d={seconds}',
                 '-vf', "geq=lum='X/W*255':cb=128:cr=128", '-pix_fmt', 'yuv420p', str(path)])
    return path


def luma_span(video, row_fraction=.5):
    raw = run_process(['ffmpeg', '-v', 'error', '-ss', '1.5', '-i', str(video), '-frames:v', '1', '-vf', 'scale=216:384,format=gray',
                       '-f', 'rawvideo', '-'])
    image = np.frombuffer(raw, dtype=np.uint8).reshape(384, 216).astype(float)
    row = image[int(384 * row_fraction)]
    return row[-8:].mean() - row[:8:].mean()


@pytest.mark.parametrize('title_band', [False, True])
def test_smart_render_shows_a_portrait_window_not_the_whole_widescreen_frame(tmp_path, title_band):
    video = gradient_source(tmp_path / 'wide.mp4')
    smart = VideoReframer.reframe_to_vertical(video, tmp_path / 'smart.mp4', start=0, duration=4, layout='smart', title_band=title_band)
    fit = VideoReframer.reframe_to_vertical(video, tmp_path / 'fit.mp4', start=0, duration=4, layout='fit', title_band=title_band)
    assert (FFmpegCore.get_video_info(smart)['width'], FFmpegCore.get_video_info(smart)['height']) == (1080, 1920)
    assert luma_span(fit) > 200                       # letterboxed: the entire left-to-right range sits in the strip
    assert 20 < luma_span(smart) < 120                # cropped: only the ~1/3 window around the subject fills the screen


def test_smart_render_is_a_letterbox_free_fit_for_vertical_sources(tmp_path, monkeypatch):
    video = source(tmp_path / 'tall.mp4', '720x1280')
    calls = []
    original = MovieReframing.plan
    monkeypatch.setattr(MovieReframing, 'plan', staticmethod(lambda *a, **k: calls.append(1) or original(*a, **k)))
    out = VideoReframer.reframe_to_vertical(video, tmp_path / 'out.mp4', start=0, duration=5, layout='smart')
    assert calls == [] and FFmpegCore.get_video_info(out)['height'] == 1920


def test_explicit_layouts_are_unchanged(tmp_path, monkeypatch):
    video = source(tmp_path / 'wide.mp4', '1280x720')
    calls = []
    original = MovieReframing.plan
    monkeypatch.setattr(MovieReframing, 'plan', staticmethod(lambda *a, **k: calls.append(1) or original(*a, **k)))
    for layout in ('fit', 'fill'):
        VideoReframer.reframe_to_vertical(video, tmp_path / f'{layout}.mp4', start=0, duration=3, layout=layout)
    assert calls == []


def test_api_accepts_smart_and_defaults_to_it():
    from app.models.schemas import RankingCreateRequest
    assert RankingCreateRequest(topic='x').layout == 'smart'
    assert RankingCreateRequest(topic='x', layout='fit').layout == 'fit'

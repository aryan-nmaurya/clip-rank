"""Media regressions for rendering and responsive background execution."""
import asyncio
import sys
import time
import numpy as np
from app.core.runtime import run_process, run_blocking
from app.media.reframer import VideoReframer
from app.media.ffmpeg_core import FFmpegCore
from app.media.captions import CaptionRenderer


def test_silent_source_renders(tmp_path):
    path = tmp_path / "silent.mp4"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=180x320:duration=2:rate=15", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    out = VideoReframer.reframe_to_vertical(path, tmp_path / "vertical.mp4", duration=10)
    info = FFmpegCore.validate_output(out, 2)
    assert info["has_audio"]


def test_narration_is_actually_mixed(tmp_path, footage):
    voice = tmp_path / "voice.wav"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=2", str(voice)])
    out = VideoReframer.reframe_to_vertical(footage[0], tmp_path / "narrated.mp4", duration=2, narration_wav=voice)
    data = run_process(["ffmpeg", "-v", "error", "-i", str(out), "-vn", "-ar", "8000", "-ac", "1", "-f", "f32le", "-"])
    samples = np.frombuffer(data, dtype=np.float32)
    spectrum = np.abs(np.fft.rfft(samples))
    freq = np.fft.rfftfreq(len(samples), 1/8000)
    narration_power = spectrum[np.abs(freq - 1000) < 5].max()
    source_power = spectrum[np.abs(freq - 300) < 5].max()
    assert narration_power > source_power * 2


def test_timed_captions_use_speech(tmp_path):
    segments = [{"start": 2, "end": 3, "text": "A real moment", "words": [
        {"start": 2, "end": 2.3, "word": "A"}, {"start": 2.3, "end": 2.6, "word": "real"}, {"start": 2.6, "end": 3, "word": "moment"}]}]
    path = CaptionRenderer.write_ass(tmp_path / "captions.ass", segments, 2, 4)
    assert "0:00:00.00,0:00:00.30" in path.read_text()
    assert "REAL" in path.read_text()
    assert CaptionRenderer.write_ass(tmp_path / "empty.ass", [], 0, 3) is None


def test_media_work_does_not_block_loop_and_can_cancel():
    async def exercise():
        task = asyncio.create_task(run_blocking(run_process, [sys.executable, "-c", "import time; time.sleep(10)"]))
        started = time.monotonic()
        await asyncio.sleep(.08)
        assert time.monotonic() - started < .5
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert time.monotonic() - started < 2
    asyncio.run(exercise())


def test_caption_track_renders_without_libass(tmp_path, footage):
    segments = [{"start": 0, "end": 1, "text": "Real timed captions", "words": [
        {"start": 0, "end": .3, "word": "Real"}, {"start": .3, "end": .6, "word": "timed"}, {"start": .6, "end": 1, "word": "captions"}]}]
    captions = CaptionRenderer.write_ass(tmp_path / "speech.ass", segments, 0, 2)
    out = VideoReframer.reframe_to_vertical(footage[0], tmp_path / "captioned.mp4", duration=2, captions_ass=captions)
    FFmpegCore.validate_output(out, 2)
    assert captions.with_suffix('.mov').is_file()
    image = tmp_path / "caption.jpg"
    FFmpegCore.extract_frame(out, image, .4)
    from PIL import Image
    with Image.open(image) as frame:
        pixels = np.array(frame)[990:1050, 80:640]
        # The active word must contain real yellow pixels above the dark outline.
        yellow = (pixels[:, :, 0] > 200) & (pixels[:, :, 1] > 180) & (pixels[:, :, 2] < 100)
        assert yellow.sum() > 100


def test_current_shorts_search_cards_are_parsed():
    import json
    from app.sources.discovery import SourceDiscovery
    card = {"shortsLockupViewModel": {"onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": "KId3r5dVwGk"}}},
            "overlayMetadata": {"primaryText": {"content": "Funny cat reaction"}}}}
    html = '<script>var ytInitialData = ' + json.dumps({"contents": [card, card]}) + ';</script>'
    entries = SourceDiscovery.parse_search_page(html)
    assert len(entries) == 1
    assert entries[0]["title"] == "Funny cat reaction"
    assert entries[0]["url"] == "https://www.youtube.com/shorts/KId3r5dVwGk"

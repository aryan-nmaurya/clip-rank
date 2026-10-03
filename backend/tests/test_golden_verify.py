"""The delivery spec is enforced on the actual file, with every violated rule reported."""
import pytest
from app.core.runtime import run_process
from app.media.verify import VerificationError, verify_mp4


def render(path, *, size='1080x1920', fps=30, seconds=11, audio=True, vcodec='libx264', pix='yuv420p', extra=()):
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={fps}:duration={seconds}']
    if audio:
        cmd += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}:sample_rate=48000']
    cmd += ['-c:v', vcodec, '-preset', 'ultrafast', '-pix_fmt', pix]
    if audio:
        cmd += ['-c:a', 'aac', '-ar', '48000', '-ac', '2']
    run_process([*cmd, *extra, str(path)])
    return path


def test_conforming_short_passes_with_full_report(tmp_path):
    report = verify_mp4(render(tmp_path / 'ok.mp4'), expected_duration=11)
    assert report['checks_passed'] and (report['width'], report['height']) == (1080, 1920)
    assert report['video_codec'] == 'h264' and report['audio_codec'] == 'aac' and report['sample_rate'] == 48000
    assert report['frame_count'] >= 300 and report['size_bytes'] > 20_000


def test_every_violation_is_listed_not_just_the_first(tmp_path):
    with pytest.raises(VerificationError) as caught:
        verify_mp4(render(tmp_path / 'bad.mp4', size='720x1280', fps=24, seconds=4))
    text = ' '.join(caught.value.failures)
    assert '720x1280' in text and 'duration' in text and 'frame rate' in text


def test_missing_audio_is_rejected_when_expected(tmp_path):
    with pytest.raises(VerificationError, match='audio stream'):
        verify_mp4(render(tmp_path / 'silent.mp4', audio=False))
    assert verify_mp4(tmp_path / 'silent.mp4', expect_audio=False)['checks_passed']


def test_wrong_pixel_format_is_rejected(tmp_path):
    with pytest.raises(VerificationError, match='yuv420p'):
        verify_mp4(render(tmp_path / 'yuv444.mp4', pix='yuv444p'))


def test_stray_data_or_subtitle_track_is_rejected(tmp_path):
    srt = tmp_path / 's.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:02,000\nhello\n')
    clip = render(tmp_path / 'plain.mp4')
    withsub = tmp_path / 'with_subtitle.mp4'
    run_process(['ffmpeg', '-v', 'error', '-y', '-i', str(clip), '-i', str(srt), '-c', 'copy', '-c:s', 'mov_text', str(withsub)])
    with pytest.raises(VerificationError, match='stray'):
        verify_mp4(withsub)


def test_truncated_or_empty_file_is_rejected(tmp_path):
    empty = tmp_path / 'empty.mp4'
    empty.write_bytes(b'')
    with pytest.raises(VerificationError, match='missing or empty'):
        verify_mp4(empty)
    clip = render(tmp_path / 'full.mp4')
    broken = tmp_path / 'broken.mp4'
    broken.write_bytes(clip.read_bytes()[:30_000])
    with pytest.raises(VerificationError):
        verify_mp4(broken)


def test_expected_duration_mismatch_is_rejected(tmp_path):
    with pytest.raises(VerificationError, match='planned'):
        verify_mp4(render(tmp_path / 'short.mp4', seconds=11), expected_duration=20)


def test_store_gate_refuses_nonconforming_media(isolated_app, tmp_path):
    from app.storage.manager import StorageManager
    import app.storage.manager as manager
    bad = render(tmp_path / 'wrong_size.mp4', size='640x360', seconds=12)
    with pytest.raises(VerificationError):
        StorageManager.move_final_clip(bad, 'ranking', 'clip_bad')
    assert not (manager.RANKING_OUTPUT_DIR / 'clip_bad.mp4').exists()
    good = render(tmp_path / 'good.mp4', seconds=12)
    stored = StorageManager.move_final_clip(good, 'ranking', 'clip_good')
    assert stored.is_file() and not stored.with_suffix('.partial').exists()

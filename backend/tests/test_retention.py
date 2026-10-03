import pytest
from app.core.runtime import run_process
from app.media import retention


@pytest.mark.parametrize('text', ['Hey guys, welcome back to the channel', 'Welcome back everyone', "Today we're going to rank saves",
                                  'In this video I show you parkour', 'Before we start, smash like', 'Hello everyone!'])
def test_filler_openings_are_rejected(text):
    with pytest.raises(ValueError, match='filler'):
        retention.validate_opening(text)


@pytest.mark.parametrize('text', ['Keep your eyes on his shoes', 'He should not have made that jump', 'Number five already looks impossible',
                                  "Today's winner never touched the ground", '', 'Watch the left foot'])
def test_real_hooks_pass(text):
    assert retention.validate_opening(text) == text


def clip(path, *, tone_seconds, total):
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size=360x640:rate=15:duration={total}',
           '-f', 'lavfi', '-i', f'sine=frequency=440:duration={tone_seconds}:sample_rate=48000',
           '-filter_complex', f'[1:a]apad=whole_dur={total}[a]', '-map', '0:v', '-map', '[a]', '-t', str(total),
           '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)]
    run_process(cmd)
    return path


def test_dead_air_in_the_finished_audio_fails(tmp_path):
    video = clip(tmp_path / 'dead.mp4', tone_seconds=2, total=12)
    with pytest.raises(ValueError, match='dead air'):
        retention.review(video, 12, [{'duration': 12, 'narration': 'Watch closely'}])


def test_continuous_audio_passes_and_reports(tmp_path):
    video = clip(tmp_path / 'live.mp4', tone_seconds=12, total=12)
    report = retention.review(video, 12, [{'duration': 12, 'narration': 'Watch closely', 'speech': {'words': [{'word': 'Watch', 'start': .1, 'end': .4}]}}])
    assert report['passed'] and report['first_speech_seconds'] == pytest.approx(.1) and not report['dead_air']


def test_late_speech_warns_but_does_not_fail(tmp_path):
    video = clip(tmp_path / 'late.mp4', tone_seconds=12, total=12)
    report = retention.review(video, 12, [{'duration': 12, 'narration': 'Look here', 'speech': {'words': [{'word': 'Look', 'start': 3.2, 'end': 3.5}]}}])
    assert report['passed'] and any('does not begin until' in w for w in report['warnings'])


def test_filler_opening_fails_the_review(tmp_path):
    video = clip(tmp_path / 'filler.mp4', tone_seconds=12, total=12)
    with pytest.raises(ValueError, match='filler'):
        retention.review(video, 12, [{'duration': 12, 'narration': 'Hey guys welcome back to my channel'}])


def test_overlong_single_beat_in_a_countdown_fails(tmp_path):
    video = clip(tmp_path / 'long.mp4', tone_seconds=30, total=30)
    with pytest.raises(ValueError, match='beat runs'):
        retention.review(video, 30, [{'duration': 25, 'narration': 'Number five'}, {'duration': 5, 'narration': 'Number four'}])

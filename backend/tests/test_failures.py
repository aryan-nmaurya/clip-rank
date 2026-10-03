"""Failures say WHAT failed, WHY, whether to retry and what happens next - and never leak secrets."""
import logging
import pytest
from app.core import database
from app.core.failures import ProcessError, RedactingFilter, classify, redact
from app.core.runtime import run_process


def test_secrets_are_redacted_everywhere():
    text = ('GET https://x/?key=AIzaSyA1234567890123456789012345678 Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123 '
            'refresh_token="1//0gabcdefghijklmnop" client_secret: GOCSPX-abcdefghijk password=hunter22')
    cleaned = redact(text)
    for secret in ('AIzaSy', 'abcdefghijklmnopqrstuvwxyz0123', '1//0gabcdefghijklmnop', 'GOCSPX', 'hunter22'):
        assert secret not in cleaned


def test_log_records_are_redacted_by_the_filter():
    record = logging.LogRecord('t', logging.ERROR, __file__, 1, 'failed with api_key=%s', ('SUPERSECRETVALUE123',), None)
    RedactingFilter().filter(record)
    assert 'SUPERSECRETVALUE123' not in record.getMessage()


def test_process_failure_keeps_exit_code_and_stderr(tmp_path):
    with pytest.raises(ProcessError) as caught:
        run_process(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp_path / 'missing.mp4'), str(tmp_path / 'out.mp4')])
    assert caught.value.tool == 'ffmpeg' and caught.value.returncode != 0 and 'missing.mp4' in caught.value.stderr
    assert isinstance(caught.value, RuntimeError)  # existing handlers keep working


@pytest.mark.parametrize('error,stage,code,retryable', [
    (ProcessError('ffmpeg', 1, 'Stream map 0:a:0 matches no streams'), 'RENDERING', 'RENDER_FAILED', True),
    (ValueError('Only 1 unused individual clips passed screening; two Top 5 Shorts need 10'), 'ANALYZING', 'NOT_ENOUGH_SOURCES', True),
    (RuntimeError("Couldn't download this video. Sign in to confirm you're not a bot"), 'INGESTING', 'SIGN_IN_REQUIRED', False),
    (OSError(28, 'No space left on device'), 'RENDERING', 'DISK_FULL', True),
    (ValueError('Production QC rejected the Short after three attempts'), 'QC', 'QC_REJECTED', True),
    (ValueError('Gemini is unavailable. Configure it in Settings'), 'ANALYZING', 'VISION_UNAVAILABLE', True),
    (KeyError('weird'), 'EDITING', 'UNEXPECTED', True),
])
def test_classification(error, stage, code, retryable):
    failure = classify(error, stage)
    assert (failure.code, failure.retryable, failure.stage) == (code, retryable, stage)
    assert failure.what and failure.next_step and failure.why


def test_job_failure_is_recorded_once_from_the_stage_it_died_in(isolated_app):
    database.create_project('p1', 'ranking', 'T', {})
    database.create_job('j1', 'p1')
    database.update_job('j1', status='RENDERING', current_stage='Mastering audio')
    first = database.record_job_failure('j1', ProcessError('ffmpeg', 1, 'Stream map failed key=AIzaSyA1234567890123456789012345678'))
    assert first['status'] == 'FAILED' and first['failure']['stage'] == 'RENDERING'
    assert first['failure']['stage_detail'] == 'Mastering audio'
    assert 'AIzaSy' not in str(first) and 'AIzaSy' not in first['detailed_error']
    assert 'FFmpeg could not render' in first['error_message']
    # The job engine's safety-net call must not overwrite the real stage with 'FAILED'.
    again = database.record_job_failure('j1', RuntimeError('wrapper'))
    assert again['failure'] == first['failure']


def test_api_exposes_failure_to_the_client(isolated_app):
    from fastapi.testclient import TestClient
    from app.api.server import app
    database.create_project('p2', 'viral', 'T', {})
    database.create_job('j2', 'p2')
    database.record_job_failure('j2', ValueError('Only 1 unused individual clips passed screening'))
    body = TestClient(app).get('/api/jobs/j2').json()
    assert body['failure']['code'] == 'NOT_ENOUGH_SOURCES' and body['failure']['retryable'] is True

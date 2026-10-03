import pytest
from app.core import database
from app.core.states import ALL_STATES, can_transition, check_transition, IllegalTransition


def test_terminal_states_are_absorbing():
    for terminal in ('COMPLETED', 'FAILED', 'CANCELLED'):
        assert not can_transition(terminal, 'RENDERING')
        assert can_transition(terminal, terminal)
        with pytest.raises(IllegalTransition):
            check_transition(terminal, 'QUEUED')


def test_running_jobs_may_repair_and_repeat_stages():
    assert can_transition('QC', 'REPAIRING') and can_transition('REPAIRING', 'RENDERING') and can_transition('RENDERING', 'QC')
    assert not can_transition('RENDERING', 'NOT_A_STATE')
    assert {'WAITING_TO_PUBLISH', 'UPLOADING', 'PUBLISHED', 'ANALYTICS_PENDING', 'PREPROCESSING'} <= ALL_STATES


def test_late_progress_cannot_resurrect_a_cancelled_job(isolated_app):
    database.create_project('p', 'ranking', 't', {})
    database.create_job('j', 'p')
    database.update_job('j', status='RENDERING', progress=60)
    database.update_job('j', status='CANCELLED', current_stage='Cancelled')
    late = database.update_job('j', status='RENDERING', progress=75, current_stage='Mastering')
    assert late['status'] == 'CANCELLED' and late['progress'] == 60 and late['current_stage'] == 'Cancelled'


def test_failure_record_survives_a_late_progress_write(isolated_app):
    database.create_project('p', 'ranking', 't', {})
    database.create_job('j', 'p')
    database.update_job('j', status='QC')
    database.record_job_failure('j', ValueError('Production QC rejected the Short'))
    database.update_job('j', status='RENDERING')
    job = database.get_job('j')
    assert job['status'] == 'FAILED' and job['failure']['stage'] == 'QC'

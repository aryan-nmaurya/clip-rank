"""Job lifecycle vocabulary and the transition rules every writer must respect.

The pipelines move through stages in a mostly forward order, repeating RENDERING/QC/
REPAIRING while a Short is repaired. What must never happen is a *terminal* job being
dragged back into a running state by a late progress write (for example from a task
that was cancelled a moment earlier).
"""
from typing import Optional

PIPELINE = [
    "CREATED", "QUEUED", "PLANNING", "DISCOVERING", "INGESTING", "ACQUIRING", "PREPROCESSING", "TRANSCRIBING",
    "ANALYZING", "DETECTING_MOMENTS", "DEDUPLICATING", "SCORING", "RANKING", "SCRIPTING", "VOICE", "GENERATING_VOICE",
    "EDITING", "RENDERING", "QC", "REPAIRING", "CLEANING", "WAITING_TO_PUBLISH", "UPLOADING", "PUBLISHED",
    "ANALYTICS_PENDING",
]
TERMINAL = {"COMPLETED", "COMPLETE", "FAILED", "CANCELLED"}
ALL_STATES = set(PIPELINE) | TERMINAL


class IllegalTransition(ValueError):
    pass


def is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL


def can_transition(old: Optional[str], new: Optional[str]) -> bool:
    """Terminal states are absorbing; everything else may move to any known state."""
    if new is None or old is None or old == new:
        return True
    if new not in ALL_STATES:
        return False
    return old not in TERMINAL


def check_transition(old: Optional[str], new: Optional[str]) -> None:
    if not can_transition(old, new):
        raise IllegalTransition(
            f"A {old} job cannot become {new}." if old in TERMINAL else f"Unknown job state {new!r}.")

"""How demanding ClipRank's AI judgment is, for Viral Discovery and for production.

Objective checks never change with this setting: a valid 1080x1920 H.264/AAC file, no black or frozen
sections, clean audio level, narration present and audible, no graphic injury. What 'relaxed' loosens is
how sure the AI must be and how many independent re-reviews a clip must survive, so more footage gets
through when the strict bar finds nothing. 'strict' is the original behaviour.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    name: str
    min_confidence: float        # AI certainty required for a verdict to count
    clarity_min: int             # minimum clarity rating (discovery)
    require_one_second: bool     # must the model say the hook lands within about a second? (discovery)
    second_review: bool          # run the extra independent blind re-review of the exact cut (discovery)
    lenient_labels: bool         # trim over-long labels/commentary instead of discarding the moment
    lenient_source_check: bool   # when unsure whether a source is a ranking/compilation, keep it
    dimension_min: int           # every key rating must reach this to be "ready to produce" (discovery)
    average_min: int             # ...and the average of the key ratings must reach this
    # production judgment
    final_critical_only: bool    # final-video review fails only on the critical checks, not on soft style checks
    reason_min: int              # how detailed the reviewer's written evidence must be
    visible_event_min: int       # ...and each per-segment observation
    allow_uncertain_outcome: bool  # blind review may call an outcome "uncertain" when the topic is not a fail
    check_label_match: bool      # second review must also confirm label/commentary wording


LEVELS = {
    'strict': Level('strict', .85, 75, True, True, False, False, 80, 0, False, 40, 20, False, True),
    'balanced': Level('balanced', .75, 65, True, False, True, True, 65, 72, True, 30, 15, False, True),
    'relaxed': Level('relaxed', .65, 55, False, False, True, True, 50, 65, True, 25, 10, True, False),
}
DEFAULT = 'relaxed'
CRITICAL_FINAL_CHECKS = ('topic_matches', 'payoffs_complete', 'rank_order_correct', 'captions_readable',
                         'framing_safe', 'production_finished', 'no_graphic_injury')


def _profile_value(field):
    try:
        from app.studio import store
        return getattr(store.profile(), field, DEFAULT)
    except Exception:   # studio tables not initialised (bare unit tests): behave as the original strict system
        return 'strict'


def level(name=None) -> Level:
    """Discovery strictness (profile setting)."""
    return LEVELS.get(name or _profile_value('discovery_strictness'), LEVELS[DEFAULT])


def production_level(name=None) -> Level:
    """Production strictness (profile setting)."""
    return LEVELS.get(name or _profile_value('production_strictness'), LEVELS[DEFAULT])

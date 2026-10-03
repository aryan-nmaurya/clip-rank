import typing
from app.studio.models import ChannelProfile
from app.studio.production import EDGE_VOICES
from app.tts.local_kokoro import PROFILES as KOKORO
from app.tts.pocket import PROFILES as POCKET, VOICES as POCKET_VOICES


def test_every_profile_is_defined_for_every_engine():
    declared = set(typing.get_args(ChannelProfile.model_fields['voice_profile'].annotation))
    assert declared == set(POCKET) == set(KOKORO) == set(EDGE_VOICES)
    assert {'Fast Entertainment', 'Cinematic', 'Suspense', 'Playful', 'Neutral'} <= declared


def test_profiles_do_not_all_sound_alike():
    assert set(POCKET.values()) <= set(POCKET_VOICES)
    assert POCKET['Fast Entertainment'] != POCKET['Suspense']          # energetic vs. narrator
    assert len(set(POCKET.values())) >= 3 and len(set(EDGE_VOICES.values())) >= 3

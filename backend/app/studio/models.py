from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator

PILLARS = ('insane_sports', 'parkour_freerunning', 'human_skills', 'epic_saves', 'physical_fails',
           'trick_shots', 'near_misses', 'crazy_stunts', 'unexpected_recoveries', 'satisfying_moments')
LEGACY_PILLARS = ('ai_tools', 'robotics', 'engineering', 'future_technology', 'automation')
CHANNEL_NAME='EXTREME, UNBELIEVABLE & FUNNY MOMENTS'
CHANNEL_PROMISE='Something surprising, impressive, funny, intense, or unbelievable in every Short.'


class SourceRights(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_url: str = Field(pattern=r'^https://', max_length=2000)
    creator: str = Field(min_length=1,max_length=200)
    license: Literal['permission','owned','CC0','CC BY 4.0'] = 'permission'
    license_evidence_url: str = Field(pattern=r'^https://',max_length=2000)
    attribution: str = Field(min_length=1,max_length=500)

    def asset(self):
        return {**self.model_dump(),'source_type':'third_party','rights_status':'commercial_use_permitted',
                'attribution_required':self.license=='CC BY 4.0','fetched_at':now()}


def now():
    return datetime.now(timezone.utc).isoformat()


class Affiliate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product: str = Field(min_length=2, max_length=100)
    url: str = Field(pattern=r'^https://', max_length=1000)
    disclosure: str = Field(default='Affiliate link: I may earn a commission.', min_length=10, max_length=300)


class ChannelProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default=CHANNEL_NAME, min_length=2, max_length=100)
    positioning: str = Field(default=CHANNEL_PROMISE, max_length=300)
    pillars: list[Literal['insane_sports','parkour_freerunning','human_skills','epic_saves','physical_fails',
        'trick_shots','near_misses','crazy_stunts','unexpected_recoveries','satisfying_moments',
        'ai_tools','robotics','engineering','future_technology','automation']] = Field(default_factory=lambda: list(PILLARS), min_length=1)
    timezone: str = 'Asia/Kolkata'
    enabled: bool = False
    auto_publish: bool = False
    production_target: Literal[3] = 3
    publication_limit: int = Field(default=2, ge=0, le=3)
    publishing_hours: list[int] = Field(default_factory=lambda: [12, 19], min_length=2, max_length=3)
    quality_threshold: int = Field(default=80, ge=75, le=100)
    exceptional_threshold: int = Field(default=92, ge=90, le=100)
    ai_mode: Literal['auto', 'local', 'gemini'] = 'auto'
    voice_profile: Literal['Curious','Energetic','Tech Curious', 'Tech Energetic', 'Documentary', 'Fast Explainer'] = 'Energetic'
    tts_engine: Literal['pocket', 'kokoro', 'edge'] = 'pocket'
    privacy: Literal['private', 'unlisted', 'public'] = 'public'
    made_for_kids: bool = False
    retain_final_days: int = Field(default=30, ge=1, le=365)
    affiliates: list[Affiliate] = Field(default_factory=list, max_length=20)
    source_rights: list[SourceRights] = Field(default_factory=list,max_length=100)
    rights_policy: Literal['user_managed','documented_permission'] = 'user_managed'

    @field_validator('timezone')
    @classmethod
    def timezone_exists(cls, value):
        try: ZoneInfo(value)
        except ZoneInfoNotFoundError as exc: raise ValueError('Choose a valid IANA timezone.') from exc
        return value

    @field_validator('publishing_hours')
    @classmethod
    def hours_valid(cls, value):
        if any(h < 0 or h > 23 for h in value) or len(set(value)) != len(value):
            raise ValueError('Use distinct local publishing hours from 0 to 23.')
        return sorted(value)


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(default=30, ge=10, le=30)


class ProduceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opportunity_id: str = Field(pattern=r'^[a-f0-9]{24}$')
    format: Literal['auto','viral_clip','ranking','commentary','explainer'] = 'auto'

"""Untrusted movie-analysis output, validated before any media command."""
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

DIMENSIONS=('hook_strength','visual_payoff','dialogue_strength','performance_strength','emotional_impact',
    'surprise','standalone_clarity','context_requirement','rewatchability','caption_potential',
    'commentary_potential','aesthetic_potential','music_fit','pacing','technical_quality','audience_fit',
    'dialogue_clarity','dialogue_importance','audio_quality','interruption_cost')
MOODS=('dominance','action','epic','style','dark','emotional','funny','dreamy')

class MovieReviewObservation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    time:float=Field(strict=True,ge=0,allow_inf_nan=False)
    visible_event:str=Field(min_length=20,max_length=1000)

class MovieObservedVideo(BaseModel):
    model_config=ConfigDict(extra='forbid')
    actual_scene_summary:str=Field(min_length=40,max_length=2000)
    observations:list[MovieReviewObservation]=Field(min_length=1,max_length=30)
    narration_text:str=Field(max_length=2000)
    original_dialogue:str=Field(max_length=2000)
    confidence:float=Field(strict=True,ge=0,le=1,allow_inf_nan=False)

class MovieFinalReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scene_matches:StrictBool
    hook_honest:StrictBool
    payoff_complete:StrictBool
    framing_safe:StrictBool
    captions_readable:StrictBool
    audio_finished:StrictBool
    format_correct:StrictBool
    dialogue_preserved:StrictBool
    commentary_grounded:StrictBool
    no_spoilers:StrictBool
    production_finished:StrictBool
    confidence:float=Field(strict=True,ge=0,le=1,allow_inf_nan=False)
    reason:str=Field(min_length=40,max_length=4000)
    observations:list[MovieReviewObservation]=Field(min_length=1,max_length=30)
    issues:list[str]=Field(max_length=30)
    repair:str=Field(max_length=4000)

class ProtectedDialogueRegion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start:float=Field(ge=0)
    end:float=Field(gt=0)
    importance:int=Field(default=100,ge=0,le=100)
    protected:Literal[True]=True
    @model_validator(mode='after')
    def ordered(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.start>=self.end:
            raise ValueError('Invalid protected dialogue bounds.')
        return self

class MovieMomentAnalysis(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start:float=Field(ge=0)
    end:float=Field(gt=0)
    payoff_timestamp:float=Field(ge=0)
    title:str=Field(min_length=2,max_length=100)
    description:str=Field(min_length=30,max_length=1200)
    reason:str=Field(min_length=25,max_length=1200)
    header_text:str=Field(max_length=80)
    commentary:str=Field(max_length=300)
    commentary_value:str=Field(max_length=600)
    mood:Literal['dominance','action','epic','style','dark','emotional','funny','dreamy']
    complete_moment:StrictBool
    clean_source:StrictBool
    contains_watermark:StrictBool
    confidence:float=Field(ge=0,le=1)
    scores:dict[str,float]
    @field_validator('scores',mode='before')
    @classmethod
    def valid_scores(cls,value):
        if not isinstance(value,dict) or set(value)!=set(DIMENSIONS):
            raise ValueError('Every movie scoring dimension is required.')
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=100 for v in value.values()):
            raise ValueError('Movie scores must be finite numbers from 0 to 100.')
        return value
    @model_validator(mode='after')
    def valid_moment(self):
        if not all(math.isfinite(t) for t in (self.start,self.end,self.payoff_timestamp,self.confidence)):
            raise ValueError('Movie times/confidence must be finite.')
        if not self.start<self.payoff_timestamp<self.end or not 6<=self.end-self.start<=60:
            raise ValueError('A complete movie moment must be 6–60 seconds with its payoff inside the cut.')
        if len(self.header_text.split())>7 or (self.header_text and len(self.header_text.split())<2):
            raise ValueError('A movie header must be empty or 2–7 words.')
        if self.commentary and not 5<=len(self.commentary.split())<=20:
            raise ValueError('Movie commentary must contain 5–20 words.')
        return self

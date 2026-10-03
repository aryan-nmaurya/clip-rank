"""Auto Shorts: start a run, watch it, cancel it."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.youtube_routes import local_access, local_mutation
from app.engines.auto_shorts import AutoShorts, NICHES, PLATFORMS

router = APIRouter(prefix="/auto-shorts", tags=["Auto Shorts"], dependencies=[Depends(local_access)])


class StartRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=5)
    platforms: List[Literal["youtube", "dailymotion", "reddit", "vimeo"]] = Field(default_factory=lambda: ["youtube", "dailymotion", "reddit"], min_length=1)
    mode: Literal["viral", "emotional", "sad_visual"] = "viral"
    niche: Literal["extreme", "funny", "skills", "general"] = "extreme"
    topic: Optional[str] = Field(default=None, max_length=120)
    voice: Optional[str] = None
    ai_provider: Literal["auto", "gemini", "groq", "nvidia", "openai", "local"] = "auto"


@router.get("/options")
def options():
    return {"platforms": list(PLATFORMS), "niches": [n for n in NICHES if n not in ("emotional", "sad_visual")], "modes": ["viral", "emotional", "sad_visual"]}


@router.post("", dependencies=[Depends(local_mutation)])
async def start(payload: StartRequest):
    if payload.voice:
        from app.tts.voice_engine import TTSEngine
        try:
            payload.voice = TTSEngine.resolve_voice(payload.voice)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return AutoShorts.start(payload.count, payload.platforms, payload.niche, payload.topic, payload.voice, payload.ai_provider, payload.mode)


@router.get("/status")
def status():
    return AutoShorts.status()


@router.post("/cancel", dependencies=[Depends(local_mutation)])
async def cancel():
    return await AutoShorts.cancel()

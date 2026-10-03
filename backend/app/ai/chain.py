"""AUTO mode: several providers behind one interface, moving on when one runs out of quota."""
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.ai.base import AIProvider
from app.ai.errors import AIQuotaExceeded

logger = logging.getLogger("ai_shorts.chain")


class ChainProvider(AIProvider):
    """Try members in priority order. A member that is out of quota, or that fails to answer, is skipped for this call.

    Only when every member is spent does AIQuotaExceeded reach the job. Members that remember their own
    cool-down (Gemini keys/models, Groq, NIM) raise instantly without a network call, so skipping is free.
    """

    def __init__(self, members: List[Tuple[str, AIProvider]]):
        if not members:
            raise ValueError("A provider chain needs at least one provider.")
        self.members = members
        self.model = getattr(members[0][1], "model", None)
        self.last_member: Optional[str] = None

    @property
    def last_error(self) -> Optional[str]:
        return next((getattr(p, "last_error", None) for _, p in reversed(self.members) if getattr(p, "last_error", None)), None)

    @property
    def names(self) -> List[str]:
        return [name for name, _ in self.members]

    async def is_available(self) -> bool:
        return any([await p.is_available() for _, p in self.members])

    async def _try(self, method: str, *args, **kwargs):
        quota: Optional[AIQuotaExceeded] = None
        for name, provider in self.members:
            function = getattr(provider, method, None)
            if function is None:
                continue
            try:
                result = await function(*args, **kwargs)
            except AIQuotaExceeded as exc:
                quota = exc
                logger.warning("%s is out of quota; trying the next provider.", name)
                continue
            if result is None and method != "analyze_video":
                continue     # that provider could not answer (transient error): give the next one a chance
            self.last_member = name
            return result
        if quota:
            raise AIQuotaExceeded("all configured AI providers", None,
                                  "Tried: " + ", ".join(self.names) + ". Add another key/provider or wait for a reset.") from None
        return None

    async def generate_text(self, prompt, system_prompt=None, options=None):
        return await self._try("generate_text", prompt, system_prompt, options)

    async def analyze_images(self, image_paths, prompt, options=None):
        return await self._try("analyze_images", image_paths, prompt, options)

    async def generate_structured(self, prompt, schema_desc, fallback_fn=None, options=None):
        raw = await self.generate_text(f"{prompt}\n\nRespond ONLY with valid JSON conforming to this schema:\n{schema_desc}", options={"json": True, **(options or {})})
        import json
        try:
            return json.loads((raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        except ValueError:
            return fallback_fn() if fallback_fn else {}

    def _video_members(self):
        for _, provider in self.members:
            if hasattr(provider, "analyze_video") and not _spent(provider):
                yield provider

    def __getattr__(self, name):
        # Whole-video review is offered only while a video-capable member (Gemini) still has quota,
        # so pipelines that check hasattr() choose the frame-based review once it is gone.
        if name == "analyze_video":
            if any(True for _ in self._video_members()):
                return self._analyze_video
        raise AttributeError(name)

    async def _analyze_video(self, video_path, prompt):
        last: Optional[AIQuotaExceeded] = None
        for provider in self._video_members():
            try:
                return await provider.analyze_video(video_path, prompt)
            except AIQuotaExceeded as exc:
                last = exc
        raise last or AIQuotaExceeded("gemini", None, "No video-capable provider has quota left.")


def _spent(provider) -> bool:
    status = getattr(provider, "key_status", None)
    return bool(status and not status()["usable_models"])

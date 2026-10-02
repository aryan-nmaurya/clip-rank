import logging
import json
import math
from typing import Dict, Any, List, Optional
from app.ai.gemini import GeminiProvider
from app.ai.openai_provider import OpenAIProvider
from app.ai.local_provider import LocalProvider

logger = logging.getLogger("ai_shorts.ai_router")

class AIRouter:
    @staticmethod
    def get_providers(settings: Dict[str, Any]):
        gemini = GeminiProvider(
            api_key=settings.get("gemini_api_key"),
            model=settings.get("gemini_model", "gemini-2.5-flash")
        )
        openai = OpenAIProvider(
            api_key=settings.get("openai_api_key"),
            model=settings.get("openai_model", "gpt-4o-mini")
        )
        local = LocalProvider(
            endpoint=settings.get("local_endpoint", "http://localhost:11434"),
            model=settings.get("local_model", "qwen2.5:latest")
        )
        return gemini, openai, local

    @classmethod
    async def get_active_provider(cls, settings: Dict[str, Any], preferred: str = None, task: str = "general"):
        preferred = (preferred or settings.get("ai_provider", "auto")).lower()
        gemini, openai, local = cls.get_providers(settings)

        selected = {"gemini": gemini, "openai": openai, "local": local}.get(preferred)
        if selected:
            if await selected.is_available():
                return selected, preferred
            raise ValueError(f"{preferred.title()} is unavailable. Configure it in Settings or choose Auto for local visual analysis.")

        # Auto mode:
        # Check cloud first for high quality multimodal/creative tasks if configured, else local
        if await gemini.is_available():
            return gemini, "gemini"
        if await openai.is_available():
            return openai, "openai"
        if await local.is_available():
            return local, "local"

        return None, "fallback"

    @classmethod
    async def get_status(cls, settings: Dict[str, Any]) -> Dict[str, Any]:
        gemini, openai, local = cls.get_providers(settings)
        gemini_ok = await gemini.is_available()
        openai_ok = await openai.is_available()
        local_ok = await local.is_available()

        preferred = settings.get("ai_provider", "auto")

        if preferred == "gemini":
            status_text = "● AI Ready · Google AI Studio" if gemini_ok else "● Google AI Studio (Key required in Settings)"
            is_ready = gemini_ok
            active_model = settings.get("gemini_model", "gemini-2.5-flash")
        elif preferred == "openai":
            status_text = "● AI Ready · OpenAI" if openai_ok else "● OpenAI (Key required in Settings)"
            is_ready = openai_ok
            active_model = settings.get("openai_model", "gpt-4o-mini")
        elif preferred == "local":
            status_text = "● AI Ready · Local (Ollama)" if local_ok else "● Local AI unavailable"
            is_ready = local_ok
            active_model = settings.get("local_model", "qwen2.5:latest")
        else: # auto
            if gemini_ok:
                status_text = "● AI Ready · Google AI Studio"
                is_ready = True
                active_model = settings.get("gemini_model", "gemini-2.5-flash")
            elif openai_ok:
                status_text = "● AI Ready · OpenAI"
                is_ready = True
                active_model = settings.get("openai_model", "gpt-4o-mini")
            elif local_ok:
                status_text = "● AI Ready · Local (Ollama)"
                is_ready = True
                active_model = settings.get("local_model", "qwen2.5:latest")
            else:
                status_text = "● Visual analysis · No AI model configured"
                is_ready = True
                active_model = "Measured motion and clarity"

        return {
            "status_text": status_text,
            "provider": preferred,
            "is_ready": is_ready,
            "local_available": local_ok,
            "gemini_configured": gemini_ok,
            "openai_configured": openai_ok,
            "active_model": active_model,
        }

    @classmethod
    async def evaluate_moments(cls, moments, image_path, settings, transcript=None, topic=None):
        """AI sees sampled source frames and real timed speech, never an invented context."""
        provider, name = await cls.get_active_provider(settings)
        if not provider:
            return moments, "visual metrics"
        evidence = []
        for i, moment in enumerate(moments):
            sentences = [s for s in (transcript or [])
                         if s["end"] > moment["start"] and s["start"] < moment["end"]]
            evidence.append({"id": i, "start": moment["start"], "end": moment["end"],
                             "source_title": moment.get("title"), "motion": moment.get("motion"),
                             "clarity": moment.get("clarity"), "speech": sentences})
        prompt = (
            "Evaluate the actual sampled video frames in the attached contact sheet. Each row has an ID and time range. "
            "Use only visible actions and supplied real speech. Do not claim viral popularity, invent dialogue or events. "
            "Flag already_ranked=true for footage that already contains a ranking list or countdown. Score retention potential (0-100), clarity and payoff, and give a short factual 2-5 word label and a specific reason. "
            "These scores are estimates, not predictions of views. Keep the provided time ranges. "
            + (f"Ranking topic: {topic}. Also give topic_relevance (0-1). " if topic else "Select standalone highlights. ")
            + "Return only JSON: {\"moments\": [{\"id\": 0, \"score\": 75, \"label\": \"Short factual label\", \"reason\": \"Observed reason\", \"topic_relevance\": 0.9}]}\n"
            + json.dumps(evidence, ensure_ascii=False)[:16000]
        )
        raw = await provider.analyze_images([image_path], prompt)
        if not raw:
            if settings.get("ai_provider") != "auto":
                raise ValueError(f"{name.title()} could not analyze the source frames. Check the configured model supports images and retry, or choose Auto.")
            return moments, "visual metrics (AI unavailable)"
        try:
            clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            response = json.loads(clean)
            revised = []
            seen = set()
            for item in response.get("moments", []):
                idx = int(item["id"])
                if idx in seen or idx < 0 or idx >= len(moments):
                    continue
                seen.add(idx)
                score = float(item["score"])
                if not math.isfinite(score):
                    continue
                revised.append({**moments[idx], "score": max(0, min(100, round(score))),
                                "label": str(item.get("label") or moments[idx]["title"])[:80],
                                "reason": str(item.get("reason") or moments[idx]["reason"])[:600],
                                "topic_relevance": max(0, min(1, float(item.get("topic_relevance", 1)))),
                                "already_ranked": bool(item.get("already_ranked", False)),
                                "analysis_basis": f"{name} frame analysis"})
            if revised:
                revised.extend(m for i, m in enumerate(moments) if i not in seen)
                return sorted(revised, key=lambda m: m["score"], reverse=True), f"{name} frame analysis"
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        if settings.get("ai_provider") != "auto":
            raise ValueError(f"{name.title()} returned invalid frame analysis. Retry or choose Auto.")
        return moments, "visual metrics (AI response invalid)"

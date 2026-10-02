import logging
import json
import math
from typing import Dict, Any, List, Optional
from app.ai.gemini import GeminiProvider
from app.ai.openai_provider import OpenAIProvider
from app.ai.local_provider import LocalProvider

logger = logging.getLogger("ai_shorts.ai_router")

class AIRouter:
    @classmethod
    async def require_ranking_provider(cls, settings):
        try:
            provider, name = await cls.get_active_provider(settings)
        except ValueError as exc:
            raise ValueError("The selected ranking vision engine is not connected. Configure your Gemini key or installed Ollama vision model in Settings.") from exc
        if not provider:
            raise ValueError("Ranking needs a connected vision model to verify your topic, action and clip labels. Add a Gemini API key or connect an installed Ollama vision model in Settings. Motion scores cannot verify a topic.")
        return provider, name

    @staticmethod
    def get_providers(settings: Dict[str, Any]):
        gemini = GeminiProvider(
            api_key=settings.get("gemini_api_key"),
            model=settings.get("gemini_model", "gemini-3.1-flash-lite")
        )
        openai = OpenAIProvider(
            api_key=settings.get("openai_api_key"),
            model=settings.get("openai_model", "gpt-4o-mini")
        )
        local = LocalProvider(
            endpoint=settings.get("local_endpoint", "http://localhost:11434"),
            model=settings.get("local_model", "qwen3-vl:4b")
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

        # Automatic mode spends local compute first. Cloud is a bounded fallback
        # in the studio, and a connection fallback for the manual production modes.
        if await local.is_available():
            return local, "local"
        if await gemini.is_available():
            return gemini, "gemini"
        if await openai.is_available():
            return openai, "openai"

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
            active_model = settings.get("gemini_model", "gemini-3.1-flash-lite")
        elif preferred == "openai":
            status_text = "● AI Ready · OpenAI" if openai_ok else "● OpenAI (Key required in Settings)"
            is_ready = openai_ok
            active_model = settings.get("openai_model", "gpt-4o-mini")
        elif preferred == "local":
            status_text = "● AI Ready · Local (Ollama)" if local_ok else "● Local AI unavailable"
            is_ready = local_ok
            active_model = settings.get("local_model", "qwen3-vl:4b")
        else: # auto
            if local_ok:
                status_text = "● AI Ready · Local (Ollama)"
                is_ready = True
                active_model = settings.get("local_model", "qwen3-vl:4b")
            elif gemini_ok:
                status_text = "● AI Ready · Google AI Studio"
                is_ready = True
                active_model = settings.get("gemini_model", "gemini-3.1-flash-lite")
            elif openai_ok:
                status_text = "● AI Ready · OpenAI"
                is_ready = True
                active_model = settings.get("openai_model", "gpt-4o-mini")
            else:
                status_text = "● Connect vision for production QC"
                is_ready = False
                active_model = "No production vision model"

        return {
            "status_text": status_text,
            "provider": preferred,
            "is_ready": is_ready,
            "local_available": local_ok,
            "gemini_configured": gemini_ok,
            "openai_configured": openai_ok,
            "active_model": active_model,
            "ranking_ready": (gemini_ok or openai_ok or local_ok) if preferred == "auto" else is_ready,
        }

    @classmethod
    async def screen_ranking_source(cls, image_path, metadata, settings, provider_info=None, required=False):
        provider, name = provider_info or await cls.get_active_provider(settings)
        if not provider:
            return None
        prompt = (
            "Inspect ALL source frames in this contact sheet. We need an individual raw event clip, "
            "never footage taken from an existing ranking/countdown/Top N/tier list or multi-event compilation. "
            "Reject large existing meme/editorial overlays that obscure action or make unsupported injury/death claims. "
            "A small creator watermark or necessary sports scoreboard alone is allowed and must remain. "
            "Look for embedded numbered lists, changing rank badges, countdown narration text, ranking headings, "
            "or a montage of unrelated clips. Subtitles, creator watermarks, and sports scoreboards alone are allowed. "
            "If uncertain, set suitable_raw=false. Use only visible evidence. Return only JSON with actual booleans: "
            '{"suitable_raw": true, "already_ranked": false, "compilation": false, "reason": "Observed evidence"}. '
            + json.dumps({"source_title": metadata.get("title")})
        )
        raw = await provider.analyze_images([image_path], prompt)
        try:
            clean = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(clean)
            if all(type(result.get(key)) is bool for key in ("suitable_raw", "already_ranked", "compilation")):
                return {**result, "method": f"{name} source verification"}
        except (ValueError, TypeError, AttributeError):
            pass
        if required or settings.get("ai_provider", "auto") != "auto":
            raise ValueError(f"{name.title()} could not verify that the source is an individual raw clip. Check the vision model and retry.")
        return None

    @classmethod
    async def evaluate_moments(cls, moments, image_path, settings, transcript=None, topic=None, provider_info=None):
        """AI sees sampled source frames and real timed speech, never an invented context."""
        if topic:
            from app.ai.ranking_verifier import RankingVerifier
            provider, name = provider_info or await cls.require_ranking_provider(settings)
            return await RankingVerifier.evaluate(provider, name, moments, image_path, topic), f"{name} verified topic and action"
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
            "Also give commentary: one conversational sentence of at most 12 words describing the visible action, without a rank number. "
            + (f"Ranking topic: {topic}. Also give topic_relevance (0-1). " if topic else "Select standalone highlights. ")
            + "Return only JSON: {\"moments\": [{\"id\": 0, \"score\": 75, \"label\": \"Short factual label\", \"commentary\": \"A short description of the observed action\", \"reason\": \"Observed reason\", \"already_ranked\": false, \"topic_relevance\": 0.9}]}\n"
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
                                "commentary": " ".join(str(item.get("commentary") or "").split()[:12]),
                                "topic_relevance": max(0, min(1, float(item.get("topic_relevance", 1)))),
                                "already_ranked": item.get("already_ranked", False) is not False,
                                "analysis_basis": f"{name} frame analysis"})
            if revised:
                revised.extend(m for i, m in enumerate(moments) if i not in seen)
                return sorted(revised, key=lambda m: m["score"], reverse=True), f"{name} frame analysis"
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        if settings.get("ai_provider") != "auto":
            raise ValueError(f"{name.title()} returned invalid frame analysis. Retry or choose Auto.")
        return moments, "visual metrics (AI response invalid)"

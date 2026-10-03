"""Providers that speak the OpenAI-style chat API: Groq and NVIDIA NIM.

They are text + image analysers (no native video input), so the pipelines use their frame-based
review path - the same one local vision models use. Quota behaviour matches Gemini: a spent daily
quota raises AIQuotaExceeded (so a job stops cleanly or the AUTO chain moves on); short rate limits
wait what the API asks for.
"""
import base64
import hashlib
import io
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.ai.base import AIProvider
from app.ai.errors import AIQuotaExceeded
from app.core.failures import redact

logger = logging.getLogger("ai_shorts.compat")
# provider/key -> epoch when usable again (shared by instances; settings reload builds new objects every call)
_cooling: Dict[str, float] = {}
DEFAULT_COOLDOWN = 3600
MAX_IMAGE_BYTES = 3_000_000   # Groq rejects base64 images above 4 MB
_RETRY = re.compile(r"(?:try again|retry) in\s*(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)(ms|s))?", re.I)


def retry_seconds(message: str, header: Optional[str] = None) -> Optional[float]:
    """Seconds the API asked us to wait: Retry-After header, or 'try again in 7m12.5s' / '350ms' in the body."""
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = _RETRY.search(message or "")
    if not match or not any(match.groups()):
        return None
    hours, minutes, amount, unit = match.groups()
    seconds = float(amount) if amount else 0.0
    if unit == "ms":
        seconds /= 1000
    return float(hours or 0) * 3600 + float(minutes or 0) * 60 + seconds


def strip_reasoning(text: Optional[str]) -> Optional[str]:
    """Reasoning models (Qwen3, Nemotron) may prefix their answer with <think>…</think>; callers want only the answer."""
    if text is None:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    return re.sub(r"^.*?</think>", "", text, flags=re.S | re.I).strip()


def prepare_image(path: Path, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """JPEG bytes small enough for the API, downscaled only when necessary."""
    data = Path(path).read_bytes()
    if len(data) <= max_bytes:
        return data
    from PIL import Image
    with Image.open(path) as image:
        image = image.convert("RGB")
        for width in (1600, 1280, 1024, 768):
            copy = image.copy()
            copy.thumbnail((width, width * 4))
            buffer = io.BytesIO()
            copy.save(buffer, "JPEG", quality=82)
            if buffer.tell() <= max_bytes:
                return buffer.getvalue()
    return buffer.getvalue()


def limit_images(paths: List[Path], limit: int) -> List[Path]:
    """APIs cap images per request. Extra images are stacked into montages, never silently dropped."""
    paths = [Path(p) for p in paths]
    if len(paths) <= limit:
        return paths
    from PIL import Image
    groups: List[List[Path]] = [[] for _ in range(limit)]
    for index, path in enumerate(paths):
        groups[min(limit - 1, index * limit // len(paths))].append(path)
    folder = Path(tempfile.mkdtemp(prefix="cliprank_montage_"))
    merged = []
    for number, group in enumerate(groups):
        if len(group) == 1:
            merged.append(group[0])
            continue
        opened = [Image.open(p).convert("RGB") for p in group]
        width = max(i.width for i in opened)
        sheet = Image.new("RGB", (width, sum(i.height for i in opened)), "#18181b")
        y = 0
        for image in opened:
            sheet.paste(image, ((width - image.width) // 2, y))
            y += image.height
        target = folder / f"montage_{number}_{hashlib.sha1(str(group).encode()).hexdigest()[:8]}.jpg"
        sheet.save(target, "JPEG", quality=85)
        merged.append(target)
    return merged


class OpenAICompatibleProvider(AIProvider):
    name = "openai-compatible"
    base_url = ""
    default_model = ""
    max_images = 5
    # Whether AUTO may use this provider to verify footage. Measured, not assumed: see GroqProvider.
    auto_eligible = True
    transport = None   # tests inject httpx.MockTransport
    last_error: Optional[str] = None

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = (api_key or "").strip() or None
        self.model = model or self.default_model

    async def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 5)

    def _slot(self) -> str:
        return f"{self.name}|{hashlib.sha256((self.api_key or '').encode()).hexdigest()[:16]}|{self.model}"

    async def _chat(self, messages: List[Dict[str, Any]], options: Optional[Dict[str, Any]] = None, timeout: float = 180) -> Optional[str]:
        if _cooling.get(self._slot(), 0) > time.time():
            raise AIQuotaExceeded(self.name, _cooling[self._slot()] - time.time(), "This provider is cooling down after its quota was spent.")
        body: Dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.25, "max_tokens": 4000}
        if options and options.get("json"):
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                    response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
            except httpx.HTTPError as exc:   # timeouts, resets, DNS: retry, then report the real reason
                self.last_error = f"{type(exc).__name__}: {redact(str(exc))[:200]}"
                if attempt < 2:
                    await _sleep(2 ** attempt)
                    continue
                logger.warning("%s request failed: %s", self.name, self.last_error)
                return None
            text = redact(response.text).replace(self.api_key or '\0', '[redacted]')
            if response.status_code == 200:
                data = response.json()
                self._record_usage(data)
                return strip_reasoning((data.get("choices") or [{}])[0].get("message", {}).get("content"))
            delay = retry_seconds(text, response.headers.get("retry-after"))
            lowered = text.lower()
            if response.status_code == 400 and "response_format" in body and ("response_format" in lowered or "json" in lowered):
                body.pop("response_format")      # this model has no JSON mode; the prompt already demands JSON
                continue
            if response.status_code in (402, 429):
                daily = (response.status_code == 402 or "per day" in lowered or "daily" in lowered or "credit" in lowered
                         or "insufficient" in lowered or (delay is not None and delay > 120))
                if daily:
                    self.last_error = text[:500]
                    _cooling[self._slot()] = time.time() + (delay or DEFAULT_COOLDOWN)
                    raise AIQuotaExceeded(self.name, delay, text[:200])
                if attempt < 2:
                    await _sleep(min(60, max(2 ** attempt, delay or 0)))
                    continue
            if response.status_code in (500, 502, 503, 504) and attempt < 2:
                await _sleep(2 ** attempt)
                continue
            self.last_error = f"HTTP {response.status_code}: {text[:400]}"
            logger.warning("%s request failed: %s", self.name, self.last_error)
            return None
        return None

    def _record_usage(self, data: Dict[str, Any]) -> None:
        try:
            usage = data.get("usage") or {}
            from app.business.store import record_ai_usage
            record_ai_usage(self.name, self.model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        except Exception as exc:
            logger.debug("AI usage not recorded: %s", exc)

    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + [{"role": "user", "content": prompt}]
        return await self._chat(messages, options)

    async def analyze_images(self, image_paths: List[Path], prompt: str, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        paths = [Path(p) for p in image_paths if Path(p).exists()]
        if not paths or len(paths) != len(image_paths):
            return None
        parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in limit_images(paths, self.max_images):
            encoded = base64.b64encode(prepare_image(path)).decode()
            parts.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded}})
        return await self._chat([{"role": "user", "content": parts}], options)

    async def generate_structured(self, prompt: str, schema_desc: str, fallback_fn=None, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import json
        raw = await self.generate_text(f"{prompt}\n\nRespond ONLY with valid JSON conforming to this schema:\n{schema_desc}", options={"json": True, **(options or {})})
        try:
            return json.loads((raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        except ValueError:
            return fallback_fn() if fallback_fn else {}


async def _sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1"
    default_model = "qwen/qwen3.8-27b"   # the only image-capable model Groq currently lists
    max_images = 5
    # Measured on this account: 0 of 5 trivial shape-counting images answered correctly (counts came back doubled),
    # so AUTO does not let it vote on footage. It stays selectable explicitly and testable in Settings.
    auto_eligible = False


class NvidiaNIMProvider(OpenAICompatibleProvider):
    name = "nvidia"
    base_url = "https://integrate.api.nvidia.com/v1"
    default_model = "meta/llama-3.2-90b-vision-instruct"
    max_images = 1   # NIM's Llama vision models accept one image per request; extras are stacked into it

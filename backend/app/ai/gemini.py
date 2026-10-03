import os
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.ai.base import AIProvider
from app.ai.errors import AIQuotaExceeded, is_daily_quota, retry_delay_seconds
from app.core.runtime import run_blocking

logger = logging.getLogger("ai_shorts.gemini")

# (key, model) pairs whose daily quota is spent, shared by every provider instance: slot -> epoch usable again.
_exhausted: Dict[str, float] = {}
# Models this account cannot use (not found / not supported); skipped for the rest of the process.
_unavailable: set = set()
DEFAULT_COOLDOWN = 6 * 3600
# Quotas are counted per model, so these add real capacity when the primary model's quota is spent.
DEFAULT_FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash")


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def slot(key: str, model: str) -> str:
    return fingerprint(key) + "|" + model


def parse_models(value) -> List[str]:
    """Accept a comma/space separated string or a list; keep valid-looking model ids in order."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").replace(",", " ").split()
    return [m for m in dict.fromkeys(i.strip() for i in items) if m and all(c.isalnum() or c in "-._" for c in m)]


def model_missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return getattr(exc, "code", None) == 404 or "not found" in text or "is not supported" in text or "no longer available" in text


class _SlotLimited(Exception):
    """This key/model cannot answer right now (rate limit that persisted, or the key was rejected)."""
    def __init__(self, cooldown, reason):
        super().__init__(reason)
        self.cooldown, self.reason = cooldown, reason


def key_rejected(exc: Exception) -> bool:
    text = str(exc).lower()
    return (getattr(exc, "code", None) in (401, 403) or "api key not valid" in text or "api_key_invalid" in text
            or "permission_denied" in text or "api key expired" in text or "leaked" in text)


class _ModelUnavailable(Exception):
    def __init__(self, model):
        super().__init__(model)
        self.model = model


class GeminiProvider(AIProvider):
    """One or more Google AI Studio keys and a chain of models.

    Order: for each model (primary first), try every key that still has quota on it. A spent
    (key, model) pair is remembered, so the next call goes straight to a pair that works.
    """
    last_error = None

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.1-flash-lite", extra_keys=(), fallback_models=()):
        self.api_key = api_key or os.environ.get("GOOGLE_AI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.model = model or "gemini-3.1-flash-lite"
        candidates = [self.api_key, *extra_keys]
        self.keys = list(dict.fromkeys(k.strip() for k in candidates if isinstance(k, str) and len(k.strip()) > 5))
        self.models = list(dict.fromkeys([self.model, *parse_models(fallback_models)]))
        self.last_model = self.model

    async def is_available(self) -> bool:
        return bool(self.keys)

    def live_slots(self, now: Optional[float] = None) -> List[tuple]:
        now = now or time.time()
        return [(m, k) for m in self.models if m not in _unavailable for k in self.keys
                if _exhausted.get(slot(k, m), 0) <= now]

    def live_keys(self, now: Optional[float] = None) -> List[str]:
        """Keys with quota left on the primary model (kept for status displays)."""
        return [k for m, k in self.live_slots(now) if m == self.model]

    def key_status(self) -> Dict[str, Any]:
        live = len(self.live_keys())
        return {"configured": len(self.keys), "available": live, "exhausted": len(self.keys) - live,
                "models": self.models, "usable_models": sorted({m for m, _ in self.live_slots()}, key=self.models.index)}

    def _redact(self, text: str) -> str:
        for key in self.keys:
            text = text.replace(key, '[redacted]')
        return text

    def _record_usage(self, result, model=None):
        """Real token counts from the API response feed the cost ledger. Never affects generation."""
        try:
            usage = getattr(result, 'usage_metadata', None)
            if usage:
                from app.business.store import record_ai_usage
                record_ai_usage('gemini', model or self.model, getattr(usage, 'prompt_token_count', 0) or 0,
                                getattr(usage, 'candidates_token_count', 0) or 0)
        except Exception as exc:
            logger.debug('AI usage not recorded: %s', exc)

    def _get_client(self, key: Optional[str] = None):
        key = key or self.api_key
        if not key:
            return None
        from google import genai
        from google.genai import types
        return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120000))

    async def _generate(self, contents, options=None):
        """Walk model -> key until something answers; only a fully spent chain raises the quota error."""
        slots = self.live_slots()
        if not slots:
            soonest = min((_exhausted.get(slot(k, m), 0) for m in self.models for k in self.keys), default=0) - time.time()
            raise AIQuotaExceeded('gemini', max(0, soonest) or None,
                                  f'All {len(self.keys)} key(s) x {len(self.models)} model(s) have used their quota.')
        retry_after, limited, ever_limited = None, 0, False
        for round_number in range(3):
            limited = 0
            for model, key in self.live_slots():
                try:
                    self.last_model = model
                    answer = await self._generate_with(key, contents, options, model)
                    logger.info('Gemini answered with key %d of %d on %s.', self.keys.index(key) + 1, len(self.keys), model)
                    return answer
                except AIQuotaExceeded as exc:
                    _exhausted[slot(key, model)] = time.time() + (exc.retry_after or DEFAULT_COOLDOWN)
                    retry_after = exc.retry_after or retry_after
                    logger.warning('Gemini key %d is out of daily quota on %s; trying the next key/model.', self.keys.index(key) + 1, model)
                except _SlotLimited as exc:
                    limited += 1
                    ever_limited = True
                    for affected in (self.models if exc.reason.startswith('key rejected') else [model]):
                        _exhausted[slot(key, affected)] = time.time() + exc.cooldown   # a bad key is bad on every model
                    self.last_error = exc.reason
                    logger.warning('Gemini key %d cannot answer on %s (%s); trying the next key/model.', self.keys.index(key) + 1, model, exc.reason[:120])
                except _ModelUnavailable:
                    _unavailable.add(model)
                    logger.warning('Gemini model %s is not available to this account; skipping it.', model)
            if not limited and not (ever_limited and not self.live_slots()):
                break
            # Everything left is only briefly rate-limited: wait for the soonest to recover, then go round again.
            soonest = min((_exhausted.get(slot(k, m), 0) for m in self.models if m not in _unavailable for k in self.keys), default=0) - time.time()
            if soonest > 90:
                break
            await asyncio.sleep(max(1, soonest))
        if ever_limited and not retry_after:
            return None      # rate-limited or rejected everywhere: callers report last_error, this is not a daily-quota stop
        raise AIQuotaExceeded('gemini', retry_after,
                              f'All {len(self.keys)} key(s) x {len(self.models)} model(s) have used their quota.') from None

    async def _generate_with(self, key, contents, options=None, model=None):
        from google.genai import types
        model = model or self.model
        # The SDK's optional aiohttp transport can assert under Python 3.14.
        # Its synchronous HTTP transport runs off the API loop and closes per call.
        def request():
            config = types.GenerateContentConfig(temperature=.25, max_output_tokens=6000,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
            if options and options.get('json'):
                config.response_mime_type = 'application/json'
            with self._get_client(key) as client:
                result = client.models.generate_content(model=model, contents=contents, config=config)
                self._record_usage(result, model)
                return result.text if result else None
        for attempt in range(3):
            try:
                return await run_blocking(request)
            except Exception as exc:
                if getattr(exc,'code',None)==429:
                    message=self._redact(str(exc))
                    delay=retry_delay_seconds(message)
                    if is_daily_quota(message,delay):
                        self.last_error=message[:500]
                        logger.error('Gemini daily quota exhausted: %s',message[:200])
                        raise AIQuotaExceeded('gemini',delay,message) from None
                    if attempt<2 and len(self.keys)<2:   # one key only: wait out the per-minute limit here
                        await asyncio.sleep(min(60,max(2**attempt,delay or 0)))
                        continue
                    if len(self.keys)>=2 or attempt==2:   # several keys: don't wait, let another key take the request
                        raise _SlotLimited(min(300,max(5,delay or 30)),'rate limited: '+message[:200]) from None
                if key_rejected(exc) and not model_missing(exc):
                    raise _SlotLimited(3600,'key rejected: '+self._redact(str(exc))[:200]) from None
                if model_missing(exc) and len(self.models) > 1:
                    raise _ModelUnavailable(model) from None
                if getattr(exc,'code',None) in (500,502,503,504) and attempt<2:
                    await asyncio.sleep(2**attempt)
                    continue
                self.last_error = self._redact(str(exc))[:500]
                logger.warning('Gemini request failed: %s', self.last_error)
                return None


    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        try:
            return await self._generate(prompt, options)
        except AIQuotaExceeded:
            raise
        except Exception as e:
            self.last_error = self._redact(str(e))[:500]
            logger.warning(f"Gemini generate_text failed: {self.last_error}")
            return None

    async def analyze_images(self, image_paths: List[Path], prompt: str, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        try:
            from PIL import Image
            images = [Image.open(p).copy() for p in image_paths if p.exists()]
            if not images or len(images) != len(image_paths):
                return None
            contents = [prompt] + images
            return await self._generate(contents, options)
        except AIQuotaExceeded:
            raise
        except Exception as e:
            self.last_error = self._redact(str(e))[:500]
            logger.warning(f"Gemini analyze_images failed: {self.last_error}")
            return None

    async def analyze_video(self, video_path: Path, prompt: str):
        from google.genai import types
        if not video_path.is_file() or video_path.stat().st_size > 18 * 1024 * 1024:
            raise ValueError('Review proxy must fit within 18 MB.')
        return await self._generate([prompt, types.Part.from_bytes(data=video_path.read_bytes(), mime_type='video/mp4')], {'json': True})

    async def generate_structured(self, prompt: str, schema_desc: str, fallback_fn=None, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        full_prompt = f"{prompt}\n\nRespond ONLY with valid JSON conforming to this schema:\n{schema_desc}"
        raw = await self.generate_text(full_prompt, options=options)
        if raw:
            clean = raw.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()
            try:
                return json.loads(clean)
            except Exception as e:
                logger.warning(f"Failed to parse Gemini JSON: {e}")
        if fallback_fn:
            return fallback_fn()
        return {}

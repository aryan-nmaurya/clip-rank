import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.ai.base import AIProvider
from app.core.runtime import run_blocking

logger = logging.getLogger("ai_shorts.gemini")

class GeminiProvider(AIProvider):
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.1-flash-lite"):
        self.api_key = api_key or os.environ.get("GOOGLE_AI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.model = model or "gemini-3.1-flash-lite"

    async def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    def _get_client(self):
        if not self.api_key:
            return None
        from google import genai
        from google.genai import types
        return genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=120000))

    async def _generate(self, contents, options=None):
        from google.genai import types
        # The SDK's optional aiohttp transport can assert under Python 3.14.
        # Its synchronous HTTP transport runs off the API loop and closes per call.
        def request():
            config = types.GenerateContentConfig(temperature=.25, max_output_tokens=6000,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
            if options and options.get('json'):
                config.response_mime_type = 'application/json'
            with self._get_client() as client:
                result = client.models.generate_content(model=self.model, contents=contents, config=config)
                return result.text if result else None
        for attempt in range(3):
            try:
                return await run_blocking(request)
            except Exception as exc:
                if getattr(exc,'code',None) in (429,500,502,503,504) and attempt<2:
                    await asyncio.sleep(2**attempt)
                    continue
                self.last_error = str(exc).replace(self.api_key or '__missing__', '[redacted]')[:500]
                logger.warning('Gemini request failed: %s', self.last_error)
                return None

    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        try:
            return await self._generate(prompt, options)
        except Exception as e:
            logger.warning(f"Gemini generate_text failed: {e}")
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
        except Exception as e:
            logger.warning(f"Gemini analyze_images failed: {e}")
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

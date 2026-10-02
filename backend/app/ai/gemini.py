import os
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.ai.base import AIProvider

logger = logging.getLogger("ai_shorts.gemini")

class GeminiProvider(AIProvider):
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.environ.get("GOOGLE_AI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.model = model or "gemini-2.5-flash"

    async def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    def _get_client(self):
        if not self.api_key:
            return None
        from google import genai
        return genai.Client(api_key=self.api_key)

    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        try:
            client = self._get_client()
            res = await client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            return res.text if res else None
        except Exception as e:
            logger.warning(f"Gemini generate_text failed: {e}")
            return None

    async def analyze_images(self, image_paths: List[Path], prompt: str, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        try:
            from PIL import Image
            images = [Image.open(p).copy() for p in image_paths if p.exists()]
            if not images:
                return await self.generate_text(prompt, options=options)
            client = self._get_client()
            contents = [prompt] + images
            res = await client.aio.models.generate_content(
                model=self.model,
                contents=contents,
            )
            return res.text if res else None
        except Exception as e:
            logger.warning(f"Gemini analyze_images failed: {e}")
            return None

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

import os
import json
import logging
import httpx
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.ai.base import AIProvider

logger = logging.getLogger("ai_shorts.openai")

class OpenAIProvider(AIProvider):
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or "gpt-4o-mini"

    async def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 5)

    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json={"model": self.model, "messages": messages, "temperature": 0.7}
                )
                if res.status_code == 200:
                    data = res.json()
                    return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"OpenAI generate_text failed: {e}")
        return None

    async def analyze_images(self, image_paths: List[Path], prompt: str, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not await self.is_available():
            return None
        import base64
        parts = [{"type": "text", "text": prompt}]
        for path in image_paths:
            parts.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode(), "detail": "high"}})
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "messages": [{"role": "user", "content": parts}], "temperature": .2})
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.warning("OpenAI frame analysis failed: %s", type(exc).__name__)
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
                logger.warning(f"Failed to parse OpenAI JSON: {e}")
        if fallback_fn:
            return fallback_fn()
        return {}

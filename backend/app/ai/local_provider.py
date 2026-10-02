import json
import logging
import httpx
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.ai.base import AIProvider

logger = logging.getLogger("ai_shorts.local_ai")

class LocalProvider(AIProvider):
    def __init__(self, endpoint: str = "http://localhost:11434", model: str = "qwen2.5:latest"):
        self.endpoint = endpoint.rstrip("/")
        self.model = model or "qwen2.5:latest"

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(f"{self.endpoint}/api/tags")
                return res.status_code == 200 and any(m.get("name") == self.model for m in res.json().get("models", []))
        except Exception:
            return False

    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
            }
            if system_prompt:
                payload["system"] = system_prompt
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(f"{self.endpoint}/api/generate", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("response")
        except Exception as e:
            logger.debug(f"Local Ollama generate_text failed: {e}")
        return None

    async def analyze_images(self, image_paths: List[Path], prompt: str, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        import base64
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{self.endpoint}/api/generate", json={
                    "model": self.model, "prompt": prompt, "stream": False, "format": "json",
                    "images": [base64.b64encode(p.read_bytes()).decode() for p in image_paths]})
                response.raise_for_status()
                return response.json().get("response")
        except Exception as exc:
            logger.warning("Local vision analysis failed: %s", type(exc).__name__)
            return None

    async def generate_structured(self, prompt: str, schema_desc: str, fallback_fn=None, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            payload = {
                "model": self.model,
                "prompt": f"{prompt}\nReturn JSON only:\n{schema_desc}",
                "stream": False,
                "format": "json"
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(f"{self.endpoint}/api/generate", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    raw = data.get("response", "{}")
                    return json.loads(raw)
        except Exception as e:
            logger.debug(f"Local Ollama generate_structured failed: {e}")
        if fallback_fn:
            return fallback_fn()
        return {}

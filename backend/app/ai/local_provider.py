import json
import logging
import httpx
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.ai.base import AIProvider

logger = logging.getLogger("ai_shorts.local_ai")

class LocalProvider(AIProvider):
    def __init__(self, endpoint: str = "http://localhost:11434", model: str = "qwen3-vl:4b"):
        self.endpoint = endpoint.rstrip("/")
        self.model = model or "qwen3-vl:4b"

    async def connection_status(self):
        status = {"endpoint": self.endpoint, "model": self.model, "reachable": False,
                  "model_installed": False, "vision_capable": False, "installed_models": []}
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                response = await client.get(f"{self.endpoint}/api/tags")
                response.raise_for_status()
                status["reachable"] = True
                status["installed_models"] = [m.get("name") for m in response.json().get("models", []) if m.get("name")]
                status["model_installed"] = self.model in status["installed_models"]
                if not status["model_installed"]:
                    status["message"] = "Endpoint connected; the selected model is not installed. Connect your vision model when ready."
                    return status
                info = await client.post(f"{self.endpoint}/api/show", json={"model": self.model})
                info.raise_for_status()
                status["vision_capable"] = "vision" in info.json().get("capabilities", [])
                status["message"] = "Vision model connected." if status["vision_capable"] else "The selected model does not report vision capability. Choose an image-capable model."
        except Exception:
            status["message"] = "Ollama is not connected. Enter the endpoint of your vision server when ready."
        return status

    async def is_available(self) -> bool:
        return (await self.connection_status())["vision_capable"]

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
                    "think": False, "options": {"temperature": .1},
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

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pathlib import Path

class AIProvider(ABC):
    @abstractmethod
    async def is_available(self) -> bool:
        """Returns True if the provider is configured and reachable."""
        pass

    @abstractmethod
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Generates plain text."""
        pass

    @abstractmethod
    async def analyze_images(self, image_paths: List[Path], prompt: str, options: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Multimodal image/frame analysis."""
        pass

    @abstractmethod
    async def generate_structured(self, prompt: str, schema_desc: str, fallback_fn=None, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generates structured JSON."""
        pass

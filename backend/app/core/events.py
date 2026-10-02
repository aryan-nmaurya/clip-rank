import asyncio
import json
import logging
from typing import Dict, Set, Any

logger = logging.getLogger("ai_shorts.events")

class EventBroadcaster:
    _instance = None

    def __init__(self):
        self.listeners: Dict[str, Set[asyncio.Queue]] = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = EventBroadcaster()
        return cls._instance

    def subscribe(self, job_id: str, queue: asyncio.Queue):
        if job_id not in self.listeners:
            self.listeners[job_id] = set()
        self.listeners[job_id].add(queue)

    def unsubscribe(self, job_id: str, queue: asyncio.Queue):
        if job_id in self.listeners:
            self.listeners[job_id].discard(queue)
            if not self.listeners[job_id]:
                del self.listeners[job_id]

    async def broadcast(self, job_id: str, data: Dict[str, Any]):
        if job_id in self.listeners:
            for q in list(self.listeners[job_id]):
                try:
                    await q.put(data)
                except Exception:
                    pass

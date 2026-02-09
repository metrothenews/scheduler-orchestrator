from __future__ import annotations

from typing import Any, Dict

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class PostExecutorClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=6))
    def schedule(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(
                f"{self.base_url}/v1/schedule",
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key,
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {"ok": True, "data": data}

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class SheetsJsonClient:
    """
    Matches the currently deployed sheets-json OpenAPI:

    POST /v1/read
      body: { "sheet_id": str, "ranges": [str, ...] }

    POST /v1/write
      body: { "sheet_id": str, "updates": [{ "range": str, "values": [[...], ...] }, ...] }

    POST /v1/read_kv
      body: { "sheet_id": str, "tab_name": str }
    """

    def __init__(self, *, base_url: str, api_key: str | None = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self, *, unredacted: bool) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if unredacted and self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _full_range(self, tab_name: str, a1_range: str) -> str:
        a1_range = a1_range.strip()
        if "!" in a1_range:
            return a1_range
        return f"{tab_name}!{a1_range}"

    def _extract_range_map(self, payload: Any) -> Dict[str, List[List[Any]]]:
        # Expected common shapes:
        # 1) {"ok":true,"data":{"Tab!A1:B2":[[...]], ...}}
        # 2) {"ok":true,"values":{"Tab!A1:B2":[[...]], ...}}
        # 3) {"Tab!A1:B2":[[...]]}  (rare)
        if isinstance(payload, dict):
            for k in ("data", "values"):
                v = payload.get(k)
                if isinstance(v, dict):
                    return v  # map range -> 2D values
            # if it's already a map of ranges
            if all(isinstance(v, list) for v in payload.values()):
                return payload  # type: ignore
        raise RuntimeError(f"Unexpected sheets-json /v1/read response shape: {type(payload)}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def read_range(
        self,
        *,
        sheet_id: str,
        tab_name: str,
        a1_range: str,
        unredacted: bool = False,
    ) -> List[List[Any]]:
        rng = self._full_range(tab_name, a1_range)
        body = {"sheet_id": sheet_id, "ranges": [rng]}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/v1/read", headers=self._headers(unredacted=unredacted), json=body)
            r.raise_for_status()
            mp = self._extract_range_map(r.json())
            return mp.get(rng, [])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def write_range(
        self,
        *,
        sheet_id: str,
        tab_name: str,
        a1_range: str,
        values: List[List[Any]],
        unredacted: bool = False,
    ) -> Dict[str, Any]:
        rng = self._full_range(tab_name, a1_range)
        body = {"sheet_id": sheet_id, "updates": [{"range": rng, "values": values}]}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/v1/write", headers=self._headers(unredacted=unredacted), json=body)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {"ok": True, "data": data}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def read_kv(self, *, sheet_id: str, tab_name: str, unredacted: bool = False) -> Dict[str, Any]:
        body = {"sheet_id": sheet_id, "tab_name": tab_name}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/v1/read_kv", headers=self._headers(unredacted=unredacted), json=body)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                return payload["data"]  # key/value dict
            if isinstance(payload, dict):
                return payload
            raise RuntimeError(f"Unexpected sheets-json /v1/read_kv response shape: {type(payload)}")

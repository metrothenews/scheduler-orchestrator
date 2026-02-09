from __future__ import annotations

from typing import Any, Dict, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class SheetsJsonClient:
    """
    Matches your deployed sheets-json:

    POST /v1/read
      body: { "sheet_id": str, "ranges": [str, ...] }
      response: { "ok": true, "valueRanges": [ { "range": "...", "values": [[...]] }, ... ] }
                (may also return {"data": {...}} / {"values": {...}} in other builds)

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
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected sheets-json /v1/read response shape: {type(payload)}")

        # 1) {"ok":true,"data":{"Tab!A1:B2":[[...]], ...}}
        v = payload.get("data")
        if isinstance(v, dict):
            return v

        # 2) {"ok":true,"values":{"Tab!A1:B2":[[...]], ...}}
        v = payload.get("values")
        if isinstance(v, dict):
            return v

        # 3) {"ok":true,"valueRanges":[{"range":"'Tab'!A1:B2","values":[[...]]}, ...]}
        vrs = payload.get("valueRanges")
        if isinstance(vrs, list):
            out: Dict[str, List[List[Any]]] = {}
            for item in vrs:
                if isinstance(item, dict) and isinstance(item.get("range"), str):
                    out[item["range"]] = item.get("values", []) or []
            return out

        # 4) {"Tab!A1:B2":[[...]]}  (rare)
        if all(isinstance(x, list) for x in payload.values()):
            return payload  # type: ignore

        raise RuntimeError("Unexpected sheets-json /v1/read response structure (no data/values/valueRanges).")

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

            # Normalize: Google may return "'Tab Name'!A1:B2" even if we asked for "Tab Name!A1:B2"
            if rng in mp:
                return mp[rng]
            for k, v in mp.items():
                if k.replace("'", "") == rng:
                    return v
            # If not found, return empty range
            return []

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
            payload = r.json()
            return payload if isinstance(payload, dict) else {"ok": True, "data": payload}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def read_kv(self, *, sheet_id: str, tab_name: str, unredacted: bool = False) -> Dict[str, Any]:
        body = {"sheet_id": sheet_id, "tab_name": tab_name}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}/v1/read_kv", headers=self._headers(unredacted=unredacted), json=body)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                return payload["data"]
            if isinstance(payload, dict):
                return payload
            raise RuntimeError(f"Unexpected sheets-json /v1/read_kv response shape: {type(payload)}")

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .openapi import find_json_body_spec
from .utils import parse_scalar


class SheetsJsonClient:
    def __init__(self, *, base_url: str, api_key: str | None = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._openapi: dict[str, Any] | None = None

        self._spec_read: Any | None = None
        self._spec_write: Any | None = None

        # Resolved field names (populated after load_openapi)
        self._k_sheet_id: str | None = None
        self._k_tab_name: str | None = None
        self._k_range: str | None = None
        self._k_values: str | None = None
        self._k_value_input_option: str | None = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def load_openapi(self) -> None:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.base_url}/openapi.json")
            r.raise_for_status()
            self._openapi = r.json()

        # Pick endpoints + request schemas
        self._spec_read = find_json_body_spec(
            self._openapi,
            candidate_paths=("/v1/read", "/v1/read_range", "/v1/read_a1", "/v1/read_values"),
            method="post",
        )
        self._spec_write = find_json_body_spec(
            self._openapi,
            candidate_paths=("/v1/write", "/v1/write_range", "/v1/write_a1"),
            method="post",
        )

        # Resolve field names by *exact* schema property names.
        # These candidates are only used to locate the exact names that exist in THIS gateway deployment.
        self._k_sheet_id = self._pick(self._spec_read.properties, ["sheet_id", "spreadsheet_id", "spreadsheetId"])
        self._k_tab_name = self._pick(self._spec_read.properties, ["tab_name", "sheet_name", "worksheet", "tab"])
        self._k_range = self._pick(self._spec_read.properties, ["range_a1", "a1_range", "range", "a1"])

        # write schema
        self._k_sheet_id_w = self._pick(self._spec_write.properties, ["sheet_id", "spreadsheet_id", "spreadsheetId"])
        self._k_tab_name_w = self._pick(self._spec_write.properties, ["tab_name", "sheet_name", "worksheet", "tab"])
        self._k_range_w = self._pick(self._spec_write.properties, ["range_a1", "a1_range", "range", "a1"])
        self._k_values = self._pick(self._spec_write.properties, ["values", "data", "rows"])
        self._k_value_input_option = self._pick_optional(self._spec_write.properties, ["value_input_option", "valueInputOption"])

        # Fail fast if gateway requires something we don't support.
        supported_read = {self._k_sheet_id, self._k_tab_name, self._k_range}
        unsupported_read_required = set(self._spec_read.required) - supported_read
        if unsupported_read_required:
            raise RuntimeError(
                f"sheets-json read endpoint {self._spec_read.path} requires unsupported fields: {sorted(unsupported_read_required)}"
            )

        supported_write = {self._k_sheet_id_w, self._k_tab_name_w, self._k_range_w, self._k_values}
        if self._k_value_input_option:
            supported_write.add(self._k_value_input_option)
        unsupported_write_required = set(self._spec_write.required) - supported_write
        if unsupported_write_required:
            raise RuntimeError(
                f"sheets-json write endpoint {self._spec_write.path} requires unsupported fields: {sorted(unsupported_write_required)}"
            )

    def _pick(self, props: Dict[str, Any], candidates: List[str]) -> str:
        for c in candidates:
            if c in props:
                return c
        raise RuntimeError(f"Could not resolve required field from candidates {candidates}. Available: {sorted(props.keys())}")

    def _pick_optional(self, props: Dict[str, Any], candidates: List[str]) -> str | None:
        for c in candidates:
            if c in props:
                return c
        return None

    def _headers(self, *, unredacted: bool) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if unredacted and self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _extract_values(self, payload: Any) -> List[List[Any]]:
        # Try common envelope shapes.
        if isinstance(payload, dict):
            for k in ("values", "data", "rows"):
                v = payload.get(k)
                if isinstance(v, list):
                    return v
        if isinstance(payload, list):
            return payload
        raise RuntimeError(f"Unexpected sheets-json response shape: {type(payload)}")

    def read_range(self, *, sheet_id: str, tab_name: str, a1_range: str, unredacted: bool = False) -> List[List[Any]]:
        if self._openapi is None:
            self.load_openapi()

        # First try range without tab in A1, then with tab prefix.
        attempts = [a1_range, f"{tab_name}!{a1_range}"] if "!" not in a1_range else [a1_range]
        last_err: Exception | None = None

        for rng in attempts:
            body = {
                self._k_sheet_id: sheet_id,
                self._k_tab_name: tab_name,
                self._k_range: rng,
            }
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(f"{self.base_url}{self._spec_read.path}", headers=self._headers(unredacted=unredacted), json=body)
                    r.raise_for_status()
                    return self._extract_values(r.json())
            except Exception as e:
                last_err = e

        raise last_err or RuntimeError("read_range failed")

    def write_range(
        self,
        *,
        sheet_id: str,
        tab_name: str,
        a1_range: str,
        values: List[List[Any]],
        unredacted: bool = False,
        value_input_option: str = "USER_ENTERED",
    ) -> Dict[str, Any]:
        if self._openapi is None:
            self.load_openapi()

        attempts = [a1_range, f"{tab_name}!{a1_range}"] if "!" not in a1_range else [a1_range]
        last_err: Exception | None = None

        for rng in attempts:
            body: Dict[str, Any] = {
                self._k_sheet_id_w: sheet_id,
                self._k_tab_name_w: tab_name,
                self._k_range_w: rng,
                self._k_values: values,
            }
            if self._k_value_input_option:
                body[self._k_value_input_option] = value_input_option

            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(f"{self.base_url}{self._spec_write.path}", headers=self._headers(unredacted=unredacted), json=body)
                    r.raise_for_status()
                    data = r.json()
                    return data if isinstance(data, dict) else {"ok": True, "data": data}
            except Exception as e:
                last_err = e

        raise last_err or RuntimeError("write_range failed")

    def read_kv(self, *, sheet_id: str, tab_name: str, unredacted: bool = False) -> Dict[str, Any]:
        # This endpoint exists in your gateway; we intentionally do NOT guess its schema.
        # We call it only if present in OpenAPI and only with the exact request fields discovered there.
        if self._openapi is None:
            self.load_openapi()

        paths = (self._openapi or {}).get("paths", {})
        if "/v1/read_kv" not in paths:
            raise RuntimeError("Gateway does not expose /v1/read_kv in openapi.json")

        spec = find_json_body_spec(self._openapi, candidate_paths=("/v1/read_kv",), method="post")
        k_sheet = self._pick(spec.properties, ["sheet_id", "spreadsheet_id", "spreadsheetId"])
        k_tab = self._pick(spec.properties, ["tab_name", "sheet_name", "worksheet", "tab"])

        body = {k_sheet: sheet_id, k_tab: tab_name}

        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base_url}{spec.path}", headers=self._headers(unredacted=unredacted), json=body)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
                return {str(k): parse_scalar(v) for k, v in data["data"].items()}
            if isinstance(data, dict):
                return {str(k): parse_scalar(v) for k, v in data.items()}
            raise RuntimeError(f"Unexpected read_kv response shape: {type(data)}")

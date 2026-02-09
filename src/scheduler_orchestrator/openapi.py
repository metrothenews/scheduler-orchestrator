from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class JsonBodySpec:
    path: str
    method: str
    properties: dict[str, Any]
    required: set[str]


def _lower(s: str) -> str:
    return s.lower()


def find_json_body_spec(openapi: dict[str, Any], *, candidate_paths: Iterable[str], method: str = "post") -> JsonBodySpec:
    paths = openapi.get("paths", {}) or {}
    for p in candidate_paths:
        if p not in paths:
            continue
        op = (paths[p] or {}).get(method, {})
        rb = (op or {}).get("requestBody", {}) or {}
        content = (rb.get("content", {}) or {}).get("application/json", {}) or {}
        schema = content.get("schema", {}) or {}
        props = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        return JsonBodySpec(path=p, method=method, properties=props, required=required)
    raise RuntimeError(f"Could not find JSON requestBody schema for any of: {list(candidate_paths)}")

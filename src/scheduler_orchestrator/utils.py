from __future__ import annotations

import re
from typing import Any


def normalize_header(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    return s


def truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("true", "t", "yes", "y", "1", "checked")


def parse_scalar(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (bool, int, float)):
        return v
    s = str(v).strip()
    if s == "":
        return ""
    sl = s.lower()
    if sl in ("true", "false"):
        return sl == "true"
    # int?
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except Exception:
            return s
    # float?
    if re.fullmatch(r"-?\d+\.\d+", s):
        try:
            return float(s)
        except Exception:
            return s
    return s


def col_to_a1(col_idx0: int) -> str:
    # 0 -> A
    n = col_idx0 + 1
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(ord("A") + r) + out
    return out


def cell_a1(row_1: int, col_idx0: int) -> str:
    return f"{col_to_a1(col_idx0)}{row_1}"

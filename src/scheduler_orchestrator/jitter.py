from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class JitterSpec:
    minutes_min: int = 1
    minutes_max: int = 20
    seconds_min: int = 5
    seconds_max: int = 55


def deterministic_jitter(seed_str: str, spec: JitterSpec) -> tuple[int, int]:
    # Inclusive ranges
    minutes_span = spec.minutes_max - spec.minutes_min + 1
    seconds_span = spec.seconds_max - spec.seconds_min + 1

    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    seed_int = int.from_bytes(h[:8], "big", signed=False)

    minutes = spec.minutes_min + (seed_int % minutes_span)
    seconds = spec.seconds_min + ((seed_int // minutes_span) % seconds_span)
    return minutes, seconds

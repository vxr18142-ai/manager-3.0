# utils/rolepanel.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoleOption:
    role_id: int
    label: str


def clamp_int(n: int, low: int, high: int) -> int:
    return max(low, min(high, int(n)))

# utils/timeparse.py
from __future__ import annotations


def humanize_minutes(mins: int) -> str:
    mins = max(1, int(mins))
    if mins < 60:
        return "1 min" if mins == 1 else f"{mins} mins"
    h = mins // 60
    m = mins % 60
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


def clamp(n: int, low: int, high: int) -> int:
    return max(low, min(high, n))

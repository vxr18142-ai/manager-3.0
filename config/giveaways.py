# config/giveaways.py
from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


GIVEAWAY_LOG_CHANNEL_ID = _env_int("GIVEAWAY_LOG_CHANNEL_ID", 0)

DEFAULT_MIN_ACCOUNT_AGE_DAYS = 7

DURATION_CHOICES = [
    ("5m", 5 * 60),
    ("10m", 10 * 60),
    ("30m", 30 * 60),
    ("1h", 60 * 60),
    ("2h", 2 * 60 * 60),
    ("6h", 6 * 60 * 60),
    ("12h", 12 * 60 * 60),
    ("1d", 24 * 60 * 60),
    ("3d", 3 * 24 * 60 * 60),
    ("7d", 7 * 24 * 60 * 60),
]

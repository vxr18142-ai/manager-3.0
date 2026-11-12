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


ROLE_SELECTOR = {
    "channel_id": _env_int("ROLE_SELECTOR_CHANNEL_ID", 0),
    "embed": {
        "title": "🏴 Sind Rust Servers",
        "description": (
            "Please select the server you would like to play on to unlock the respective channels.\n\n"
            "• You may select **multiple servers**\n"
            "• Click again to **remove** access"
        ),
    },
    "buttons": [
        {
            "label": "3X",
            "emoji": "🎮",
            "role_id": _env_int("ROLE_SELECTOR_ROLE_3X", 0),
        },
    ],
}

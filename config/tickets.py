# config/tickets.py
from __future__ import annotations
import os
import json
from typing import Any, Dict


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _env_json(name: str, default: Dict[str, Any]) -> Dict[str, Any]:
    v = os.getenv(name)
    if not v:
        return default
    try:
        parsed = json.loads(v)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return default


TICKET_PANEL_CHANNEL_ID = os.getenv("TICKET_PANEL_CHANNEL_ID")

INACTIVITY_HOURS = _env_int("INACTIVITY_HOURS", 24)

DEFAULT_SERVERS: Dict[str, Any] = {
    "EU": {"label": "EU", "emoji": "🇪🇺"},
}

SERVERS = _env_json("TICKET_SERVERS", DEFAULT_SERVERS)

DEFAULT_TICKET_TYPES: Dict[str, Any] = {
    "general": {"label": "📄 Ticket General", "description": "General questions or requests."},
    "base": {"label": "🏠 Ticket Base", "description": "Base or area related issues."},
    "clan": {"label": "👥 Ticket Clan", "description": "Clan-related issues or requests."},
    "shop": {"label": "💎 Ticket Shop", "description": "Store or purchase questions."},
    "raid": {"label": "💰 Bounty Raid", "description": "bounty raids."},
    "bug": {"label": "🐛 Ticket Bug", "description": "Bug or glitch reports."},
}

TICKET_TYPES = _env_json("TICKET_TYPES", DEFAULT_TICKET_TYPES)

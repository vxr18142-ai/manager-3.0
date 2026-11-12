# utils/constants.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerScope:
    EU_3X: str = "EU_3X"
    EU_5X: str = "EU_5X"
    GLOBAL: str = "GLOBAL"


SCOPE_LABELS = {
    ServerScope.EU_3X: "EU 3X",
    ServerScope.EU_5X: "EU 5X",
    ServerScope.GLOBAL: "GLOBAL",
}

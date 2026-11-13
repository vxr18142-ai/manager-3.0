from __future__ import annotations
import os


def _parse_color(val: str | None) -> int:
    if not val:
        return 0x008B8B
    s = val.strip()
    if s.startswith("#"):
        s = s[1:]
    if s.startswith("0x"):
        s = s[2:]
    try:
        return int(s, 16)
    except Exception:
        try:
            return int(val)
        except Exception:
            return 0x0000ff


BRAND_NAME = os.getenv("BRAND_NAME", "VOIDEX HQ Core")
EMBED_COLOR = _parse_color(os.getenv("EMBED_COLOR", None))
FOOTER_TEXT = os.getenv("FOOTER_TEXT", "VOIDEX HQ Core")

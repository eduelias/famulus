"""Resolve a famulus user's current location from Home Assistant.

FAMULUS_USER_PERSONS maps WhatsApp numbers to HA person entities:
    FAMULUS_USER_PERSONS=31618337245:person.eduelias,31618337246:person.venancia7_msn_com

Returns (lat, lon, zone_label) or None. Read-only; never raises into callers.
"""
import os

import httpx

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

_MAP = {}
for pair in os.environ.get("FAMULUS_USER_PERSONS", "").split(","):
    if ":" in pair:
        num, ent = pair.split(":", 1)
        _MAP[num.strip()] = ent.strip()


def person_of(user: str) -> str | None:
    return _MAP.get((user or "").strip())


def locate(user: str) -> tuple[float, float, str] | None:
    """(lat, lon, zone) for the user's HA person, or None if unavailable."""
    ent = person_of(user)
    if not (ent and HA_URL and HA_TOKEN):
        return None
    try:
        s = httpx.get(f"{HA_URL}/api/states/{ent}",
                      headers={"Authorization": f"Bearer {HA_TOKEN}"},
                      timeout=10).json()
        a = s.get("attributes", {})
        lat, lon = a.get("latitude"), a.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon), s.get("state", "unknown")
    except Exception:
        return None

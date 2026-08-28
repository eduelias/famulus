"""Family locator: 'where is my wife?' via Home Assistant person entities.

FAMULUS_FAMILY_NAMES maps spoken names to WhatsApp numbers (which geo.py maps
on to HA persons): FAMULUS_FAMILY_NAMES=eduardo:316...245,wife:316...246
Family-internal by design — only allowlisted family members can ask.
"""
import os

from .. import geo
from ..plugins.base import BasePlugin, spec

_NAMES = {}
for pair in os.environ.get("FAMULUS_FAMILY_NAMES", "").split(","):
    if ":" in pair:
        name, num = pair.split(":", 1)
        _NAMES[name.strip().lower()] = num.strip()


class FamilyPlugin(BasePlugin):
    name = "family"
    tools = [
        spec("where_is",
             "Locate a family member ('where is my wife?', 'is Eduardo home?'). "
             "Returns their current zone (home/away/work) from the home system.",
             {"person": {"type": "string",
                         "description": "family member name, e.g. 'wife', 'eduardo'"}},
             ["person"]),
    ]

    def execute(self, tool: str, args: dict) -> object:
        if tool != "where_is":
            raise ValueError(f"unknown tool {tool}")
        name = str(args.get("person", "")).strip().lower()
        num = _NAMES.get(name)
        if not num:
            known = ", ".join(sorted(_NAMES)) or "(none configured)"
            return {"error": f"I don't know '{name}' — I can locate: {known}"}
        loc = geo.locate(num)
        if not loc:
            return {"person": name, "location": "unknown",
                    "message": "their phone isn't reporting a location right now"}
        lat, lon, zone = loc
        return {"person": name, "zone": zone,
                "note": "zone 'home' = at home; other values are HA zone names "
                        "or 'not_home' (away)"}

"""Deterministic owner fast-path for allowlist management.

A small local model drowning in dozens of tools reliably fails to pick the
right one for a rare admin action ("add my wife 316…") — it tends to refuse
instead. Allowlist changes are exactly the kind of high-value, low-frequency
action that shouldn't depend on flaky tool-selection, so the webhook recognises
them directly and routes to the users plugin (still owner-gated). Anything that
doesn't match falls through to the normal LLM agent unchanged.
"""
from __future__ import annotations

import re

_NUMBER = re.compile(r"(\+?\d[\d\s\-]{6,}\d)")
_ADD = re.compile(r"\b(add|allow|invite|whitelist|let|grant)\b", re.IGNORECASE)
_REMOVE = re.compile(r"\b(remove|revoke|block|deny|disallow|kick|unallow)\b", re.IGNORECASE)
_LIST = re.compile(
    r"\b(who|which|list|show)\b.{0,40}\b(allow|allowed|access|users?|use the bot|numbers?)\b",
    re.IGNORECASE)
_RELATION = re.compile(r"\bmy (\w+)\b", re.IGNORECASE)


def parse_admin_intent(text: str) -> dict | None:
    """Return {'action': 'add'|'remove'|'list', 'number'?, 'label'?} or None.

    Only returns add/remove when a phone-number-like token is present, so plain
    chatter ('add milk to the list') doesn't trigger it.
    """
    t = (text or "").strip()
    low = t.lower()
    m = _NUMBER.search(t)
    number = "".join(c for c in m.group(1) if c.isdigit()) if m else ""

    if _LIST.search(low) and ("user" in low or "allow" in low or "access" in low
                              or "use the bot" in low or "number" in low):
        return {"action": "list"}
    if number and len(number) >= 8:
        if _REMOVE.search(low):
            return {"action": "remove", "number": number}
        if _ADD.search(low):
            rel = _RELATION.search(t)
            label = rel.group(1) if rel else ""
            return {"action": "add", "number": number, "label": label}
    return None

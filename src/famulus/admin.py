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

from . import access

_NUMBER = re.compile(r"(\+?\d[\d\s\-]{6,}\d)")
_ADD = re.compile(r"\b(add|allow|invite|whitelist|let|grant|give)\b", re.IGNORECASE)
_REMOVE = re.compile(r"\b(remove|revoke|block|deny|disallow|kick|unallow)\b", re.IGNORECASE)
_RESTRICT = re.compile(r"\b(only|just|restrict|limit)\b", re.IGNORECASE)
_ACCESS_Q = re.compile(r"\b(what|which).{0,30}\b(can|access|use|do)\b|\baccess (for|of)\b"
                       r"|\bcan (they|it|he|she|this number)\b", re.IGNORECASE)
_LIST = re.compile(
    r"\b(who|which|list|show)\b.{0,40}\b(allow|allowed|access|users?|use the bot|numbers?)\b",
    re.IGNORECASE)
_RELATION = re.compile(r"\bmy (\w+)\b", re.IGNORECASE)


def parse_admin_intent(text: str) -> dict | None:
    """Return an admin intent dict or None.

    Actions: 'list' (all users), 'access' (one user's domains), 'remove',
    'grant' (allowlist + restrict to domains), 'add' (allowlist, full access).
    A phone number must be present for the per-user actions, so plain chatter
    ('add milk to the list') doesn't trigger it.
    """
    t = (text or "").strip()
    low = t.lower()
    m = _NUMBER.search(t)
    number = "".join(c for c in m.group(1) if c.isdigit()) if m else ""
    domains = access.resolve_domains(t)

    # global list (no specific number)
    if not number and _LIST.search(low) and (
            "user" in low or "allow" in low or "access" in low
            or "use the bot" in low or "number" in low):
        return {"action": "list"}
    if not number or len(number) < 8:
        return None

    if _REMOVE.search(low):
        return {"action": "remove", "number": number}
    # a read-only "what can X use?" — only when it's not also an add/grant verb
    if _ACCESS_Q.search(low) and not _ADD.search(low) and not domains:
        return {"action": "access", "number": number}
    rel = _RELATION.search(t)
    label = rel.group(1) if rel else ""
    # restricted add / grant: any recognised domain, or an explicit restrict word
    if domains and (_ADD.search(low) or _RESTRICT.search(low) or _ACCESS_Q.search(low)):
        return {"action": "grant", "number": number, "domains": domains, "label": label}
    # full-access add
    if _ADD.search(low):
        return {"action": "add", "number": number, "label": label}
    return None

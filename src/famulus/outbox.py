"""Things plugins want sent that are not the reply to the current message.

A tool runs inside one sender's turn and can only answer that sender. Some
plugins need more: tell another user something (a requester hears "it's live"),
or put a decision in front of the *owner* that another user's message triggered
("build this request? YES/NO"). Plugins queue those here, from any thread
(a tool call, a background worker, a cron tick); the webhook drains the queue
after every message and on a short timer.

Owner approvals are ordinary gated actions: the owner sees ``description`` and
only a fresh YES runs ``tool`` with ``args``, as the owner. Only one can wait
for an answer at a time, so the rest queue behind it.
"""
from __future__ import annotations

import threading
from collections import deque

_lock = threading.Lock()
_items: deque[dict] = deque()


def send_text(to: str, text: str) -> None:
    """Queue a free-text WhatsApp message. Meta drops these outside the 24-hour
    window since the user last wrote; use send_template for cold notifications."""
    _put({"kind": "text", "to": to, "text": text})


def send_template(to: str, name: str, params: list[str], lang: str = "en") -> None:
    """Queue an approved WhatsApp template message (body parameters in order)."""
    _put({"kind": "template", "to": to, "name": name, "params": [str(p) for p in params],
          "lang": lang})


def ask_owner(tool: str, args: dict, description: str) -> None:
    """Queue a gated action for the owner to approve with YES (or refuse with NO)."""
    _put({"kind": "approval", "tool": tool, "args": dict(args), "description": description})


def _put(item: dict) -> None:
    with _lock:
        _items.append(item)


def drain() -> list[dict]:
    """Take everything queued so far, oldest first."""
    with _lock:
        out = list(_items)
        _items.clear()
    return out

"""Best-effort mirror of the conversation log to an IRC channel (via the du7bot
HTTP bridge), so exchanges can be followed from an IRC client like the rest of
the household logging.

Active only when FAMULUS_LOG_CONVERSATIONS is on AND the bridge is configured;
fail-soft — a down bridge must never delay or break a WhatsApp reply.

    FAMULUS_CHAT_IRC_URL      e.g. http://192.168.2.68:8096/notify
    FAMULUS_CHAT_IRC_TOKEN    the bridge token
    FAMULUS_CHAT_IRC_CHANNEL  channel name (default "famulus-chat")
"""
import contextlib
import json
import os
import urllib.parse
import urllib.request

from . import config

URL = os.environ.get("FAMULUS_CHAT_IRC_URL", "").strip()
TOKEN = os.environ.get("FAMULUS_CHAT_IRC_TOKEN", "").strip()
CHANNEL = os.environ.get("FAMULUS_CHAT_IRC_CHANNEL", "famulus-chat").strip()
_LINE = 400


def enabled() -> bool:
    return bool(URL and TOKEN and config.LOG_CONVERSATIONS)


def _send(text: str) -> None:
    q = urllib.parse.urlencode({"channel": CHANNEL, "token": TOKEN})
    req = urllib.request.Request(f"{URL}?{q}", data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=3).read()


def post(direction: str, user: str, text: str, tools: list | None = None) -> None:
    """Mirror one conversation line to IRC. Best-effort; swallows errors.
    direction: "in" (user → bot) or "out" (bot → user)."""
    if not enabled() or not text:
        return
    tail = f" [tools: {', '.join(tools)}]" if tools else ""
    arrow = "→" if direction == "in" else "←"
    line = " ".join(text.split())
    # IRC mirroring is nice-to-have, never worth failing a message over
    with contextlib.suppress(Exception):
        _send(f"{arrow} <{user}>{tail} {line[:_LINE]}")
        for i in range(_LINE, min(len(line), 3 * _LINE), _LINE):
            _send("  " + line[i:i + _LINE])

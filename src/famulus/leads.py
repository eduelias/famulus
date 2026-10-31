"""Lead intake: what famulus does with a message from a number that isn't allowlisted.

Off unless LEAD_INTAKE is on. When on, a stranger's message never reaches the agent, the
tools or the history: famulus answers once a day with a fixed text (LEAD_REPLY) and forwards
the message to the owner with a wa.me link, so the owner answers from their own phone.
Rate-limited per number and per day so a spammer can't flood the owner or burn messages.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import config

MAX_TEXT = 1000  # forwarded characters per message

# what a stranger's non-text message looks like in the forward
KINDS = {"audio": "áudio", "voice": "áudio", "image": "imagem", "video": "vídeo",
         "document": "arquivo", "sticker": "figurinha", "location": "localização",
         "contacts": "contato"}


def describe(msg: dict) -> str:
    """The forwarded text: the body for text, a caption or a [kind] note otherwise."""
    kind = msg.get("type", "")
    if kind == "text":
        return (msg.get("text", {}).get("body") or "").strip()[:MAX_TEXT]
    body = msg.get(kind)
    caption = body.get("caption", "") if isinstance(body, dict) else ""
    note = f"[{KINDS.get(kind, kind or 'mensagem')}]"
    return f"{note} {caption.strip()[:MAX_TEXT]}".strip()


@dataclass
class LeadIntake:
    per_sender_per_day: int = 10
    per_day: int = 50
    clock: Callable[[], float] = time.time
    _replied: dict[str, float] = field(default_factory=dict)  # number -> last auto-reply
    _forwards: dict[str, list[float]] = field(default_factory=dict)  # number -> times

    def handle(self, number: str, name: str, msg: dict) -> tuple[str | None, str | None]:
        """Returns (reply to the stranger or None, message for the owner or None)."""
        now = self.clock()
        day = now - 86400
        for n in list(self._forwards):
            self._forwards[n] = [t for t in self._forwards[n] if t > day]
            if not self._forwards[n]:
                del self._forwards[n]
        mine = self._forwards.get(number, [])
        total = sum(len(v) for v in self._forwards.values())
        if len(mine) >= self.per_sender_per_day or total >= self.per_day:
            return None, None

        self._forwards.setdefault(number, []).append(now)
        who = f"{name} (+{number})" if name else f"+{number}"
        forward = (f"📥 {config.LEAD_LABEL}: {who}\n\n{describe(msg)}\n\n"
                   f"Responder: https://wa.me/{number}")
        reply = None
        if self._replied.get(number, 0) <= day:
            self._replied[number] = now
            reply = config.LEAD_REPLY
        return reply, forward

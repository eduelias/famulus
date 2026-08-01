"""Outbound WhatsApp Cloud API calls."""
import logging

import httpx

from . import config

GRAPH = "https://graph.facebook.com/v20.0"
log = logging.getLogger("famulus")


async def send_text(to: str, text: str) -> bool:
    """Send a text message. Returns False (and logs Meta's error body) on failure."""
    chunks = [text[i: i + 4000] for i in range(0, len(text), 4000)] or [""]
    async with httpx.AsyncClient(timeout=30) as client:
        for chunk in chunks:
            r = await client.post(
                f"{GRAPH}/{config.WA_PHONE_ID}/messages",
                headers={"Authorization": f"Bearer {config.WA_TOKEN}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": chunk},
                },
            )
            if r.status_code >= 400:
                log.error("WhatsApp send to %s failed (%s): %s", to, r.status_code, r.text)
                return False
    return True

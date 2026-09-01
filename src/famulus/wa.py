"""Outbound WhatsApp Cloud API calls."""
import logging

import httpx

from . import config

GRAPH = "https://graph.facebook.com/v20.0"
log = logging.getLogger("famulus")


def fetch_media(media_id: str) -> tuple[bytes, str]:
    """Download a WhatsApp media object (sync). Returns (content, mime_type).

    Shared by plugins whose tools accept a wa_media_id — a file the user sent
    over WhatsApp (Meta keeps media ~30 days; a stale id raises a clear error)."""
    headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
    with httpx.Client(timeout=60) as client:
        meta = client.get(f"{GRAPH}/{media_id}", headers=headers)
        meta.raise_for_status()
        info = meta.json()
        blob = client.get(info["url"], headers=headers)
        blob.raise_for_status()
        return blob.content, info.get("mime_type", "application/octet-stream")


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

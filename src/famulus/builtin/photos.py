"""Photo retrieval from Immich, delivered as WhatsApp images.

"a picture of baby Lily" → resolve the person via Immich's face DB, CLIP smart
search for the qualifier ("baby"), fetch previews, upload to the Meta media API,
send to the asking user. Uses sync httpx throughout (plugin execute() runs
inside the agent loop — no nested asyncio).

Requires IMMICH_URL + IMMICH_API_KEY in the environment. Person search works
once faces are named in the Immich UI; text search works once the CLIP job has
indexed the library.
"""
import os

import httpx

from .. import config, context
from ..plugins.base import BasePlugin, spec

IMMICH_URL = os.environ.get("IMMICH_URL", "").rstrip("/")
IMMICH_KEY = os.environ.get("IMMICH_API_KEY", "")
GRAPH = "https://graph.facebook.com/v20.0"


def _immich(method: str, path: str, **kw):
    r = httpx.request(method, f"{IMMICH_URL}/api{path}",
                      headers={"x-api-key": IMMICH_KEY}, timeout=30, **kw)
    r.raise_for_status()
    return r


def _find_person(name: str) -> dict | None:
    people = _immich("GET", "/people", params={"withHidden": "false"}).json()
    people = people.get("people", people) if isinstance(people, dict) else people
    name = name.strip().lower()
    exact = [p for p in people if p.get("name", "").lower() == name]
    partial = [p for p in people if name in p.get("name", "").lower()]
    return (exact or partial or [None])[0]


def _search_assets(person_id: str | None, query: str, count: int) -> list[dict]:
    if query:
        body = {"query": query, "size": max(count * 3, 10), "type": "IMAGE"}
        if person_id:
            body["personIds"] = [person_id]
        res = _immich("POST", "/search/smart", json=body).json()
    else:
        body = {"size": max(count * 3, 10), "type": "IMAGE", "order": "desc",
                "withPeople": True}
        if person_id:
            body["personIds"] = [person_id]
        res = _immich("POST", "/search/metadata", json=body).json()
    items = res.get("assets", {}).get("items", [])
    return items[:count]


def _send_image_to(user: str, image: bytes, mime: str, caption: str) -> bool:
    up = httpx.post(
        f"{GRAPH}/{config.WA_PHONE_ID}/media",
        headers={"Authorization": f"Bearer {config.WA_TOKEN}"},
        data={"messaging_product": "whatsapp"},
        files={"file": ("photo.jpg", image, mime or "image/jpeg")},
        timeout=60)
    if up.status_code >= 400:
        return False
    media_id = up.json().get("id")
    msg = httpx.post(
        f"{GRAPH}/{config.WA_PHONE_ID}/messages",
        headers={"Authorization": f"Bearer {config.WA_TOKEN}"},
        json={"messaging_product": "whatsapp", "to": user, "type": "image",
              "image": {"id": media_id, "caption": caption[:1000]}},
        timeout=60)
    return msg.status_code < 400


class PhotosPlugin(BasePlugin):
    name = "photos"
    persona = (
        "You can retrieve real photos from the family photo library (Immich) and "
        "send them in the chat. Use photo_search for any request like 'a picture "
        "of X', 'show me photos from the beach', 'recent pics of the kids'. The "
        "images are sent automatically — after the tool call, just add a short "
        "friendly line about what was sent.")
    tools = [
        spec("photo_search",
             "Search the family photo library and SEND matching photos to the "
             "asking user on WhatsApp. person = a named family member (as tagged "
             "in the library); query = free-text scene/content search ('beach', "
             "'baby', 'birthday cake'); both together narrow it ('baby' photos of "
             "person 'Lily'). Omit both for the most recent photos.",
             {"person": {"type": "string", "description": "family member name, optional"},
              "query": {"type": "string", "description": "free-text content search, optional"},
              "count": {"type": "integer", "description": "how many photos, default 1, max 5"}},
             []),
    ]

    def execute(self, tool: str, args: dict) -> object:
        if tool != "photo_search":
            raise ValueError(f"unknown tool {tool}")
        if not (IMMICH_URL and IMMICH_KEY):
            raise ValueError("photo library is not configured (IMMICH_URL/IMMICH_API_KEY)")
        user = context.current_user()
        if not user:
            raise ValueError("no user in context")
        person_name = str(args.get("person", "") or "").strip()
        query = str(args.get("query", "") or "").strip()
        count = min(max(int(args.get("count", 1) or 1), 1), 5)

        person = None
        if person_name:
            person = _find_person(person_name)
            if person is None:
                return {"sent": 0,
                        "message": f"Nobody named '{person_name}' is tagged in the "
                                   "photo library yet. Faces can be named in Immich; "
                                   "try a content search meanwhile."}
        assets = _search_assets(person["id"] if person else None, query, count)
        if not assets:
            return {"sent": 0, "message": "No matching photos found."}

        sent = 0
        for a in assets:
            thumb = None
            try:
                thumb = _immich("GET", f"/assets/{a['id']}/thumbnail",
                                params={"size": "preview"})
            except httpx.HTTPStatusError:
                # thumbnail not generated yet (fresh index) — try the original,
                # guarded by WhatsApp's ~5MB image limit
                orig = _immich("GET", f"/assets/{a['id']}/original")
                if len(orig.content) <= 4_500_000:
                    thumb = orig
            if thumb is None:
                continue
            when = (a.get("fileCreatedAt") or a.get("localDateTime") or "")[:10]
            cap = " ".join(x for x in [person_name.title() if person_name else "",
                                       query, f"({when})" if when else ""] if x).strip()
            if _send_image_to(user, thumb.content,
                              thumb.headers.get("content-type", "image/jpeg"),
                              cap or "📷 from the family library"):
                sent += 1
        return {"sent": sent, "of": len(assets),
                "message": f"sent {sent} photo(s) to the chat"}

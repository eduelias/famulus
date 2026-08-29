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
import re

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


def _search_assets(person_ids: list[str], query: str, count: int,
                   recent: bool = False, year: int = 0) -> list[dict]:
    if query:
        # recent=True: take a wide pool of RELEVANT matches, then newest wins
        body = {"query": query, "size": 60 if recent else max(count * 3, 10),
                "type": "IMAGE"}
        if person_ids:
            body["personIds"] = person_ids
        if year:
            body["takenAfter"] = f"{year}-01-01T00:00:00Z"
            body["takenBefore"] = f"{year}-12-31T23:59:59Z"
        res = _immich("POST", "/search/smart", json=body).json()
    else:
        body = {"size": 200, "type": "IMAGE", "order": "desc", "withPeople": True}
        if person_ids:
            body["personIds"] = person_ids
        if year:
            body["takenAfter"] = f"{year}-01-01T00:00:00Z"
            body["takenBefore"] = f"{year}-12-31T23:59:59Z"
        res = _immich("POST", "/search/metadata", json=body).json()
    items = res.get("assets", {}).get("items", [])
    if not query or recent:
        # the API's order param isn't reliable — "most recent" must be true:
        # sort by capture date ourselves before slicing
        items.sort(key=lambda a: a.get("localDateTime") or a.get("fileCreatedAt") or "",
                   reverse=True)
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
             "asking user on WhatsApp. people = family member name(s) as tagged, "
             "comma-separated — MULTIPLE names means photos where they appear "
             "TOGETHER ('lily, ben' = both in the same shot). query = free-text "
             "scene search ('beach', 'birthday cake') — ALWAYS write the query "
             "in ENGLISH, translating the user's words (the image search only "
             "understands English; names in people= stay as tagged). Combine to narrow. Set "
             "recent=true whenever the user says recent/latest/newest — without it, "
             "query results are best-match regardless of age. Omit people+query "
             "for most recent overall. count: keep 1 unless the user asks for more.",
             {"people": {"type": "string",
                         "description": "comma-separated tagged names; multiple = together in one photo"},
              "person": {"type": "string", "description": "single family member (legacy alias)"},
              "query": {"type": "string",
                        "description": "free-text content search IN ENGLISH (translate from the user's language), optional"},
              "recent": {"type": "boolean",
                         "description": "true = newest among the matches (user said recent/latest)"},
              "year": {"type": "integer",
                       "description": "restrict to one year, e.g. 2025 for 'last year' (see today's date in your context)"},
              "exclusive": {"type": "boolean",
                            "description": "true = ONLY the named people in the photo, nobody else ('just the two of them')"},
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
        names_raw = str(args.get("people", "") or args.get("person", "") or "")
        names = [n.strip() for n in re.split(r"[,;]| and | e | met ", names_raw,
                                             flags=re.IGNORECASE) if n.strip()]
        query = str(args.get("query", "") or "").strip()
        count = min(max(int(args.get("count", 1) or 1), 1), 5)

        person_ids, display = [], []
        for n in names:
            person = _find_person(n)
            if person is None:
                return {"sent": 0, "final": True,
                        "message": f"Nobody named '{n}' is tagged in the photo "
                                   "library yet. Faces can be named in Immich; "
                                   "try a content search meanwhile.",
                        "instruction": "Relay this message to the user now. Do NOT "
                                       "call photo_search again for this request."}
            person_ids.append(person["id"])
            display.append(person.get("name") or n.title())
        recent = bool(args.get("recent"))
        year = int(args.get("year", 0) or 0)
        exclusive = bool(args.get("exclusive"))
        fetch = count * 4 if exclusive else count
        assets = _search_assets(person_ids, query, fetch, recent, year)
        if exclusive and person_ids:
            wanted = set(person_ids)
            picked = []
            for a in assets:
                info = _immich("GET", "/assets/" + a["id"]).json()
                on_photo = {pp.get("id") for pp in info.get("people", [])}
                if on_photo == wanted:
                    picked.append(a)
                if len(picked) >= count:
                    break
            assets = picked
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
            cap = " ".join(x for x in [" & ".join(display), query,
                                       f"({when})" if when else ""] if x).strip()
            if _send_image_to(user, thumb.content,
                              thumb.headers.get("content-type", "image/jpeg"),
                              cap or "📷 from the family library"):
                sent += 1
        return {"sent": sent, "of": len(assets),
                "message": f"sent {sent} photo(s) to the chat"}

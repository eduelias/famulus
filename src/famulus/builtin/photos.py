"""Photo retrieval from Immich, delivered as WhatsApp images.

"a picture of baby Lily" → resolve the person via Immich's face DB, CLIP smart
search for the qualifier ("baby"), fetch previews, upload to the Meta media API,
send to the asking user. Uses sync httpx throughout (plugin execute() runs
inside the agent loop — no nested asyncio).

When people are named, candidates are ranked by how many OTHER faces are on the
photo: a shot of just the named people beats a group shot, and group shots are
only sent when nothing closer exists.

Requires IMMICH_URL + IMMICH_API_KEY in the environment. Person search works
once faces are named in the Immich UI; text search works once the CLIP job has
indexed the library.

Names resolve through an owner-maintained alias file (DATA_DIR/photo_aliases.json,
nickname -> tagged name) and then Immich's person search. A name that matches
more than one tagged person ("Noah") is never guessed: the user is asked.
Photos already sent to a user recently are skipped, so "another one" is new.
"""
import datetime as dt
import json
import os
import re
import time
import unicodedata

import httpx

from .. import config, context
from ..plugins.base import BasePlugin, spec

IMMICH_URL = os.environ.get("IMMICH_URL", "").rstrip("/")
IMMICH_KEY = os.environ.get("IMMICH_API_KEY", "")
GRAPH = "https://graph.facebook.com/v20.0"

# how many candidates to inspect per requested photo when ranking by company
POOL_PER_PHOTO = 10
POOL_MIN, POOL_MAX = 30, 40
# don't resend a photo to the same user within this window
SENT_TTL = 12 * 3600
SENT_CAP = 500
_sent: dict[str, dict[str, float]] = {}   # user -> {asset_id: sent_at}

ALIAS_FILE = os.path.join(config.DATA_DIR, "photo_aliases.json")


def _immich(method: str, path: str, **kw):
    r = httpx.request(method, f"{IMMICH_URL}/api{path}",
                      headers={"x-api-key": IMMICH_KEY}, timeout=30, **kw)
    r.raise_for_status()
    return r


def _people_list(res) -> list[dict]:
    if isinstance(res, dict):
        return res.get("people", res) if isinstance(res.get("people"), list) else []
    return res or []


def _norm(s: str) -> str:
    """lowercase, accents stripped, single spaces: 'Vitória ' -> 'vitoria'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _load_aliases() -> dict[str, str]:
    try:
        with open(ALIAS_FILE) as f:
            d = json.load(f)
        return {_norm(k): v for k, v in d.items() if isinstance(v, str)} \
            if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_aliases(d: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(ALIAS_FILE), exist_ok=True)
    tmp = ALIAS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict(sorted(d.items())), f, indent=1, ensure_ascii=False)
    os.replace(tmp, ALIAS_FILE)


def _candidates(name: str) -> list[dict]:
    """Tagged people matching `name`: exact (accent/case-insensitive) matches if
    any, else people whose name words start with every word typed ('noah' ->
    'Noah Costa', 'Noah Francisco'). Immich's own fuzzy hits are filtered out.
    The library holds thousands of detected faces and GET /people only returns
    the first page, so the lookup is server-side, never a scan."""
    want = _norm(name)
    if not want:
        return []
    target = _load_aliases().get(want)
    if target:
        want = _norm(target)
    seen, people = set(), []
    for q in {want, want.split()[0]}:
        res = _immich("GET", "/search/person",
                      params={"name": q, "withHidden": "false"}).json()
        for p in _people_list(res):
            if p.get("name") and p.get("id") not in seen:
                seen.add(p.get("id"))
                people.append(p)
    exact = [p for p in people if _norm(p["name"]) == want]
    if exact:
        return exact
    words = want.split()
    return [p for p in people
            if all(any(t.startswith(w) for t in _norm(p["name"]).split()) for w in words)]


def _find_person(name: str) -> dict | None:
    """The single tagged person `name` refers to, or None (unknown OR ambiguous)."""
    c = _candidates(name)
    return c[0] if len(c) == 1 else None


def _named_people() -> list[dict]:
    """Everyone tagged in the library (Immich lists named people first)."""
    res = _immich("GET", "/people", params={"withHidden": "false"}).json()
    return sorted((p for p in _people_list(res) if p.get("name")),
                  key=lambda p: p["name"].lower())


def _age_window(birth: str, age: int) -> tuple[str, str] | None:
    """Capture-date range in which someone born on `birth` was `age` years old."""
    try:
        b = dt.date.fromisoformat(str(birth)[:10])
    except ValueError:
        return None

    def plus(years: int) -> dt.date:
        try:
            return b.replace(year=b.year + years)
        except ValueError:          # born on 29 Feb
            return b.replace(year=b.year + years, day=28)
    return (f"{plus(age).isoformat()}T00:00:00Z",
            f"{(plus(age + 1) - dt.timedelta(days=1)).isoformat()}T23:59:59Z")


def _recently_sent(user: str) -> set[str]:
    now = time.time()
    got = {a: t for a, t in _sent.get(user, {}).items() if now - t < SENT_TTL}
    _sent[user] = got
    return set(got)


def _mark_sent(user: str, asset_id: str) -> None:
    got = _sent.setdefault(user, {})
    got[asset_id] = time.time()
    if len(got) > SENT_CAP:
        for a, _ in sorted(got.items(), key=lambda kv: kv[1])[:len(got) - SENT_CAP]:
            del got[a]


def _search_assets(person_ids: list[str], query: str, count: int,
                   recent: bool = False, year: int = 0, after: str = "",
                   before: str = "", language: str = "") -> list[dict]:
    if year and not (after or before):
        after, before = f"{year}-01-01T00:00:00Z", f"{year}-12-31T23:59:59Z"
    window = {k: v for k, v in (("takenAfter", after), ("takenBefore", before)) if v}
    if query:
        # recent=True: take a wide pool of RELEVANT matches, then newest wins
        body = {"query": query, "size": max(60 if recent else count * 3, 10),
                "type": "IMAGE", **window}
        if person_ids:
            body["personIds"] = person_ids
        if language and language != "en":
            body["language"] = language
        try:
            res = _immich("POST", "/search/smart", json=body).json()
        except httpx.HTTPStatusError:
            if "language" not in body:
                raise
            body.pop("language")        # unknown code: search as English
            res = _immich("POST", "/search/smart", json=body).json()
    else:
        body = {"size": 200, "type": "IMAGE", "order": "desc", "withPeople": True,
                **window}
        if person_ids:
            body["personIds"] = person_ids
        res = _immich("POST", "/search/metadata", json=body).json()
    items = res.get("assets", {}).get("items", [])
    if not query or recent:
        # the API's order param isn't reliable — "most recent" must be true:
        # sort by capture date ourselves before slicing
        items.sort(key=lambda a: a.get("localDateTime") or a.get("fileCreatedAt") or "",
                   reverse=True)
    for a in items:
        # metadata search (withPeople) already carries the faces; smart search
        # returns an empty list regardless, so those need a per-asset lookup
        a["_people_known"] = not query
    return items[:count]


def _people_on(asset: dict) -> set[str]:
    if asset.get("_people_known"):
        people = asset.get("people") or []
    else:
        people = _immich("GET", "/assets/" + asset["id"]).json().get("people", [])
    return {p.get("id") for p in people if p.get("id")}


def _rank_by_company(assets: list[dict], wanted: set[str], count: int,
                     exclusive: bool) -> list[dict]:
    """Fewest strangers first; ties keep the search order (relevance/recency).
    Annotates each asset with `_others` = number of faces that are not wanted."""
    for a in assets:
        a["_others"] = len(_people_on(a) - wanted)
    ranked = sorted(enumerate(assets), key=lambda ia: (ia[1]["_others"], ia[0]))
    picked = [a for _, a in ranked if not exclusive or a["_others"] == 0]
    return picked[:count]


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
        "friendly line about what was sent. People are found by FACE tags only: "
        "a name in query= finds nothing about that person. If photo_search says a "
        "name is not tagged, or that it matches several people, tell the user "
        "exactly that (ask which one) and stop — never retry with the name as a "
        "text query, and never claim someone is tagged unless photo_people lists "
        "them. You cannot tag faces or edit the library.")
    tools = [
        spec("photo_search",
             "Search the family photo library and SEND matching photos to the "
             "asking user on WhatsApp. people = family member name(s) or nicknames, "
             "comma-separated — MULTIPLE names means photos where they appear "
             "TOGETHER ('lily, ben' = both in the same shot). Photos of ONLY the "
             "named people are preferred automatically; group shots are sent only "
             "when nothing closer exists. Photos already sent recently are skipped. "
             "query = free-text SCENE search ('beach', 'praia', 'birthday cake') in "
             "the user's own words, with language = its ISO code. NEVER put a "
             "person's name in query: it is not face recognition. Use age for "
             "'baby X' (0) or 'X at 3' (3). Set recent=true whenever the user says "
             "recent/latest/newest — without it, query results are best-match "
             "regardless of age. Omit people+query for most recent overall. "
             "count: keep 1 unless the user asks for more.",
             {"people": {"type": "string",
                         "description": "comma-separated names/nicknames; multiple = together in one photo"},
              "person": {"type": "string", "description": "single family member (legacy alias)"},
              "query": {"type": "string",
                        "description": "free-text scene/content search, optional. Not for names."},
              "language": {"type": "string",
                           "description": "ISO 639-1 code of the words in query: 'pt', 'nl', 'en'"},
              "recent": {"type": "boolean",
                         "description": "true = newest among the matches (user said recent/latest)"},
              "year": {"type": "integer",
                       "description": "restrict to one year, e.g. 2025 for 'last year' (see today's date in your context)"},
              "age": {"type": "integer",
                      "description": "age in whole years of the FIRST named person in the photo: "
                                     "0 = baby/first year. Uses their Immich birth date."},
              "exclusive": {"type": "boolean",
                            "description": "true = REQUIRE only the named people, send nothing if no such "
                                           "photo exists ('just the two of them'). Default already "
                                           "prefers such photos, so rarely needed."},
              "count": {"type": "integer", "description": "how many photos, default 1, max 5"}},
             []),
        spec("photo_people",
             "List the names tagged (face-identified) in the family photo library, "
             "their nicknames, and who has a birth date set. Use it to answer 'is X "
             "in the library / tagged?' or 'who can you find photos of?'. Sends nothing.",
             {}, []),
        spec("photo_alias",
             "Owner only: remember a nickname for a tagged person, e.g. alias "
             "'Maria Vitoria' -> person 'Mavi', so photo_search understands it. "
             "remove=true forgets the nickname.",
             {"alias": {"type": "string", "description": "the nickname"},
              "person": {"type": "string", "description": "the tagged name it means"},
              "remove": {"type": "boolean", "description": "true = forget this nickname"}},
             ["alias"]),
    ]

    def execute(self, tool: str, args: dict) -> object:
        if tool not in ("photo_search", "photo_people", "photo_alias"):
            raise ValueError(f"unknown tool {tool}")
        if not (IMMICH_URL and IMMICH_KEY):
            raise ValueError("photo library is not configured (IMMICH_URL/IMMICH_API_KEY)")
        user = context.current_user()
        if not user:
            raise ValueError("no user in context")
        if tool == "photo_people":
            return self._people()
        if tool == "photo_alias":
            return self._alias(user, args)
        return self._search(user, args)

    def _people(self) -> dict:
        people = _named_people()
        aliases = _load_aliases()
        return {"tagged_people": [p["name"] for p in people],
                "nicknames": aliases,
                "with_birth_date": [p["name"] for p in people if p.get("birthDate")],
                "message": "Only these names (or the nicknames) can be searched by "
                           "face. Anyone else is not tagged; say so plainly."}

    def _alias(self, user: str, args: dict) -> dict:
        if not config.is_owner(user):
            return {"final": True, "message": "Only the owner can set photo nicknames."}
        alias = " ".join(str(args.get("alias", "")).split())
        if not alias:
            raise ValueError("alias is required")
        aliases = _load_aliases()
        if args.get("remove"):
            gone = aliases.pop(_norm(alias), None)
            _save_aliases(aliases)
            return {"final": True, "message": f"Forgot nickname '{alias}'." if gone
                    else f"'{alias}' was not a nickname."}
        target = str(args.get("person", "") or "").strip()
        aliases.pop(_norm(alias), None)          # never resolve an alias via itself
        matches = [c for c in _candidates(target)] if target else []
        if len(matches) != 1:
            names = ", ".join(c["name"] for c in matches)
            return {"final": True, "message": (
                f"'{target}' matches several tagged people ({names}); give the full name."
                if matches else f"Nobody named '{target}' is tagged in the photo library.")}
        aliases[_norm(alias)] = matches[0]["name"]
        _save_aliases(aliases)
        return {"final": True,
                "message": f"Noted: '{alias}' means {matches[0]['name']} in photo searches."}

    def _search(self, user: str, args: dict) -> dict:
        names_raw = str(args.get("people", "") or args.get("person", "") or "")
        names = [n.strip() for n in re.split(r"[,;&]| and | e | en | met ", names_raw,
                                             flags=re.IGNORECASE) if n.strip()]
        query = str(args.get("query", "") or "").strip()
        language = str(args.get("language", "") or "").strip().lower()[:3]
        count = min(max(int(args.get("count", 1) or 1), 1), 5)

        persons = []
        for n in names:
            found = _candidates(n)
            if len(found) > 1:
                return {"sent": 0, "final": True,
                        "message": f"'{n}' matches more than one tagged person: "
                                   f"{', '.join(p['name'] for p in found)}. Ask the "
                                   "user which one they mean."}
            if not found:
                return {"sent": 0, "final": True,
                        "message": f"Nobody named '{n}' is tagged in the photo "
                                   "library yet. Faces can be named in Immich; "
                                   "try a content search meanwhile.",
                        "instruction": "Relay this message to the user now. Do NOT "
                                       "call photo_search again for this request, "
                                       "and do NOT use the name as a text query."}
            persons.append(found[0])
        person_ids = [p["id"] for p in persons]
        display = [p["name"] for p in persons]
        recent = bool(args.get("recent"))
        year = int(args.get("year", 0) or 0)
        exclusive = bool(args.get("exclusive"))

        after = before = ""
        age = args.get("age")
        if age not in (None, "") and persons:
            window = _age_window(persons[0].get("birthDate") or "", max(int(age), 0))
            if window is None:
                return {"sent": 0, "final": True,
                        "message": f"{display[0]} has no birth date in the photo "
                                   "library, so I can't pick photos by age. It can "
                                   "be set on their Immich person page."}
            after, before = window
            year = 0

        skip = _recently_sent(user)
        if person_ids:
            pool = min(max(count * POOL_PER_PHOTO, POOL_MIN), POOL_MAX)
            found = _search_assets(person_ids, query, pool + len(skip), recent, year,
                                   after, before, language)
            fresh = [a for a in found if a["id"] not in skip][:pool]
            assets = _rank_by_company(fresh or found[:pool], set(person_ids), count, exclusive)
            if not assets and found:
                return {"sent": 0, "final": True,
                        "message": f"No photo with only {' & '.join(display)} matched; "
                                   f"{len(found)} with other people in the shot "
                                   "exist. Ask if those are wanted."}
        else:
            found = _search_assets(person_ids, query, count + len(skip), recent, year,
                                   after, before, language)
            assets = ([a for a in found if a["id"] not in skip] or found)[:count]
        if not assets:
            return {"sent": 0, "message": "No matching photos found."}
        repeats = sum(1 for a in assets if a["id"] in skip)

        sent, others = 0, []
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
                _mark_sent(user, a["id"])
                others.append(a.get("_others", 0))
        out = {"sent": sent, "of": len(assets),
               "message": f"sent {sent} photo(s) to the chat"}
        if person_ids:
            alone = sum(1 for o in others if o == 0)
            out["others_on_photo"] = others
            out["message"] += (f": {alone} with only {' & '.join(display)}, "
                               f"{sent - alone} with other people in the shot")
        elif query:
            out["note"] = ("Content (scene) search only — no person was identified. "
                           "Do not tell the user anyone is tagged or recognised.")
        if repeats:
            out["message"] += f"; {repeats} of them were sent before (no new matches left)"
        return out

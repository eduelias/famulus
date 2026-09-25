"""Photos plugin: person resolution, search modes, ranking, WhatsApp delivery."""
import httpx
import pytest

from famulus.builtin import photos

LILY = {"id": "L", "name": "Lily", "birthDate": "2017-05-10"}
BEN = {"id": "B", "name": "Ben"}
PEOPLE = [LILY, BEN, {"id": "N1", "name": "Noah Costa"},
          {"id": "N2", "name": "Noah Francisco"}, {"id": "M", "name": "Mavi"},
          {"id": "V", "name": "Maria Vitória Lopes"}]


@pytest.fixture(autouse=True)
def _cfg(monkeypatch, tmp_path):
    monkeypatch.setattr(photos, "IMMICH_URL", "http://immich:2283")
    monkeypatch.setattr(photos, "IMMICH_KEY", "k")
    monkeypatch.setattr(photos, "ALIAS_FILE", str(tmp_path / "photo_aliases.json"))
    monkeypatch.setattr(photos, "_sent", {})
    monkeypatch.setattr("famulus.context.current_user", lambda: "31600000001")


class _R:
    def __init__(self, payload=None):
        self._p = payload
    content = b"jpeg"
    headers = {"content-type": "image/jpeg"}

    def json(self):
        return self._p


def _fake_immich(people=PEOPLE, items=None, faces=None, bodies=None, smart_fails_lang=False):
    """/search/person does a loose substring match (like Immich's fuzzy search);
    search endpoints return `items`; /assets/{id} reveals `faces[id]`."""
    def fake(method, path, **kw):
        if path == "/search/person":
            q = photos._norm(kw["params"]["name"])
            return _R([p for p in people if q[:3] in photos._norm(p["name"])])
        if path == "/people":
            return _R({"people": people})
        if path.startswith("/search/"):
            body = kw["json"]
            if bodies is not None:
                bodies.append((path, dict(body)))
            if smart_fails_lang and "language" in body:
                raise httpx.HTTPStatusError("bad", request=None, response=None)
            return _R({"assets": {"items": [dict(a) for a in (items or [])]}})
        if path.startswith("/assets/") and not path.endswith(("/thumbnail", "/original")):
            aid = path.split("/")[2]
            return _R({"people": [{"id": p} for p in (faces or {}).get(aid, [])]})
        return _R()
    return fake


def _run(monkeypatch, args, **fake):
    monkeypatch.setattr(photos, "_immich", _fake_immich(**fake))
    sent = []
    monkeypatch.setattr(photos, "_send_image_to", lambda u, i, m, c: sent.append(c) or True)
    return photos.PhotosPlugin().execute("photo_search", args), sent


# ---- name resolution ------------------------------------------------------

def test_resolution_exact_prefix_and_fuzzy_rejected(monkeypatch):
    monkeypatch.setattr(photos, "_immich", _fake_immich())
    assert photos._find_person("lily")["id"] == "L"
    assert photos._find_person("LILY ")["id"] == "L"
    assert photos._find_person("maria vitoria")["id"] == "V"     # accents ignored
    assert photos._find_person("mav")["id"] == "M"               # word prefix
    assert photos._find_person("lilian") is None                 # fuzzy hit rejected
    assert photos._find_person("noah") is None                   # ambiguous -> not guessed
    assert photos._find_person("noah fran")["id"] == "N2"


def test_ambiguous_name_asks_which_one(monkeypatch):
    out, sent = _run(monkeypatch, {"people": "Noah e Mavi"})
    assert out["sent"] == 0 and out["final"] is True and not sent
    assert "Noah Costa" in out["message"] and "Noah Francisco" in out["message"]


def test_unknown_person_forbids_name_as_query(monkeypatch):
    out, sent = _run(monkeypatch, {"people": "Lily, Zé"})
    assert out["sent"] == 0 and out["final"] is True and "Zé" in out["message"]
    assert "text query" in out["instruction"] and not sent


def test_alias_resolves_and_owner_only(monkeypatch):
    monkeypatch.setattr(photos, "_immich", _fake_immich())
    monkeypatch.setattr("famulus.config.is_owner", lambda u: True)
    p = photos.PhotosPlugin()
    out = p.execute("photo_alias", {"alias": "Mavie", "person": "mavi"})
    assert "Mavi" in out["message"] and out["final"] is True
    assert photos._find_person("mavie")["id"] == "M"
    out = p.execute("photo_alias", {"alias": "Noah da Mavi", "person": "noah"})
    assert "several" in out["message"]                           # must be unambiguous
    p.execute("photo_alias", {"alias": "Noah da Mavi", "person": "Noah Francisco"})
    assert photos._find_person("noah da mavi")["id"] == "N2"
    p.execute("photo_alias", {"alias": "mavie", "remove": True})
    assert photos._find_person("mavie") is None
    monkeypatch.setattr("famulus.config.is_owner", lambda u: False)
    out = p.execute("photo_alias", {"alias": "x", "person": "Lily"})
    assert "Only the owner" in out["message"]


def test_photo_people_lists_names_nicknames_birthdates(monkeypatch):
    monkeypatch.setattr(photos, "_immich", _fake_immich(
        people=[LILY, {"id": "2", "name": ""}, BEN, {"id": "4"}]))
    photos._save_aliases({"mavie": "Mavi"})
    out = photos.PhotosPlugin().execute("photo_people", {})
    assert out["tagged_people"] == ["Ben", "Lily"]
    assert out["nicknames"] == {"mavie": "Mavi"}
    assert out["with_birth_date"] == ["Lily"]


def test_unconfigured():
    photos.IMMICH_URL = ""
    with pytest.raises(ValueError):
        photos.PhotosPlugin().execute("photo_search", {})


# ---- search bodies --------------------------------------------------------

def test_smart_vs_metadata_and_year(monkeypatch):
    bodies = []
    monkeypatch.setattr(photos, "_immich", _fake_immich(items=[{"id": "x"}], bodies=bodies))
    photos._search_assets(["pid"], "baby", 2)
    assert bodies[-1][0] == "/search/smart" and bodies[-1][1]["personIds"] == ["pid"]
    photos._search_assets([], "", 1, year=2025)
    path, body = bodies[-1]
    assert path == "/search/metadata" and "personIds" not in body
    assert body["takenAfter"].startswith("2025-01-01")
    assert body["takenBefore"].startswith("2025-12-31")


def test_language_passed_and_dropped_when_rejected(monkeypatch):
    bodies = []
    monkeypatch.setattr(photos, "_immich", _fake_immich(items=[{"id": "x"}], bodies=bodies))
    photos._search_assets([], "praia", 1, language="pt")
    assert bodies[-1][1]["language"] == "pt"
    photos._search_assets([], "beach", 1, language="en")
    assert "language" not in bodies[-1][1]                        # en is the default
    bodies.clear()
    monkeypatch.setattr(photos, "_immich", _fake_immich(
        items=[{"id": "x"}], bodies=bodies, smart_fails_lang=True))
    assert photos._search_assets([], "strand", 1, language="xx")[0]["id"] == "x"
    assert "language" not in bodies[-1][1]                        # retried as English


def test_metadata_search_sorts_newest_first(monkeypatch):
    items = [{"id": "old", "localDateTime": "2023-01-01T10:00:00"},
             {"id": "new", "localDateTime": "2026-08-20T10:00:00"},
             {"id": "mid", "fileCreatedAt": "2025-01-01T10:00:00"}]
    monkeypatch.setattr(photos, "_immich", _fake_immich(items=items))
    assert [a["id"] for a in photos._search_assets(["pid"], "", 2)] == ["new", "mid"]


def test_recent_flag_date_sorts_smart_results(monkeypatch):
    items = [{"id": "best-match-2017", "localDateTime": "2017-07-01"},
             {"id": "ok-match-2026", "localDateTime": "2026-08-01"}]
    bodies = []
    monkeypatch.setattr(photos, "_immich", _fake_immich(items=items, bodies=bodies))
    assert photos._search_assets(["pid"], "beach", 1, recent=True)[0]["id"] == "ok-match-2026"
    assert bodies[-1][1]["size"] == 60
    assert photos._search_assets(["pid"], "beach", 1)[0]["id"] == "best-match-2017"


# ---- age -------------------------------------------------------------------

def test_age_window_from_birth_date():
    assert photos._age_window("2017-05-10", 0) == ("2017-05-10T00:00:00Z",
                                                   "2018-05-09T23:59:59Z")
    assert photos._age_window("2020-02-29", 1)[0] == "2021-02-28T00:00:00Z"
    assert photos._age_window("", 1) is None


def test_age_filters_by_first_person_birth_date(monkeypatch):
    bodies = []
    out, _ = _run(monkeypatch, {"people": "Lily", "age": 0, "year": 2025},
                     items=[{"id": "a", "people": [{"id": "L"}]}], bodies=bodies)
    body = bodies[0][1]
    assert body["takenAfter"].startswith("2017-05-10")          # age wins over year
    assert body["takenBefore"].startswith("2018-05-09")
    assert out["sent"] == 1


def test_age_without_birth_date_is_final(monkeypatch):
    out, sent = _run(monkeypatch, {"people": "Ben", "age": 1})
    assert out["final"] is True and "birth date" in out["message"] and not sent


# ---- ranking by company ------------------------------------------------------

def test_prefers_photo_of_only_the_named_people(monkeypatch):
    """Best CLIP match is a crowded group shot; the pair-only photo must win."""
    bodies = []
    out, sent = _run(monkeypatch, {"people": "lily, ben", "query": "christmas", "year": 2025},
                     items=[{"id": "xmas"}, {"id": "trio"}, {"id": "pair"}],
                     faces={"xmas": ["L", "B", "V", "u1", "u2"], "trio": ["L", "B", "X"],
                            "pair": ["L", "B"]}, bodies=bodies)
    assert out["sent"] == 1 and out["others_on_photo"] == [0]
    assert bodies[0][1]["size"] >= photos.POOL_MIN
    assert "1 with only Lily & Ben, 0 with other people" in out["message"]
    assert "Lily & Ben" in sent[0]


def test_ranking_keeps_relevance_within_a_tier(monkeypatch):
    out, _ = _run(monkeypatch, {"people": "lily, ben", "query": "beach", "count": 3},
                  items=[{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
                  faces={"a": ["L", "B", "X"], "b": ["L", "B"], "c": ["L", "B"],
                         "d": ["L", "B", "X", "Y"]})
    assert out["others_on_photo"] == [0, 0, 1]


def test_group_shot_only_when_nothing_closer_exists(monkeypatch):
    out, _ = _run(monkeypatch, {"people": "lily, ben", "query": "park"},
                  items=[{"id": "g1"}, {"id": "g2"}],
                  faces={"g1": ["L", "B", "X", "Y"], "g2": ["L", "B", "X"]})
    assert out["sent"] == 1 and out["others_on_photo"] == [1]
    assert "0 with only Lily & Ben, 1 with other people" in out["message"]


def test_exclusive_refuses_group_shots_but_says_they_exist(monkeypatch):
    out, sent = _run(monkeypatch, {"people": "lily, ben", "query": "park", "exclusive": True},
                     items=[{"id": "g1"}, {"id": "g2"}],
                     faces={"g1": ["L", "B", "X"], "g2": ["L", "B", "Y"]})
    assert out["sent"] == 0 and out["final"] is True and not sent
    assert "No photo with only Lily & Ben" in out["message"] and "2 with other" in out["message"]


def test_metadata_results_need_no_per_asset_lookup(monkeypatch):
    def fake(method, path, **kw):
        assert not (path.startswith("/assets/") and not path.endswith("/thumbnail"))
        return _fake_immich(items=[
            {"id": "solo", "localDateTime": "2025-05-01", "people": [{"id": "L"}, {"id": "B"}]},
            {"id": "grp", "localDateTime": "2025-06-01",
             "people": [{"id": "L"}, {"id": "B"}, {"id": "X"}]}])(method, path, **kw)
    monkeypatch.setattr(photos, "_immich", fake)
    monkeypatch.setattr(photos, "_send_image_to", lambda *a: True)
    out = photos.PhotosPlugin().execute("photo_search", {"people": "lily, ben", "year": 2025})
    assert out["others_on_photo"] == [0]


# ---- repeats + delivery -------------------------------------------------------

def test_does_not_resend_until_nothing_new_is_left(monkeypatch):
    items = [{"id": "p1", "people": [{"id": "L"}]}, {"id": "p2", "people": [{"id": "L"}]}]
    first, _ = _run(monkeypatch, {"people": "Lily"}, items=items)
    second, _ = _run(monkeypatch, {"people": "Lily"}, items=items)
    assert first["sent"] == second["sent"] == 1
    sent_ids = set(photos._sent["31600000001"])
    assert sent_ids == {"p1", "p2"}                               # a different photo
    third, _ = _run(monkeypatch, {"people": "Lily"}, items=items)
    assert third["sent"] == 1 and "sent before" in third["message"]


def test_query_only_search_flags_no_face_match(monkeypatch):
    out, _ = _run(monkeypatch, {"query": "mavie"}, items=[{"id": "x"}])
    assert out["sent"] == 1 and "no person was identified" in out["note"]
    assert "others_on_photo" not in out


def test_caption_and_recipient(monkeypatch):
    monkeypatch.setattr(photos, "_immich", _fake_immich(
        items=[{"id": "a1", "fileCreatedAt": "2020-05-01T"}], faces={"a1": ["L"]}))
    got = []
    monkeypatch.setattr(photos, "_send_image_to",
                        lambda u, img, mime, cap: got.append((u, cap)) or True)
    out = photos.PhotosPlugin().execute("photo_search", {"person": "lily", "query": "baby"})
    assert out["sent"] == 1 and got[0][0] == "31600000001"
    assert got[0][1] == "Lily baby (2020-05-01)"

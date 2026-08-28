"""Photos plugin: person resolution, search modes, WhatsApp delivery."""
import pytest

from famulus.builtin import photos


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(photos, "IMMICH_URL", "http://immich:2283")
    monkeypatch.setattr(photos, "IMMICH_KEY", "k")
    monkeypatch.setattr("famulus.context.current_user", lambda: "31600000001")


def test_person_resolution_prefers_exact(monkeypatch):
    people = [{"id": "a", "name": "Lily Saleh"}, {"id": "b", "name": "Lily"}]
    monkeypatch.setattr(photos, "_immich",
                        lambda m, p, **k: type("R", (), {"json": lambda s: {"people": people}})())
    assert photos._find_person("lily")["id"] == "b"
    assert photos._find_person("lily sal")["id"] == "a"
    assert photos._find_person("ben") is None


def test_search_smart_vs_metadata(monkeypatch):
    calls = []

    class R:
        def __init__(self, path): self.path = path
        def json(self):
            return {"assets": {"items": [{"id": "x", "fileCreatedAt": "2020-05-01T00:00:00Z"}]}}

    def fake(method, path, **kw):
        calls.append((path, kw.get("json", {})))
        return R(path)

    monkeypatch.setattr(photos, "_immich", fake)
    photos._search_assets("pid", "baby", 2)
    assert calls[-1][0] == "/search/smart" and calls[-1][1]["personIds"] == ["pid"]
    photos._search_assets(None, "", 1)
    assert calls[-1][0] == "/search/metadata" and "personIds" not in calls[-1][1]


def test_execute_sends_and_reports(monkeypatch):
    monkeypatch.setattr(photos, "_find_person", lambda n: {"id": "pid", "name": "Lily"})
    monkeypatch.setattr(photos, "_search_assets",
                        lambda p, q, c: [{"id": "a1", "fileCreatedAt": "2020-05-01T"}])

    class Thumb:
        content = b"jpegbytes"
        headers = {"content-type": "image/jpeg"}
    monkeypatch.setattr(photos, "_immich", lambda m, p, **k: Thumb())
    sent = []
    monkeypatch.setattr(photos, "_send_image_to",
                        lambda u, img, mime, cap: sent.append((u, cap)) or True)
    out = photos.PhotosPlugin().execute("photo_search", {"person": "lily", "query": "baby"})
    assert out["sent"] == 1 and sent[0][0] == "31600000001"
    assert "Lily" in sent[0][1] and "baby" in sent[0][1]


def test_execute_unknown_person(monkeypatch):
    monkeypatch.setattr(photos, "_find_person", lambda n: None)
    out = photos.PhotosPlugin().execute("photo_search", {"person": "ghost"})
    assert out["sent"] == 0 and "ghost" in out["message"]


def test_execute_unconfigured(monkeypatch):
    monkeypatch.setattr(photos, "IMMICH_URL", "")
    with pytest.raises(ValueError):
        photos.PhotosPlugin().execute("photo_search", {})


def test_metadata_search_sorts_newest_first(monkeypatch):
    items = [{"id": "old", "localDateTime": "2023-01-01T10:00:00"},
             {"id": "new", "localDateTime": "2026-08-20T10:00:00"},
             {"id": "mid", "fileCreatedAt": "2025-01-01T10:00:00"}]

    class R:
        def json(self):
            return {"assets": {"items": list(items)}}
    monkeypatch.setattr(photos, "_immich", lambda m, p, **k: R())
    out = photos._search_assets("pid", "", 2)
    assert [a["id"] for a in out] == ["new", "mid"]   # newest first, truly

"""Per-user domain access control + the owner grant fast-path."""
import pytest

from famulus import access, admin, config


@pytest.fixture
def acc(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "GRANTS_FILE", str(tmp_path / "grants.json"))
    monkeypatch.setattr(config, "OWNER_WA_NUMBER", "31600000001")
    return access


ALL = {"tutor", "torrent", "homeassistant", "gmail", "shell", "users", "weather"}


def test_resolve_domains(acc):
    assert acc.resolve_domains("Dutch") == ["tutor"]
    assert acc.resolve_domains("torrent, weather") == ["torrent", "weather"]
    assert acc.resolve_domains("only home and lights") == ["homeassistant"]
    assert acc.resolve_domains("blahblah") == []


def test_owner_gets_everything(acc):
    assert acc.allowed_plugins("31600000001", ALL) == ALL
    assert acc.user_allowed("31600000001", "shell") is True


def test_no_entry_is_all_but_owner_only(acc):
    # wife-style user: no grants entry → everything except shell/users
    allowed = acc.allowed_plugins("31600000002", ALL)
    assert "tutor" in allowed and "gmail" in allowed
    assert "shell" not in allowed and "users" not in allowed
    assert acc.user_allowed("31600000002", "gmail") is True
    assert acc.user_allowed("31600000002", "shell") is False


def test_restricted_user(acc):
    acc.set_grants("31600000003", "Dutch")
    assert acc.get_grants("31600000003") == ["tutor"]
    assert acc.allowed_plugins("31600000003", ALL) == {"tutor"}
    assert acc.user_allowed("31600000003", "tutor") is True
    assert acc.user_allowed("31600000003", "torrent") is False
    assert acc.user_allowed("31600000003", "gmail") is False


def test_grant_cannot_include_owner_only(acc):
    acc.set_grants("31600000004", "Dutch, shell")   # shell must be dropped
    assert acc.get_grants("31600000004") == ["tutor"]


def test_set_empty_domains_raises(acc):
    with pytest.raises(ValueError):
        acc.set_grants("31600000005", "gibberish")


def test_clear_grants_restores_full(acc):
    acc.set_grants("31600000006", "torrent")
    assert acc.allowed_plugins("31600000006", ALL) == {"torrent"}
    assert acc.clear_grants("31600000006") is True
    assert "gmail" in acc.allowed_plugins("31600000006", ALL)   # back to full


# --- owner fast-path parsing ------------------------------------------------

def test_admin_intent_grant_and_add(acc):
    r = admin.parse_admin_intent("add my friend 31612345678, only Dutch")
    assert r["action"] == "grant" and r["number"] == "31612345678"
    assert r["domains"] == ["tutor"] and r["label"] == "friend"

    r = admin.parse_admin_intent("let 31612345678 use torrent and weather")
    assert r["action"] == "grant" and r["domains"] == ["torrent", "weather"]

    r = admin.parse_admin_intent("restrict 31612345678 to Dutch")
    assert r["action"] == "grant" and r["domains"] == ["tutor"]

    r = admin.parse_admin_intent("add my wife 31612345678")   # no domains → full
    assert r["action"] == "add"

    r = admin.parse_admin_intent("what can 31612345678 use?")
    assert r["action"] == "access" and r["number"] == "31612345678"

    assert admin.parse_admin_intent("what's the weather in Dutch cities") is None

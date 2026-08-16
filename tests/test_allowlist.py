"""Dynamic allowlist + owner-gated user management."""
import importlib

import pytest


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_WA_NUMBERS", "31600000001")
    monkeypatch.setenv("OWNER_WA_NUMBER", "31600000001")
    monkeypatch.setenv("ALLOWLIST_FILE", str(tmp_path / "allowed.json"))
    from famulus import config
    importlib.reload(config)
    return config


def test_norm_and_owner(cfg):
    assert cfg._norm_number("+31 6-0000 0001") == "31600000001"
    assert cfg.is_owner("+31600000001") is True
    assert cfg.is_owner("31699999999") is False


def test_add_remove_and_union(cfg):
    assert cfg.allowed_numbers() == {"31600000001"}          # env seed only
    cfg.add_allowed("+31 6 0000 0002", "wife")
    cfg.add_allowed("31600000003", "friend")
    assert cfg.allowed_numbers() == {"31600000001", "31600000002", "31600000003"}
    assert cfg.list_allowed()["31600000002"] == "wife"
    assert cfg.remove_allowed("31600000002") is True
    assert "31600000002" not in cfg.allowed_numbers()
    # env seed can't be removed at runtime
    assert cfg.remove_allowed("31600000001") is False
    assert "31600000001" in cfg.allowed_numbers()


def test_users_plugin_owner_only(cfg, monkeypatch):
    from famulus import context
    from famulus.builtin.users import UsersPlugin
    p = UsersPlugin()

    context.set_current_user("31699999999")   # not the owner
    with pytest.raises(ValueError):
        p.execute("allow_add", {"number": "31600000009"})
    assert p.is_gated("allow_add", {}) is False   # non-owner: no confirm dance

    context.set_current_user("31600000001")   # owner
    assert p.is_gated("allow_add", {}) is True
    r = p.execute("allow_add", {"number": "+31 6 0000 0009", "label": "test"})
    assert r["added"] == "31600000009"
    assert "31600000009" in cfg.allowed_numbers()
    context.set_current_user("")


def test_parse_admin_intent():
    from famulus.admin import parse_admin_intent as pa
    r = pa("add my wife to the bot, her number is +31 6 2468 1357")
    assert r == {"action": "add", "number": "31624681357", "label": "wife"}
    assert pa("allow 31633334444")["action"] == "add"
    assert pa("remove 31633334444 please")["action"] == "remove"
    assert pa("who can use the bot?")["action"] == "list"
    assert pa("list allowed users") == {"action": "list"}
    # no phone number → not an add/remove (don't hijack normal chat)
    assert pa("add milk to my shopping list") is None
    assert pa("what's the weather tomorrow") is None

"""Conversation-log → IRC mirror: gating, formatting, fail-soft."""
from famulus import chatlog, config


def test_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(chatlog, "URL", "")
    monkeypatch.setattr(chatlog, "TOKEN", "")
    sent = []
    monkeypatch.setattr(chatlog, "_send", lambda t: sent.append(t))
    chatlog.post("in", "316", "hallo")
    assert sent == []


def test_noop_when_conversation_logging_off(monkeypatch):
    monkeypatch.setattr(chatlog, "URL", "http://x/notify")
    monkeypatch.setattr(chatlog, "TOKEN", "tok")
    monkeypatch.setattr(config, "LOG_CONVERSATIONS", False)
    sent = []
    monkeypatch.setattr(chatlog, "_send", lambda t: sent.append(t))
    chatlog.post("in", "316", "hallo")
    assert sent == []


def test_posts_in_and_out_with_tools(monkeypatch):
    monkeypatch.setattr(chatlog, "URL", "http://x/notify")
    monkeypatch.setattr(chatlog, "TOKEN", "tok")
    monkeypatch.setattr(config, "LOG_CONVERSATIONS", True)
    sent = []
    monkeypatch.setattr(chatlog, "_send", lambda t: sent.append(t))
    chatlog.post("in", "31618337245", "geef me een les")
    chatlog.post("out", "31618337245", "Hier is je les", ["tutor_lesson"])
    assert sent[0].startswith("→ <31618337245> geef me een les")
    assert sent[1].startswith("← <31618337245> [tools: tutor_lesson] Hier is je les")


def test_send_errors_are_swallowed(monkeypatch):
    monkeypatch.setattr(chatlog, "URL", "http://x/notify")
    monkeypatch.setattr(chatlog, "TOKEN", "tok")
    monkeypatch.setattr(config, "LOG_CONVERSATIONS", True)

    def boom(t):
        raise OSError("bridge down")
    monkeypatch.setattr(chatlog, "_send", boom)
    chatlog.post("out", "316", "x")  # must not raise

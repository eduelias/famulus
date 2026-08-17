import asyncio

import pytest

from famulus import config, llm


def _backends(monkeypatch, value, default_model="qwen3:8b", url="http://primary:11434"):
    monkeypatch.setattr(config, "LLM_BACKENDS", value)
    monkeypatch.setattr(config, "MODEL_DEFAULT", default_model)
    monkeypatch.setattr(config, "OLLAMA_URL", url)
    return config.llm_backends()


def test_falls_back_to_single_backend(monkeypatch):
    assert _backends(monkeypatch, "") == [("http://primary:11434", "qwen3:8b")]


def test_parses_chain_and_strips_trailing_slash(monkeypatch):
    got = _backends(monkeypatch, "http://gpu:11434|big , http://pi:11434/|small")
    assert got == [("http://gpu:11434", "big"), ("http://pi:11434", "small")]


def test_backend_without_model_uses_default(monkeypatch):
    assert _backends(monkeypatch, "http://gpu:11434") == [("http://gpu:11434", "qwen3:8b")]


def test_chat_uses_second_backend_when_first_fails(monkeypatch):
    _backends(monkeypatch, "http://dead:11434|big,http://alive:11434|small")
    tried = []

    async def fake_post(url, model, messages, tools, fmt=""):
        tried.append((url, model))
        if "dead" in url:
            raise ConnectionError("boom")
        return {"content": "hello from " + model}

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    msg = asyncio.run(llm._chat([{"role": "user", "content": "hi"}], None))
    assert msg["content"] == "hello from small"
    assert tried == [("http://dead:11434", "big"), ("http://alive:11434", "small")]


def test_raises_when_every_backend_fails(monkeypatch):
    _backends(monkeypatch, "http://a:11434|m1,http://b:11434|m2")

    async def fake_post(url, model, messages, tools, fmt=""):
        raise ConnectionError("nope")

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    with pytest.raises(llm.NoBackendAvailable) as e:
        asyncio.run(llm._chat([], None))
    assert "http://a:11434" in str(e.value) and "http://b:11434" in str(e.value)


def test_model_override_applies_to_every_backend(monkeypatch):
    _backends(monkeypatch, "http://dead:11434|big,http://alive:11434|small")
    seen = []

    async def fake_post(url, model, messages, tools, fmt=""):
        seen.append(model)
        if "dead" in url:
            raise ConnectionError("boom")
        return {"content": "ok"}

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    asyncio.run(llm._chat([], None, model_override="coder:7b"))
    assert seen == ["coder:7b", "coder:7b"]


def test_model_override_falls_back_to_backend_default(monkeypatch):
    """A persona's preferred model is tried everywhere first; a backend that
    lacks it (404) still answers on its own default rather than failing."""
    _backends(monkeypatch, "http://gpu:11434|big,http://pi:11434|small")
    seen = []

    async def fake_post(url, model, messages, tools, fmt=""):
        seen.append((url, model))
        if model == "llama3.1:8b":       # not pulled on either host here
            raise RuntimeError("404 model not found")
        return {"content": "ok from " + model}

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    msg = asyncio.run(llm._chat([], None, model_override="llama3.1:8b"))
    assert msg["content"] == "ok from big"        # fell back to gpu's default
    assert seen == [("http://gpu:11434", "llama3.1:8b"),   # preferred, both hosts
                    ("http://pi:11434", "llama3.1:8b"),
                    ("http://gpu:11434", "big")]           # then defaults

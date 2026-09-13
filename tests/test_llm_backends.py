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


def _http_error(url, status):
    import httpx
    req = httpx.Request("POST", url)
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(status, request=req))


def test_chat_retries_same_backend_once_on_5xx(monkeypatch):
    """A crashed runner (GPU OOM → HTTP 500) is retried after a pause before failing over."""
    _backends(monkeypatch, "http://gpu:11434|big,http://pi:11434|small")
    monkeypatch.setattr(config, "LLM_RETRY_DELAY", 0)
    calls = []

    async def fake_post(url, model, messages, tools, fmt=""):
        calls.append((url, model))
        if len(calls) == 1:
            raise _http_error(url, 500)
        return {"content": "recovered on " + model}

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    msg = asyncio.run(llm._chat([{"role": "user", "content": "hi"}], None))
    assert msg["content"] == "recovered on big"
    assert calls == [("http://gpu:11434", "big"), ("http://gpu:11434", "big")]


def test_chat_does_not_retry_4xx(monkeypatch):
    _backends(monkeypatch, "http://gpu:11434|big,http://pi:11434|small")
    calls = []

    async def fake_post(url, model, messages, tools, fmt=""):
        calls.append(url)
        if "gpu" in url:
            raise _http_error(url, 400)
        return {"content": "small"}

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    msg = asyncio.run(llm._chat([{"role": "user", "content": "hi"}], None))
    assert msg["content"] == "small"
    assert calls == ["http://gpu:11434", "http://pi:11434"]


def test_chat_skips_backstop_for_oversized_requests(monkeypatch):
    """A research-sized context is not handed to the small backstop (it would only time out)."""
    _backends(monkeypatch, "http://gpu:11434|big,http://pi:11434|small")
    monkeypatch.setattr(config, "LLM_BACKSTOP_MAX_CHARS", 1000)
    calls = []

    async def fake_post(url, model, messages, tools, fmt=""):
        calls.append(url)
        raise ConnectionError("down")

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    big = [{"role": "user", "content": "x" * 5000}]
    with pytest.raises(llm.NoBackendAvailable) as e:
        asyncio.run(llm._chat(big, None))
    assert calls == ["http://gpu:11434"]
    assert "skipped" in str(e.value) and "http://pi:11434" in str(e.value)


def test_chat_backstop_still_used_for_small_requests(monkeypatch):
    _backends(monkeypatch, "http://gpu:11434|big,http://pi:11434|small")
    monkeypatch.setattr(config, "LLM_BACKSTOP_MAX_CHARS", 1000)

    async def fake_post(url, model, messages, tools, fmt=""):
        if "gpu" in url:
            raise ConnectionError("down")
        return {"content": "small answered"}

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    msg = asyncio.run(llm._chat([{"role": "user", "content": "short"}], None))
    assert msg["content"] == "small answered"


def test_run_agent_answers_from_gathered_results_when_rounds_run_out(monkeypatch):
    """Exhausting MAX_TOOL_ROUNDS asks for a best-effort answer instead of a canned refusal."""
    from famulus.plugins import Registry
    from famulus.plugins.base import BasePlugin, spec

    class Search(BasePlugin):
        name = "search"
        tools = [spec("web_search", "search", {"q": {"type": "string"}}, ["q"])]

        def execute(self, tool, args):
            return {"hits": [args["q"]]}

    reg = Registry([Search()])
    monkeypatch.setattr(config, "ROUTER_ENABLED", False)
    seen = []

    async def fake_chat(messages, tools, model_override="", fmt=""):
        seen.append(tools)
        if tools:  # keep asking for a new search every round
            n = len(seen)
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "web_search", "arguments": {"q": f"q{n}"}}}]}
        assert messages[-1]["role"] == "system" and "Answer the user now" in messages[-1]["content"]
        return {"role": "assistant", "content": "Here is what I found so far."}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    history = []
    reply, pending = asyncio.run(llm.run_agent(reg, history, "Research crispr"))
    assert reply == "Here is what I found so far."
    assert pending is None
    assert len(seen) == llm.MAX_TOOL_ROUNDS + 1 and seen[-1] is None

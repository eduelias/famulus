"""A failed agent turn leaves no half-finished messages in the sender's history."""
import asyncio

import pytest

from famulus import config, llm
from famulus import main as fmain

USER = "31600000042"


def _text(body, mid):
    return {"id": mid, "from": USER, "type": "text", "text": {"body": body}}


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setattr(config, "allowed_numbers", lambda: {USER})
    monkeypatch.setattr(config, "LOG_CONVERSATIONS", False)
    monkeypatch.setitem(fmain.histories, USER, [{"role": "system", "content": "sys"}])
    out = []

    async def fake_send(to, text):
        out.append(text)
        return True

    monkeypatch.setattr(fmain.wa, "send_text", fake_send)
    return out


def _agent_failing_after_a_tool_round(exc):
    async def run_agent(registry, history, text):
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "weather", "arguments": {}}}]})
        history.append({"role": "tool", "content": "{}"})
        raise exc
    return run_agent


@pytest.mark.parametrize("exc", [llm.NoBackendAvailable("all down"), RuntimeError("boom")])
def test_failed_turn_is_dropped_whole(monkeypatch, sent, exc):
    monkeypatch.setattr(llm, "run_agent", _agent_failing_after_a_tool_round(exc))
    asyncio.run(fmain._handle(_text("weather?", f"wamid.fail-{type(exc).__name__}")))
    assert fmain.histories[USER] == [{"role": "system", "content": "sys"}]
    assert len(sent) == 1 and sent[0].startswith("⚠️")

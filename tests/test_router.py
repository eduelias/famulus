"""Two-stage tool router: catalog/subset helpers + plugin selection."""
import asyncio

from famulus import llm
from famulus.plugins import BasePlugin, Registry, spec


class _Weather(BasePlugin):
    name = "weather"
    tools = [spec("get_weather", "current weather", {"city": {"type": "string"}}, ["city"])]

    def execute(self, tool, args):
        return "sunny"


class _Torrent(BasePlugin):
    name = "torrent"
    tools = [spec("qbt_stats", "seedbox stats", {}, []),
             spec("tl_ratio", "TL ratio", {}, [])]

    def execute(self, tool, args):
        return {}


def _reg():
    return Registry([_Weather(), _Torrent()])


def test_catalog_and_subset():
    r = _reg()
    cat = r.plugin_catalog()
    assert cat == {"weather": ["get_weather"], "torrent": ["qbt_stats", "tl_ratio"]}
    names = {t["function"]["name"] for t in r.tools_for(["torrent"])}
    assert names == {"qbt_stats", "tl_ratio"}
    assert r.tools_for(["nope"]) == []


def test_route_picks_subset(monkeypatch):
    r = _reg()

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        assert fmt == "json"          # router asks for JSON
        assert tools is None          # router itself uses no tools
        return {"content": '{"plugins": ["torrent"]}'}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    chosen = asyncio.run(llm._route(r, "what's my seedbox ratio"))
    assert chosen == {"torrent"}


def test_route_drops_unknown_and_empty(monkeypatch):
    r = _reg()

    async def only_unknown(messages, tools=None, model_override="", fmt=""):
        return {"content": '{"plugins": ["made_up"]}'}
    monkeypatch.setattr(llm, "_chat", only_unknown)
    assert asyncio.run(llm._route(r, "hi")) is None   # unknown filtered → None → all tools

    async def empty(messages, tools=None, model_override="", fmt=""):
        return {"content": '{"plugins": []}'}
    monkeypatch.setattr(llm, "_chat", empty)
    assert asyncio.run(llm._route(r, "hello there")) is None


def test_route_survives_bad_json(monkeypatch):
    r = _reg()

    async def garbage(messages, tools=None, model_override="", fmt=""):
        return {"content": "not json at all"}
    monkeypatch.setattr(llm, "_chat", garbage)
    assert asyncio.run(llm._route(r, "x")) is None    # never raises → falls back to all


def test_run_agent_narrows_tools(monkeypatch):
    """End to end: router picks torrent, so the agent turn only sees torrent tools."""
    r = _reg()
    monkeypatch.setattr("famulus.config.ROUTER_ENABLED", True)
    monkeypatch.setattr("famulus.config.ROUTER_MIN_TOOLS", 1)  # force routing on
    seen = {}

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        if fmt == "json":
            return {"content": '{"plugins": ["torrent"]}'}   # router stage
        seen["tools"] = [t["function"]["name"] for t in (tools or [])]
        return {"content": "ok"}                              # agent stage, no tool calls

    monkeypatch.setattr(llm, "_chat", fake_chat)
    reply, action = asyncio.run(llm.run_agent(r, [{"role": "system", "content": "s"}],
                                              "how's my seedbox"))
    assert reply == "ok" and action is None
    assert set(seen["tools"]) == {"qbt_stats", "tl_ratio"}   # weather was excluded

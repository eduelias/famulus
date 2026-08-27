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
    assert chosen == ["torrent"]


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


def test_route_resolves_tool_names_to_plugins(monkeypatch):
    """The 8B often returns tool names, not category names — resolve them."""
    r = _reg()

    async def toolnames(messages, tools=None, model_override="", fmt=""):
        return {"content": '{"plugins": ["tl_ratio", "qbt_stats"]}'}
    monkeypatch.setattr(llm, "_chat", toolnames)
    assert asyncio.run(llm._route(r, "seedbox?")) == ["torrent"]


class _Tutor(BasePlugin):
    name = "tutor"
    persona = "You are a warm Dutch tutor. Always reply in simple Dutch."
    tools = [spec("tutor_lesson", "next lesson", {}, [])]

    def context(self, user):
        return f"Learner {user}: level A2, weak on word order."

    def execute(self, tool, args):
        return "les"


def test_registry_persona_and_context():
    r = Registry([_Tutor(), _Weather()])
    assert "Dutch tutor" in r.persona_of("tutor")
    assert r.persona_of("weather") == ""            # no persona declared
    assert "level A2" in r.context_of("tutor", "31600000002")
    assert r.context_of("weather", "x") == ""


def test_persona_and_context_injected_into_system(monkeypatch):
    r = Registry([_Tutor(), _Weather()])
    monkeypatch.setattr("famulus.config.ROUTER_ENABLED", True)
    monkeypatch.setattr("famulus.config.ROUTER_MIN_TOOLS", 1)
    monkeypatch.setattr("famulus.context.current_user", lambda: "31600000002")
    seen = {}

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        if fmt == "json":
            return {"content": '{"plugins": ["tutor"]}'}
        seen["system"] = messages[0]["content"]
        seen["tools"] = [t["function"]["name"] for t in (tools or [])]
        return {"content": "hoi"}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    reply, _ = asyncio.run(llm.run_agent(r, [{"role": "system", "content": "base"}], "leer me Nederlands"))
    assert reply == "hoi"
    # the tutor persona + its per-user memory are in the turn's system prompt
    assert "Dutch tutor" in seen["system"]
    assert "level A2, weak on word order" in seen["system"]
    # and only the tutor's tools were exposed
    assert seen["tools"] == ["tutor_lesson"]


class _Coach(BasePlugin):
    name = "tutor"
    persona = "You are a warm Dutch tutor."
    model = "llama3.1:8b"          # a Dutch-strong model just for this domain
    tools = [spec("tutor_lesson", "next lesson", {}, [])]

    def execute(self, tool, args):
        return "les"


def test_registry_model_of():
    r = Registry([_Coach(), _Weather()])
    assert r.model_of("tutor") == "llama3.1:8b"
    assert r.model_of("weather") == ""          # no preferred model declared
    assert r.model_of("nope") == ""


def test_persona_model_used_for_primary(monkeypatch):
    """When the router makes the tutor primary, the agent turn runs on the
    tutor's preferred model; other domains keep the base model."""
    r = Registry([_Coach(), _Weather()])
    monkeypatch.setattr("famulus.config.ROUTER_ENABLED", True)
    monkeypatch.setattr("famulus.config.ROUTER_MIN_TOOLS", 1)
    monkeypatch.setattr("famulus.context.current_user", lambda: "u9")
    llm._last_primary.pop("u9", None)
    seen = {}

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        if fmt == "json":
            return {"content": '{"plugins": ["tutor"]}'}
        seen["model"] = model_override
        return {"content": "hoi"}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    asyncio.run(llm.run_agent(r, [{"role": "system", "content": "base"}],
                              "leer me Nederlands"))
    assert seen["model"] == "llama3.1:8b"


def test_no_persona_model_leaves_base_model(monkeypatch):
    r = Registry([_Weather(), _Torrent()])
    monkeypatch.setattr("famulus.config.ROUTER_ENABLED", True)
    monkeypatch.setattr("famulus.config.ROUTER_MIN_TOOLS", 1)
    monkeypatch.setattr("famulus.context.current_user", lambda: "u9")
    llm._last_primary.pop("u9", None)
    seen = {}

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        if fmt == "json":
            return {"content": '{"plugins": ["torrent"]}'}
        seen["model"] = model_override
        return {"content": "ok"}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    asyncio.run(llm.run_agent(r, [{"role": "system", "content": "base"}], "ratio?"))
    assert seen["model"] == ""          # torrent declares no model → base chain


def test_duplicate_tool_call_breaks_loop(monkeypatch):
    """A model retrying the identical call gets an answer-now nudge, not a re-run."""
    r = _reg()
    executed = []
    monkeypatch.setattr(r, "execute", lambda name, args: executed.append(name) or "0.5")
    monkeypatch.setattr("famulus.config.ROUTER_ENABLED", False)
    calls = {"n": 0}

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        calls["n"] += 1
        if calls["n"] <= 3:  # model stubbornly repeats the same call
            return {"tool_calls": [{"function": {"name": "tl_ratio", "arguments": {}}}]}
        return {"content": "final answer"}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    reply, _ = asyncio.run(llm.run_agent(r, [{"role": "system", "content": "s"}], "ratio?"))
    assert reply == "final answer"
    assert executed == ["tl_ratio"]          # executed ONCE, repeats were blocked


def test_recent_context_formats_last_turns():
    hist = [{"role": "system", "content": "s"},
            {"role": "user", "content": "leer me Nederlands"},
            {"role": "assistant", "content": "Hoi! Wat wil je leren?"},
            {"role": "user", "content": "hoe zeg je hond?"}]
    ctx = llm.recent_context(hist, turns=4)
    assert "system:" not in ctx                       # system excluded
    assert "leer me Nederlands" in ctx and "hoe zeg je hond" in ctx


def test_route_receives_conversation_context(monkeypatch):
    r = _reg()
    seen = {}

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        seen["prompt"] = messages[-1]["content"]
        return {"content": '{"plugins": ["torrent"]}'}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    asyncio.run(llm._route(r, "ja", recent="user: hoe is mijn ratio?", current_primary="torrent"))
    assert "Current active specialist: torrent" in seen["prompt"]
    assert "hoe is mijn ratio?" in seen["prompt"]


def test_sticky_primary_on_ambiguous_followup(monkeypatch):
    r = Registry([_Tutor(), _Torrent(), _Weather()])
    monkeypatch.setattr("famulus.config.ROUTER_ENABLED", True)
    monkeypatch.setattr("famulus.config.ROUTER_MIN_TOOLS", 1)
    monkeypatch.setattr("famulus.context.current_user", lambda: "u1")
    llm._last_primary.pop("u1", None)

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        if fmt == "json":
            # first message routes to tutor; the ambiguous follow-up routes to nothing
            if "leer me" in messages[-1]["content"].lower():
                return {"content": '{"plugins": ["tutor"]}'}
            return {"content": '{"plugins": []}'}
        return {"content": "ok"}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    hist = [{"role": "system", "content": "base"}]
    asyncio.run(llm.run_agent(r, hist, "leer me Nederlands"))
    assert llm._last_primary["u1"] == "tutor"
    # ambiguous "ja" -> router returns [] -> stays tutor (sticky)
    asyncio.run(llm.run_agent(r, hist, "ja"))
    assert llm._last_primary["u1"] == "tutor"
    assert "Dutch tutor" in hist[0]["content"]        # tutor persona still active

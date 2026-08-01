from famulus.plugins import Registry
from famulus.plugins.base import PLUGIN_API_VERSION, BasePlugin, spec


class Echo(BasePlugin):
    name = "echo"
    tools = [spec("echo_say", "say", {"text": {"type": "string"}}, ["text"]),
             spec("echo_publish", "publish", {"text": {"type": "string"}}, ["text"])]
    gated = {"echo_publish"}

    def execute(self, tool, args):
        return f"{tool}:{args['text']}"


class Stale(BasePlugin):
    name = "stale"
    api_version = PLUGIN_API_VERSION + 1
    tools = [spec("stale_tool", "x", {}, [])]


class Clash(BasePlugin):
    name = "clash"
    tools = [spec("echo_say", "collides", {}, [])]


def test_registry_merges_and_dispatches():
    reg = Registry([Echo()])
    assert [t["function"]["name"] for t in reg.tools] == ["echo_say", "echo_publish"]
    assert reg.execute("echo_say", {"text": "hi"}) == "echo_say:hi"


def test_gating_flags():
    reg = Registry([Echo()])
    assert not reg.is_gated("echo_say", {})
    assert reg.is_gated("echo_publish", {})
    assert "echo_publish" in reg.describe("echo_publish", {"text": "x"})


def test_api_version_mismatch_skipped():
    reg = Registry([Echo(), Stale()])
    assert "stale" not in reg.plugins


def test_tool_name_collision_skipped():
    reg = Registry([Echo(), Clash()])
    # clash plugin loads, but its colliding tool is dropped
    assert reg.execute("echo_say", {"text": "hi"}) == "echo_say:hi"
    names = [t["function"]["name"] for t in reg.tools]
    assert names.count("echo_say") == 1


def test_unknown_tool_raises():
    reg = Registry([Echo()])
    try:
        reg.execute("nope", {})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "unknown tool" in str(e)


def test_builtins_load():
    from famulus.plugins import load_registry
    reg = load_registry()
    assert {"weather", "web"} <= set(reg.plugins)
    assert {"weather_forecast", "web_search", "web_fetch"} <= {
        t["function"]["name"] for t in reg.tools}

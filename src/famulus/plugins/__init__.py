"""Plugin discovery and dispatch."""
import logging
from importlib.metadata import entry_points

from .base import PLUGIN_API_VERSION, BasePlugin, Plugin, spec  # noqa: F401

log = logging.getLogger("famulus")


class Registry:
    def __init__(self, plugins: list):
        self.plugins: dict[str, object] = {}
        self._tool_owner: dict[str, object] = {}
        for p in plugins:
            self._add(p)

    def _add(self, p) -> None:
        if getattr(p, "api_version", None) != PLUGIN_API_VERSION:
            log.warning("plugin %s targets API v%s, famulus speaks v%s — skipping",
                        getattr(p, "name", p), getattr(p, "api_version", "?"),
                        PLUGIN_API_VERSION)
            return
        if p.name in self.plugins:
            log.warning("duplicate plugin name %r — skipping the later one", p.name)
            return
        for t in p.tools:
            tool_name = t["function"]["name"]
            if tool_name in self._tool_owner:
                log.warning("tool %r from plugin %r collides with plugin %r — skipping tool",
                            tool_name, p.name, self._tool_owner[tool_name].name)
                continue
            self._tool_owner[tool_name] = p
        self.plugins[p.name] = p
        log.info("plugin loaded: %s (%d tools)", p.name, len(p.tools))

    @property
    def tools(self) -> list[dict]:
        return [t for p in self.plugins.values() for t in p.tools
                if self._tool_owner.get(t["function"]["name"]) is p]

    def _owner(self, tool: str):
        p = self._tool_owner.get(tool)
        if p is None:
            raise ValueError(f"unknown tool {tool!r}")
        return p

    def is_gated(self, tool: str, args: dict) -> bool:
        return self._owner(tool).is_gated(tool, args)

    def describe(self, tool: str, args: dict) -> str:
        return self._owner(tool).describe(tool, args)

    def execute(self, tool: str, args: dict) -> object:
        return self._owner(tool).execute(tool, args)


def load_registry() -> Registry:
    """Built-in plugins plus everything installed in the famulus.plugins group."""
    from ..builtin.weather import WeatherPlugin
    from ..builtin.web import WebPlugin

    plugins: list = [WeatherPlugin(), WebPlugin()]
    for ep in entry_points(group="famulus.plugins"):
        try:
            obj = ep.load()
            plugins.append(obj() if isinstance(obj, type) else obj)
        except Exception:
            log.exception("failed to load plugin entry point %r", ep.name)
    return Registry(plugins)

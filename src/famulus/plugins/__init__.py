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
    def document_handlers(self) -> list:
        """Plugins that opted into raw document messages (optional capability:
        ``wants_document(msg) -> bool`` and ``handle_document(msg) -> str``)."""
        return [p for p in self.plugins.values()
                if callable(getattr(p, "handle_document", None))
                and callable(getattr(p, "wants_document", None))]

    @property
    def tools(self) -> list[dict]:
        return [t for p in self.plugins.values() for t in p.tools
                if self._tool_owner.get(t["function"]["name"]) is p]

    def _own_tool_names(self, p) -> list[str]:
        return [t["function"]["name"] for t in p.tools
                if self._tool_owner.get(t["function"]["name"]) is p]

    def plugin_catalog(self) -> dict[str, list[str]]:
        """{plugin_name: [tool_names]} — the menu the router chooses from.
        Tool names alone route well (qbt_stats→torrent, ha_*→homeassistant)."""
        out: dict[str, list[str]] = {}
        for name, p in self.plugins.items():
            names = self._own_tool_names(p)
            if names:
                out[name] = names
        return out

    def tools_for(self, plugin_names) -> list[dict]:
        """Tool specs owned by the named plugins (for the narrowed agent turn)."""
        want = set(plugin_names)
        return [t for p in self.plugins.values() if p.name in want
                for t in p.tools if self._tool_owner.get(t["function"]["name"]) is p]

    def _owner(self, tool: str):
        p = self._tool_owner.get(tool)
        if p is None:
            raise ValueError(f"unknown tool {tool!r}")
        return p

    def is_gated(self, tool: str, args: dict) -> bool:
        return self._owner(tool).is_gated(tool, args)

    def describe(self, tool: str, args: dict) -> str:
        return self._owner(tool).describe(tool, args)

    def plugin_of(self, tool: str) -> str:
        return self._owner(tool).name

    def persona_of(self, name: str) -> str:
        p = self.plugins.get(name)
        return getattr(p, "persona", "") or "" if p else ""

    def context_of(self, name: str, user: str, history: list | None = None) -> str:
        p = self.plugins.get(name)
        if p is None or not callable(getattr(p, "context", None)):
            return ""
        try:
            try:
                return p.context(user, history) or ""
            except TypeError:  # older plugin with context(self, user) only
                return p.context(user) or ""
        except Exception:
            log.exception("context() failed in plugin %s", name)
            return ""

    def execute(self, tool: str, args: dict) -> object:
        p = self._owner(tool)
        # defense in depth: re-check access at execution time (covers the gated
        # confirm path and any bug that let a disallowed tool through).
        from .. import access, context
        if not access.user_allowed(context.current_user(), p.name):
            raise ValueError(f"you don't have access to {p.name} tools")
        return p.execute(tool, args)


def load_registry() -> Registry:
    """Built-in plugins plus everything installed in the famulus.plugins group."""
    from ..builtin.users import UsersPlugin
    from ..builtin.weather import WeatherPlugin
    from ..builtin.web import WebPlugin

    plugins: list = [WeatherPlugin(), WebPlugin(), UsersPlugin()]
    for ep in entry_points(group="famulus.plugins"):
        try:
            obj = ep.load()
            plugins.append(obj() if isinstance(obj, type) else obj)
        except Exception:
            log.exception("failed to load plugin entry point %r", ep.name)
    return Registry(plugins)

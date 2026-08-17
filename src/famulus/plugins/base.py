"""The famulus plugin contract.

A plugin is any object (typically an instance exported through the
``famulus.plugins`` entry-point group) with the attributes below. Tools are
described in OpenAI-style function-calling format so any capable model can
use them.

Gating: tools whose name is in ``gated`` — or for which ``is_gated`` returns
True for a specific call — are never executed directly. The owner receives a
human-readable description (from ``describe``) over WhatsApp and must reply
YES before ``execute`` runs.
"""
from typing import Protocol, runtime_checkable

PLUGIN_API_VERSION = 1


def spec(name: str, description: str, params: dict, required: list[str]) -> dict:
    """Helper to build one tool spec in function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": params, "required": required},
        },
    }


@runtime_checkable
class Plugin(Protocol):
    api_version: int          # set to PLUGIN_API_VERSION
    name: str                 # short id, e.g. "weather"
    tools: list[dict]         # list of spec() results
    gated: set[str]           # tool names that always need owner confirmation

    def is_gated(self, tool: str, args: dict) -> bool:
        """Return True if this specific call needs confirmation."""
        ...

    def describe(self, tool: str, args: dict) -> str:
        """Human-readable summary of a gated call, shown to the owner."""
        ...

    def execute(self, tool: str, args: dict) -> object:
        """Run the tool and return a JSON-serializable result."""
        ...


class BasePlugin:
    """Convenience base: subclass, set name/tools/gated, implement execute.

    Optional persona-router hooks:
      - ``persona``: a system-prompt fragment giving this domain its voice/behaviour
        (e.g. the Dutch tutor). Empty = no special persona.
      - ``context(user)``: dynamic memory/params to inject for this turn (the
        learner's level and current lesson, the seedbox's ratio, …). Empty = none.
      - ``model``: a preferred LLM for this domain, used only when the router makes
        this plugin the turn's primary (e.g. a Dutch-strong model for the tutor).
        Empty = use the normal backend chain. It's tried first across the backends
        and falls back to each backend's default model, so it never breaks failover.
    When the router picks this plugin as the turn's primary domain, the core
    composes: base safety rules + persona + context, exposes this domain's tools,
    and answers on ``model`` if set. Personas augment the base rules; they never
    replace the safety/gating.
    """

    api_version = PLUGIN_API_VERSION
    name = "unnamed"
    tools: list[dict] = []
    gated: set[str] = set()
    persona: str = ""
    model: str = ""

    def context(self, user: str, history: list | None = None) -> str:
        """Per-user memory/params to inject when this plugin is the primary.

        `history` is the conversation so far (excluding the incoming message), so a
        plugin can tell a fresh start from an ongoing session and inject
        accordingly (e.g. the tutor gives the full lesson at the start, then just
        'continue from the conversation, don't repeat' on follow-ups)."""
        return ""

    def is_gated(self, tool: str, args: dict) -> bool:
        return tool in self.gated

    def describe(self, tool: str, args: dict) -> str:
        return f"{tool} {args}"

    def execute(self, tool: str, args: dict) -> object:  # pragma: no cover
        raise NotImplementedError

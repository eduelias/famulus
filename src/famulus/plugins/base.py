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
    """Convenience base: subclass, set name/tools/gated, implement execute."""

    api_version = PLUGIN_API_VERSION
    name = "unnamed"
    tools: list[dict] = []
    gated: set[str] = set()

    def is_gated(self, tool: str, args: dict) -> bool:
        return tool in self.gated

    def describe(self, tool: str, args: dict) -> str:
        return f"{tool} {args}"

    def execute(self, tool: str, args: dict) -> object:  # pragma: no cover
        raise NotImplementedError

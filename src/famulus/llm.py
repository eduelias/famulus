"""Agent loop: Ollama chat with tool calling.

Gated tools interrupt the loop and return a PendingAction; the caller asks the
owner for confirmation and executes later.
"""
import json
from dataclasses import dataclass

import httpx

from . import config
from .plugins import Registry

MAX_TOOL_ROUNDS = 6


@dataclass
class PendingAction:
    tool: str
    args: dict
    description: str


async def _chat(messages: list[dict], tools: list[dict] | None, model: str) -> dict:
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{config.OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False,
                  "think": False, "keep_alive": "60m",
                  **({"tools": tools} if tools else {})},
        )
        r.raise_for_status()
        return r.json()["message"]


def _wants_coder(text: str) -> bool:
    return bool(config.MODEL_CODER) and text.lower().startswith(("code:", "/code"))


async def run_agent(registry: Registry, history: list[dict],
                    user_text: str) -> tuple[str, PendingAction | None]:
    """Returns (reply_text, pending_action_or_None). history is mutated in place."""
    if _wants_coder(user_text):
        history.append({"role": "user", "content": user_text})
        msg = await _chat(history, tools=None, model=config.MODEL_CODER)
        history.append(msg)
        return msg.get("content", ""), None

    history.append({"role": "user", "content": user_text})
    for _ in range(MAX_TOOL_ROUNDS):
        msg = await _chat(history, tools=registry.tools, model=config.MODEL_DEFAULT)
        history.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content", ""), None
        for call in calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args or "{}")
            if registry.is_gated(name, args):
                return "", PendingAction(name, args, registry.describe(name, args))
            try:
                result = registry.execute(name, args)
            except Exception as e:  # surface tool errors to the model
                result = {"error": str(e)}
            history.append({"role": "tool",
                            "content": json.dumps(result, default=str)[:12000]})
    return "I hit my tool-call limit for one message — try narrowing the request.", None

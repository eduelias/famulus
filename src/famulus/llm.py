"""Agent loop: Ollama chat with tool calling, over one or more backends.

Backends are tried in order, so a small always-on model (e.g. on the machine
running famulus) can answer when the fast GPU box is asleep or unreachable.
Gated tools interrupt the loop and return a PendingAction; the caller asks the
owner for confirmation and executes later.
"""
import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

from . import access, config, context
from .plugins import Registry

MAX_TOOL_ROUNDS = 6
log = logging.getLogger("famulus")

# last routed primary domain per user, so a conversation stays with its active
# specialist across short follow-ups (reset on process restart — harmless).
_last_primary: dict[str, str] = {}


class NoBackendAvailable(RuntimeError):
    """Every configured LLM backend failed to respond."""


@dataclass
class PendingAction:
    tool: str
    args: dict
    description: str


async def _post_chat(url: str, model: str, messages: list[dict],
                     tools: list[dict] | None, fmt: str = "") -> dict:
    # short connect timeout so failover is fast when a host is asleep and
    # silently drops packets (a refused port fails instantly; a dropped one
    # would otherwise hang for the full read timeout)
    limits = httpx.Timeout(config.LLM_TIMEOUT, connect=config.LLM_CONNECT_TIMEOUT)
    async with httpx.AsyncClient(timeout=limits) as client:
        r = await client.post(
            f"{url}/api/chat",
            json={"model": model, "messages": messages, "stream": False,
                  "think": False, "keep_alive": "60m",
                  "options": {"num_ctx": config.LLM_NUM_CTX},
                  **({"tools": tools} if tools else {}),
                  **({"format": fmt} if fmt else {})},
        )
        r.raise_for_status()
        return r.json()["message"]


async def _chat(messages: list[dict], tools: list[dict] | None,
                model_override: str = "", fmt: str = "") -> dict:
    """Try each backend in order; return the first successful reply.

    With a ``model_override`` (a persona's preferred model) it's tried on every
    backend first, then we fall back to each backend's own default model — so the
    preferred model is used where it exists but a host that lacks it (e.g. the
    small always-on backstop) still answers instead of failing the whole turn."""
    errors = []
    backends = config.llm_backends()
    size = sum(len(str(m.get("content") or "")) for m in messages)
    # first pass: the preferred model everywhere; second pass: backend defaults
    passes = [model_override, ""] if model_override else [""]
    tried: set[tuple[str, str]] = set()
    for pref in passes:
        for i, (url, model) in enumerate(backends):
            m = pref or model
            if (url, m) in tried:
                continue
            tried.add((url, m))
            if i and config.LLM_BACKSTOP_MAX_CHARS and size > config.LLM_BACKSTOP_MAX_CHARS:
                # don't hand a research-sized context to the small backstop: it would only
                # burn LLM_TIMEOUT and fail anyway, delaying the error the owner sees
                errors.append(f"{url} ({m}): skipped, {size} chars is too large for a backstop")
                log.warning("LLM backend %s model %s skipped — request too large (%d chars)",
                            url, m, size)
                continue
            try:
                return await _post_chat(url, m, messages, tools, fmt)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    # server-side crash (runner died, GPU OOM): Ollama reloads the model by
                    # itself, so one short pause and a retry usually gets the answer
                    log.warning("LLM backend %s model %s returned HTTP %d — retrying once",
                                url, m, e.response.status_code)
                    await asyncio.sleep(config.LLM_RETRY_DELAY)
                    try:
                        return await _post_chat(url, m, messages, tools, fmt)
                    except Exception as e2:
                        e = e2
                errors.append(f"{url} ({m}): {type(e).__name__}")
                log.warning("LLM backend %s model %s failed (%s) — trying next",
                            url, m, type(e).__name__)
            except Exception as e:  # connect error, timeout, 404 model missing, ...
                errors.append(f"{url} ({m}): {type(e).__name__}")
                log.warning("LLM backend %s model %s failed (%s) — trying next",
                            url, m, type(e).__name__)
    raise NoBackendAvailable("; ".join(errors))


_ROUTER_SYS = (
    "You are a router for an ongoing conversation. Given capability categories (each "
    "with its tool names), the recent conversation, and a new message, decide which "
    "categories the NEW message needs. Return JSON exactly as "
    '{"plugins": ["name", ...]} where each name is a CATEGORY name — the identifier '
    'before the colon (e.g. "tutor", "torrent"), NOT an individual tool name.\n'
    "CONTINUITY MATTERS: if a 'current specialist' is given and the new message is a "
    "short reply or a follow-up that continues that ongoing topic (e.g. 'ja', 'waarom?', "
    "a question about the current lesson), KEEP the current specialist first. Switch to a "
    "different category only when the new message clearly changes topic to it. Use an "
    "empty list only for pure small talk that needs no tools.")


def recent_context(history: list[dict], turns: int = 4) -> str:
    """The last few user/assistant lines (text only), oldest→newest, for the router."""
    lines = []
    for m in history:
        if m.get("role") in ("user", "assistant"):
            txt = m.get("content")
            if isinstance(txt, str) and txt.strip():
                lines.append(f"{m['role']}: {' '.join(txt.split())[:200]}")
    return "\n".join(lines[-turns:])


async def _route(registry: Registry, user_text: str, allowed: set[str] | None = None,
                 recent: str = "", current_primary: str = "") -> list[str] | None:
    """Ordered relevant plugins (first = primary), or None to use all tools.

    `allowed` restricts routing to the plugins the current user may use; `recent`
    and `current_primary` give the router conversation context so short follow-ups
    stay with the active specialist instead of mis-routing."""
    catalog = registry.plugin_catalog()
    if allowed is not None:
        catalog = {k: v for k, v in catalog.items() if k in allowed}
    if not catalog:
        return None
    menu = "\n".join(
        f"- {name}"
        + (f" ({registry.hint_of(name)})" if registry.hint_of(name) else "")
        + f": {', '.join(tools[:10])}"
        for name, tools in catalog.items())
    ctx = ""
    if recent:
        ctx += f"Recent conversation (oldest→newest):\n{recent}\n"
    if current_primary:
        ctx += f"Current active specialist: {current_primary}\n"
    user = (f"Categories (name: its tools):\n{menu}\n\n{ctx}\n"
            f"New message: {user_text!r}\n\nReturn the JSON now.")
    try:
        msg = await _chat([{"role": "system", "content": _ROUTER_SYS},
                           {"role": "user", "content": user}], tools=None, fmt="json")
        data = json.loads(msg.get("content") or "{}")
    except Exception as e:
        log.warning("router failed (%s) — using all tools", type(e).__name__)
        return None
    names = data.get("plugins") if isinstance(data, dict) else data
    if not isinstance(names, list):
        return None
    # small models often return tool names instead of the category — resolve
    # either form back to the owning plugin. Preserve order: the first is the
    # primary domain (drives the persona), the rest add their tools.
    tool_to_plugin = {t: name for name, tools in catalog.items() for t in tools}
    chosen: list[str] = []
    for n in names:
        plug = n if n in catalog else tool_to_plugin.get(n)
        if plug and plug not in chosen:
            chosen.append(plug)
    return chosen or None


def _wants_coder(text: str) -> bool:
    return bool(config.MODEL_CODER) and text.lower().startswith(("code:", "/code"))


async def _final_answer(history: list[dict], model_override: str, result: dict) -> str:
    """Let the model phrase a final tool result in the user's language, with no
    tools offered so the turn cannot continue; fall back to the tool's message."""
    history.append({"role": "system", "content": (
        "That tool result is final for this request. Tell the user what it says, "
        "in their language, briefly. Do not offer to do things you cannot do.")})
    msg = await _chat(history, tools=None, model_override=model_override)
    history.append(msg)
    return msg.get("content") or str(result.get("message", ""))


async def run_agent(registry: Registry, history: list[dict],
                    user_text: str) -> tuple[str, PendingAction | None]:
    """Returns (reply_text, pending_action_or_None). history is mutated in place."""
    if _wants_coder(user_text):
        history.append({"role": "user", "content": user_text})
        msg = await _chat(history, tools=None, model_override=config.MODEL_CODER)
        history.append(msg)
        return msg.get("content", ""), None

    # Access control: restrict to the domains this user may use, so a restricted
    # user's model never even sees tools outside their grants.
    user = context.current_user()
    allowed = access.allowed_plugins(user, registry.plugins.keys())
    tools = registry.tools_for(allowed)

    # Persona routing: the pre-read picks the domains this message needs (ordered,
    # first = primary), narrows the tools, AND sets the responding persona +
    # injects that persona's memory. It's given the recent conversation + the
    # active specialist so a short follow-up ('ja', 'waarom?') stays in the same
    # persona instead of mis-routing. Falls back to the plain assistant + the
    # user's full allowed toolset when routing is off/small/unsure.
    primary = ""
    if config.ROUTER_ENABLED and len(tools) > config.ROUTER_MIN_TOOLS:
        was = _last_primary.get(user, "")
        chosen = await _route(registry, user_text, allowed,
                              recent=recent_context(history),
                              current_primary=was if was in allowed else "")
        sel = [p for p in (chosen or []) if p in allowed]
        if not sel and was in allowed:
            sel = [was]  # sticky: keep the active specialist on an ambiguous turn
        if sel:
            primary = sel[0]
            narrowed = registry.tools_for(set(sel))
            if narrowed:
                tools = narrowed
            _last_primary[user] = primary
            log.info("router: primary=%s (was %s) tools=%d/%d", primary, was or "-",
                     len(tools), len(registry.tools_for(allowed)))

    # compose this turn's system prompt: safety base + primary persona + its memory,
    # and answer on the persona's preferred model if it declares one.
    import datetime as _dt
    sys = config.SYSTEM_PROMPT + _dt.datetime.now().strftime(
        "\n\nToday is %A, %Y-%m-%d.")
    model_override = ""
    if primary:
        persona = registry.persona_of(primary)
        ctx = registry.context_of(primary, user, history)  # history: start vs continue
        model_override = registry.model_of(primary)
        if persona:
            sys += "\n\n# Active specialist\n" + persona
        if ctx:
            sys += "\n\n# Context for this conversation\n" + ctx
        if model_override:
            log.info("router: primary=%s using model %s", primary, model_override)
    if history and history[0].get("role") == "system":
        history[0]["content"] = sys
    else:
        history.insert(0, {"role": "system", "content": sys})

    history.append({"role": "user", "content": user_text})
    seen_calls: set[str] = set()
    for _ in range(MAX_TOOL_ROUNDS):
        msg = await _chat(history, tools=tools, model_override=model_override)
        history.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content", ""), None
        for call in calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args or "{}")
            # anti-loop: a model retrying the exact same call won't get new data —
            # tell it to answer with what it already has instead of burning rounds.
            sig = name + json.dumps(args, sort_keys=True, default=str)
            if sig in seen_calls:
                history.append({"role": "tool", "content": json.dumps({
                    "error": "You already made this exact call and have its result. "
                             "Do not call it again — answer the user now using the "
                             "previous result."})})
                continue
            seen_calls.add(sig)
            if registry.is_gated(name, args):
                return "", PendingAction(name, args, registry.describe(name, args))
            try:
                result = registry.execute(name, args)
            except Exception as e:  # surface tool errors to the model
                result = {"error": str(e)}
            history.append({"role": "tool",
                            "content": json.dumps(result, default=str)[:12000]})
            if isinstance(result, dict) and result.get("final"):
                # the tool says this request is settled (e.g. "nobody by that name"):
                # small models ignore "don't call again" in the payload and improvise
                # a second, wrong search — so end the turn here, tools withheld.
                return await _final_answer(history, model_override, result), None
    # Out of tool rounds. A canned refusal throws away everything the tools already
    # fetched (the owner reads it as "never answered"), so ask for a best-effort answer
    # from the gathered results instead — with tools withheld so the loop must end.
    history.append({"role": "system", "content": (
        "You have used all tool calls available for this message. Answer the user now, "
        "as completely as you can, from the results you already have. Briefly say what "
        "you could not verify.")})
    msg = await _chat(history, tools=None, model_override=model_override)
    history.append(msg)
    return (msg.get("content") or
            "I hit my tool-call limit for one message — try narrowing the request."), None

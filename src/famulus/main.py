"""WhatsApp Cloud API webhook + agent orchestration."""
import hashlib
import hmac
import logging

from fastapi import FastAPI, Request, Response

from . import config, context, llm, wa
from .plugins import load_registry

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("famulus")
app = FastAPI(title="famulus")
registry = load_registry()

# per-sender state (single process; see README limitations)
histories: dict[str, list[dict]] = {}
pending: dict[str, llm.PendingAction] = {}
seen_ids: set[str] = set()

if not config.WA_APP_SECRET and not config.WA_ALLOW_UNSIGNED:
    log.critical(
        "WA_APP_SECRET is not set. Incoming webhooks cannot be verified and "
        "will be REJECTED. Set WA_APP_SECRET (Meta app dashboard -> App "
        "settings -> Basic) or, for local development only, WA_ALLOW_UNSIGNED=true."
    )


def _signature_ok(raw: bytes, header: str | None) -> bool:
    if config.WA_ALLOW_UNSIGNED:
        return True
    if not config.WA_APP_SECRET or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(config.WA_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


@app.get("/webhook")
async def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == config.WA_VERIFY_TOKEN:
        return Response(q.get("hub.challenge", ""), media_type="text/plain")
    return Response("forbidden", status_code=403)


@app.get("/health")
async def health():
    return {"ok": True, "plugins": sorted(registry.plugins),
            "tools": len(registry.tools)}


@app.post("/webhook")
async def receive(request: Request):
    raw = await request.body()
    if not _signature_ok(raw, request.headers.get("X-Hub-Signature-256")):
        log.warning("rejected webhook with missing/invalid signature")
        return Response("invalid signature", status_code=403)
    import json as _json
    body = _json.loads(raw or b"{}")
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []) or []:
                await _handle(msg)
    return {"status": "ok"}


async def _handle(msg: dict) -> None:
    mid, sender = msg.get("id"), msg.get("from", "")
    if mid in seen_ids:  # Meta retries webhooks; dedupe
        return
    seen_ids.add(mid)
    if config._norm_number(sender) not in config.allowed_numbers():
        log.warning("ignoring message from non-allowlisted %s", sender)
        return
    # expose the sender to plugins (per-user state, owner checks) for this message
    context.set_current_user(config._norm_number(sender))
    if msg.get("type") == "document":
        for p in registry.document_handlers:
            try:
                if not p.wants_document(msg):
                    continue
            except Exception:
                log.exception("wants_document failed in plugin %s", p.name)
                continue
            await wa.send_text(sender, "📄 Received — processing…")
            try:
                import asyncio
                reply = await asyncio.to_thread(p.handle_document, msg)
                if reply:
                    await wa.send_text(sender, reply)
            except Exception as e:
                log.exception("document handler %s failed", p.name)
                await wa.send_text(sender, f"⚠️ {p.name} could not process that file: {e}")
            return
        return
    if msg.get("type") != "text":
        return
    text = msg["text"]["body"].strip()

    # confirmation flow for gated actions
    if sender in pending:
        action = pending.pop(sender)
        if text.lower() in config.CONFIRM_WORDS:
            try:
                result = registry.execute(action.tool, action.args)
                await wa.send_text(sender, f"Done: {result}")
            except Exception as e:
                await wa.send_text(sender, f"Failed: {e}")
        elif text.lower() in config.CANCEL_WORDS:
            await wa.send_text(sender, "Cancelled.")
        else:
            pending[sender] = action
            await wa.send_text(
                sender, "Reply YES to execute or NO to cancel:\n\n" + action.description)
        return

    history = histories.setdefault(
        sender, [{"role": "system", "content": config.SYSTEM_PROMPT}])
    if len(history) > 40:  # keep context bounded
        del history[1:-20]
    try:
        reply, action = await llm.run_agent(registry, history, text)
    except llm.NoBackendAvailable as e:
        # don't leak a stack trace to WhatsApp; the owner can't act on it
        log.error("no LLM backend reachable: %s", e)
        history.pop()  # the question was never answered; don't poison context
        await wa.send_text(
            sender,
            "⚠️ I can't reach my language model right now, so I couldn't answer "
            "that. Check that your LLM server is running, then try again.")
        return
    except Exception:
        log.exception("agent error")
        await wa.send_text(
            sender, "⚠️ Something went wrong handling that message. "
                    "The details are in my logs.")
        return
    if action:
        pending[sender] = action
        await wa.send_text(
            sender,
            "⚠️ Confirmation needed — reply YES to execute, NO to cancel:\n\n"
            + action.description)
    elif reply:
        await wa.send_text(sender, reply)

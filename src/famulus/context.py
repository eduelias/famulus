"""Who is the bot talking to *right now*.

famulus handles one WhatsApp sender at a time, but plugins receive only their
tool arguments — not the sender. This exposes the current sender to plugins
(and to per-user state) without threading it through every ``execute`` call, via
a context variable set for the duration of handling one message.

asyncio copies the context per task, and ``asyncio.to_thread`` copies it into
the worker thread, so a value set in ``_handle`` is visible to synchronous tool
execution and document handlers for that message — and isolated from other
concurrently-handled messages.
"""
from __future__ import annotations

import contextvars

_current_user: contextvars.ContextVar[str] = contextvars.ContextVar(
    "famulus_current_user", default="")


def set_current_user(number: str) -> None:
    _current_user.set(number or "")


def current_user() -> str:
    """The E.164 (no '+') number of the sender being handled, or '' outside a
    request (e.g. cron/CLI)."""
    return _current_user.get()


_current_history: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "famulus_current_history", default=None)


def set_current_history(history: list | None) -> None:
    _current_history.set(history)


def current_history() -> list:
    """The conversation being answered (system/user/assistant/tool messages, the
    incoming user message last), so a tool can quote what the user actually wrote
    instead of the model's paraphrase. [] outside an agent turn."""
    return _current_history.get() or []

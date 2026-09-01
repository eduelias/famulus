"""Unclaimed documents and unsupported message types must never go silent."""
import asyncio

from famulus import config, llm
from famulus import main as fmain
from famulus.main import _document_note


DOC_MSG = {
    "id": "wamid.test-doc-1",
    "from": "31600000001",
    "type": "document",
    "document": {"id": "MEDIA123", "filename": "report.pdf",
                 "mime_type": "application/pdf",
                 "caption": "email this to lu7@msn.com"},
}


def test_document_note_includes_handle_and_caption():
    note = _document_note(DOC_MSG)
    assert "report.pdf" in note
    assert "MEDIA123" in note
    assert "application/pdf" in note
    assert "email this to lu7@msn.com" in note


def test_document_note_without_caption_asks():
    msg = {"document": {"id": "M1", "filename": "a.pdf", "mime_type": "application/pdf"}}
    note = _document_note(msg)
    assert "M1" in note
    assert "ask the user" in note


def _allow(monkeypatch, sender: str):
    monkeypatch.setattr(config, "allowed_numbers", lambda: {sender})


def test_unclaimed_document_reaches_the_agent(monkeypatch):
    sender = "31600000001"
    _allow(monkeypatch, sender)
    seen = {}

    async def fake_agent(registry, history, text):
        seen["text"] = text
        return "on it", None

    sent = []

    async def fake_send(to, text):
        sent.append((to, text))
        return True

    monkeypatch.setattr(llm, "run_agent", fake_agent)
    monkeypatch.setattr(fmain.wa, "send_text", fake_send)
    asyncio.run(fmain._handle(dict(DOC_MSG)))
    assert "MEDIA123" in seen["text"]
    assert "email this to lu7@msn.com" in seen["text"]
    assert sent == [(sender, "on it")]


def test_unsupported_type_gets_a_reply(monkeypatch):
    sender = "31600000002"
    _allow(monkeypatch, sender)
    sent = []

    async def fake_send(to, text):
        sent.append((to, text))
        return True

    monkeypatch.setattr(fmain.wa, "send_text", fake_send)
    asyncio.run(fmain._handle(
        {"id": "wamid.test-sticker-1", "from": sender, "type": "sticker"}))
    assert len(sent) == 1
    assert "sticker" in sent[0][1]

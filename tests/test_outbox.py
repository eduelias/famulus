"""Plugin-raised messages and owner approvals, owner-only/aliased domains, verbatim
results and the single-domain specialist."""
import asyncio

import pytest

from famulus import access, config, context, llm, outbox
from famulus import main as fmain
from famulus.plugins import BasePlugin, Registry, spec

OWNER, OTHER = "31600000001", "5511999990000"


class _Admin(BasePlugin):
    name = "padmin"
    owner_only = True
    tools = [spec("build_it", "build a request", {"n": {"type": "integer"}}, ["n"])]
    gated = {"build_it"}

    def __init__(self):
        self.runs = []

    def execute(self, tool, args):
        self.runs.append((context.current_user(), args["n"]))
        return f"building #{args['n']}"


class _Intake(BasePlugin):
    name = "intake"
    domain_aliases = ("pedidos",)
    persona = "Fale português."
    tools = [spec("file_it", "file a request", {"title": {"type": "string"}}, ["title"])]

    def execute(self, tool, args):
        said = [m["content"] for m in context.current_history() if m.get("role") == "user"]
        outbox.ask_owner("build_it", {"n": 7}, f"Build #7 ({args['title']})? {said[-1]}")
        return {"final": True, "verbatim": "Ok, pedido registrado (#7)."}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "GRANTS_FILE", str(tmp_path / "grants.json"))
    monkeypatch.setattr(access, "OWNER_ONLY", set(access.OWNER_ONLY))
    monkeypatch.setattr(access, "DOMAIN_ALIASES", dict(access.DOMAIN_ALIASES))
    monkeypatch.setattr(config, "OWNER_WA_NUMBER", OWNER)
    monkeypatch.setattr(config, "allowed_numbers", lambda: {OWNER, OTHER})
    admin = _Admin()
    reg = Registry([admin, _Intake()])
    monkeypatch.setattr(fmain, "registry", reg)
    monkeypatch.setattr(fmain, "pending", {})
    monkeypatch.setattr(fmain, "histories", {})
    monkeypatch.setattr(fmain, "owner_queue", fmain.deque())
    outbox.drain()
    sent = []

    async def fake_send(to, text):
        sent.append((to, text))
        return True

    async def fake_template(to, name, params, lang="en"):
        sent.append((to, f"template:{name}:{lang}:{'|'.join(params)}"))
        return True

    monkeypatch.setattr(fmain.wa, "send_text", fake_send)
    monkeypatch.setattr(fmain.wa, "send_template", fake_template)
    return {"admin": admin, "reg": reg, "sent": sent}


_n = iter(range(10_000))


def _msg(sender, text):
    return {"id": f"wamid.outbox-{next(_n)}", "from": sender, "type": "text",
            "text": {"body": text}}


def test_owner_only_and_aliases_register(env):
    assert "padmin" in access.OWNER_ONLY
    assert access.resolve_domains("only pedidos") == ["intake"]
    assert access.set_grants(OTHER, "intake, padmin") == ["intake"]   # owner-only dropped
    assert access.allowed_plugins(OTHER, env["reg"].plugins) == {"intake"}
    assert access.user_allowed(OTHER, "padmin") is False


def test_texts_and_templates_are_sent(env):
    outbox.send_text(OTHER, "oi")
    outbox.send_template(OTHER, "pauta_live", ["12", "1.3.0"], lang="pt_BR")
    asyncio.run(fmain.flush_outbox())
    assert env["sent"] == [(OTHER, "oi"), (OTHER, "template:pauta_live:pt_BR:12|1.3.0")]


def test_requester_files_owner_approves(env, monkeypatch):
    """The requester's turn raises an approval that goes to the OWNER, not to them."""
    access.set_grants(OTHER, "pedidos")
    seen = {}

    async def fake_chat(messages, tools=None, model_override="", fmt=""):
        seen["system"] = messages[0]["content"]
        seen["tools"] = [t["function"]["name"] for t in tools or []]
        return {"content": "", "tool_calls": [
            {"function": {"name": "file_it", "arguments": {"title": "filtro"}}}]}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    asyncio.run(fmain._handle(_msg(OTHER, "quero um filtro de clientes")))
    assert seen["tools"] == ["file_it"]                 # never sees build_it
    assert "Fale português." in seen["system"]          # the one domain is the specialist
    assert (OTHER, "Ok, pedido registrado (#7).") in env["sent"]
    assert OTHER not in fmain.pending                   # the requester is never asked
    owner_prompt = [t for to, t in env["sent"] if to == OWNER]
    assert len(owner_prompt) == 1 and "Build #7 (filtro)" in owner_prompt[0]
    assert "quero um filtro de clientes" in owner_prompt[0]   # the user's own words

    asyncio.run(fmain._handle(_msg(OWNER, "SIM")))
    assert env["admin"].runs == [(OWNER, 7)]
    assert (OWNER, "Done: building #7") in env["sent"]


def test_requester_cannot_confirm_owner_action(env, monkeypatch):
    async def fake_agent(registry, history, text):
        return "?", None

    monkeypatch.setattr(llm, "run_agent", fake_agent)
    access.set_grants(OTHER, "intake")
    outbox.ask_owner("build_it", {"n": 3}, "Build #3?")
    asyncio.run(fmain._handle(_msg(OTHER, "yes")))       # goes to the agent, not the gate
    assert env["admin"].runs == []
    assert OWNER in fmain.pending and OTHER not in fmain.pending


def test_approvals_queue_one_at_a_time(env):
    outbox.ask_owner("build_it", {"n": 1}, "Build #1?")
    outbox.ask_owner("build_it", {"n": 2}, "Build #2?")
    asyncio.run(fmain.flush_outbox())
    assert [t for _, t in env["sent"]][-1].endswith("Build #1?")
    asyncio.run(fmain._handle(_msg(OWNER, "não")))
    assert (OWNER, "Cancelled.") in env["sent"]
    assert env["sent"][-1][1].endswith("Build #2?")
    asyncio.run(fmain._handle(_msg(OWNER, "yes")))
    assert env["admin"].runs == [(OWNER, 2)]
    assert not fmain.owner_queue and OWNER not in fmain.pending


def test_other_reply_parks_the_approval(env, monkeypatch):
    """A non-YES/NO reply is handled normally; the approval is asked again after."""
    async def fake_agent(registry, history, text):
        return f"answer to {text}", None

    monkeypatch.setattr(llm, "run_agent", fake_agent)
    outbox.ask_owner("build_it", {"n": 5}, "Build #5?")
    asyncio.run(fmain.flush_outbox())
    asyncio.run(fmain._handle(_msg(OWNER, "what's the weather?")))
    assert env["admin"].runs == []
    texts = [t for _, t in env["sent"]]
    assert "answer to what's the weather?" in texts
    assert texts[-1].endswith("Build #5?")                # asked again after the answer
    assert fmain.pending[OWNER].args == {"n": 5}


def test_owner_turn_gated_action_is_not_parked(env, monkeypatch):
    """The owner's own gated actions keep the old behaviour: re-asked, never parked."""
    fmain.pending[OWNER] = llm.PendingAction("build_it", {"n": 9}, "Build #9?")
    asyncio.run(fmain._handle(_msg(OWNER, "hmm")))
    assert env["sent"][-1][1].startswith("Reply YES to execute or NO to cancel")
    assert not fmain.owner_queue

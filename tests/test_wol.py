"""famulus.wol: wake a sleeping backend host before the first request. No sockets, no
clock, no network — everything is monkeypatched."""
import asyncio
import socket

import pytest

from famulus import llm, wol

HOST, MAC = "192.168.2.242", "30:9c:23:b4:9b:55"
URL = f"http://{HOST}:11434"


class FakeSocket:
    """Records sendto() calls; stands in for the UDP socket."""
    sent: list[tuple[bytes, tuple[str, int]]] = []
    opts: list[tuple] = []

    def __init__(self, *a, **k):
        pass

    def setsockopt(self, *a):
        FakeSocket.opts.append(a)

    def sendto(self, data, addr):
        FakeSocket.sent.append((data, addr))

    def close(self):
        pass


@pytest.fixture
def wol_env(monkeypatch):
    """A known host, a fake socket, a fake clock, no sleeping. Returns the clock."""
    FakeSocket.sent.clear()
    FakeSocket.opts.clear()
    clock = {"t": 1000.0}
    monkeypatch.setattr(wol, "WOL_HOSTS", f"{HOST}={MAC}")
    monkeypatch.setattr(wol, "_last_wake", {})
    monkeypatch.setattr(wol, "_seen_up", {})
    monkeypatch.setattr(wol, "_udp_socket", FakeSocket)
    monkeypatch.setattr(wol, "_now", lambda: clock["t"])
    monkeypatch.setattr(wol, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    return clock


def test_hosts_parses_pairs_and_skips_junk(monkeypatch):
    monkeypatch.setattr(wol, "WOL_HOSTS",
                        f" {HOST}={MAC} , 10.0.0.9=AA-BB-CC-DD-EE-FF, bogus, 10.0.0.1=zz")
    assert wol.hosts() == {HOST: "309c23b49b55", "10.0.0.9": "aabbccddeeff"}


def test_hosts_empty_by_default(monkeypatch):
    monkeypatch.setattr(wol, "WOL_HOSTS", "")
    assert wol.hosts() == {}


def test_magic_packet_layout():
    pkt = wol.magic_packet(MAC)
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:] == bytes.fromhex("309c23b49b55") * 16


def test_probe_url_by_service():
    assert wol.probe_url("http://gpu:11434") == "http://gpu:11434/api/tags"
    assert wol.probe_url("http://gpu:8188/") == "http://gpu:8188/system_stats"
    assert wol.probe_url("http://gpu:9000/x") == "http://gpu:9000/"


def test_send_wake_unicasts_and_broadcasts(wol_env):
    wol.send_wake(HOST, MAC, port=9)
    assert [addr for _, addr in FakeSocket.sent] == [(HOST, 9), ("255.255.255.255", 9)]
    assert all(data == wol.magic_packet(MAC) for data, _ in FakeSocket.sent)
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in FakeSocket.opts


def test_send_wake_survives_a_socket_error(wol_env, monkeypatch):
    def boom(self, data, addr):
        raise OSError("network is unreachable")
    monkeypatch.setattr(FakeSocket, "sendto", boom)
    wol.send_wake(HOST, MAC)  # logs, does not raise


def test_unknown_host_is_neither_probed_nor_woken(wol_env, monkeypatch):
    monkeypatch.setattr(wol, "is_up", lambda url, timeout=0: pytest.fail("probed"))
    assert wol.ensure_awake("http://192.168.2.68:11434") is True
    assert FakeSocket.sent == []


def test_awake_host_gets_no_packet(wol_env, monkeypatch):
    monkeypatch.setattr(wol, "is_up", lambda url, timeout=0: True)
    assert wol.ensure_awake(URL) is True
    assert FakeSocket.sent == []


def test_sleeping_host_is_woken_and_polled_until_up(wol_env, monkeypatch):
    probes = []

    def is_up(url, timeout=0):
        probes.append(wol_env["t"])
        return len(probes) >= 4  # up on the third poll after the first probe
    monkeypatch.setattr(wol, "is_up", is_up)
    assert wol.ensure_awake(URL, wait=60) is True
    assert len(FakeSocket.sent) == 2  # one wake: unicast + broadcast
    assert probes[0] == 1000.0 and probes[-1] == 1000.0 + 3 * wol.POLL_INTERVAL


def test_gives_up_after_wait(wol_env, monkeypatch):
    monkeypatch.setattr(wol, "is_up", lambda url, timeout=0: False)
    assert wol.ensure_awake(URL, wait=10) is False
    assert wol_env["t"] == pytest.approx(1010.0)
    assert len(FakeSocket.sent) == 2


def test_no_second_packet_inside_the_cooldown(wol_env, monkeypatch):
    monkeypatch.setattr(wol, "is_up", lambda url, timeout=0: False)
    monkeypatch.setattr(wol, "WOL_COOLDOWN", 180.0)
    assert wol.ensure_awake(URL, wait=10) is False
    assert len(FakeSocket.sent) == 2
    wol_env["t"] += 30          # still cooling down, and past the wait: fail fast
    assert wol.ensure_awake(URL, wait=10) is False
    assert len(FakeSocket.sent) == 2
    assert wol_env["t"] == pytest.approx(1040.0)   # no waiting either
    wol_env["t"] += 200         # cooldown over: wake again
    assert wol.ensure_awake(URL, wait=10) is False
    assert len(FakeSocket.sent) == 4


def test_second_caller_waits_for_the_wake_in_flight(wol_env, monkeypatch):
    """Two calls shortly after each other (router + chat) share one magic packet: the
    second waits for the remaining budget of the first wake."""
    monkeypatch.setattr(wol, "is_up", lambda url, timeout=0: False)
    wol._last_wake[HOST] = 1000.0 - 20  # a wake 20 s ago, still inside wait=60
    assert wol.ensure_awake(URL, wait=60) is False
    assert FakeSocket.sent == []
    assert wol_env["t"] == pytest.approx(1040.0)  # waited to the 60 s deadline only


def test_recently_seen_up_host_is_not_probed_again(wol_env, monkeypatch):
    calls = []
    monkeypatch.setattr(wol, "is_up", lambda url, timeout=0: calls.append(url) or True)
    assert wol.ensure_awake(URL) is True
    assert wol.ensure_awake(URL) is True
    assert calls == [URL]
    wol_env["t"] += wol.UP_TTL + 1
    assert wol.ensure_awake(URL) is True
    assert calls == [URL, URL]


def test_chat_skips_a_backend_that_stays_asleep(monkeypatch, wol_env):
    from famulus import config
    monkeypatch.setattr(config, "LLM_BACKENDS", f"{URL}|big,http://pi:11434|small")
    monkeypatch.setattr(wol, "is_up", lambda url, timeout=0: False)
    tried = []

    async def fake_post(url, model, messages, tools, fmt=""):
        tried.append(url)
        return {"content": "hi from " + model}
    monkeypatch.setattr(llm, "_post_chat", fake_post)
    monkeypatch.setattr(wol, "WOL_WAIT", 5.0)
    msg = asyncio.run(llm._chat([{"role": "user", "content": "hi"}], None))
    assert msg["content"] == "hi from small"
    assert tried == ["http://pi:11434"]       # the GPU box was never asked
    assert len(FakeSocket.sent) == 2          # but it was woken


def test_chat_uses_the_backend_once_it_wakes(monkeypatch, wol_env):
    from famulus import config
    monkeypatch.setattr(config, "LLM_BACKENDS", f"{URL}|big,http://pi:11434|small")
    n = {"probes": 0}

    def is_up(url, timeout=0):
        n["probes"] += 1
        return n["probes"] > 2
    monkeypatch.setattr(wol, "is_up", is_up)

    async def fake_post(url, model, messages, tools, fmt=""):
        return {"content": "hi from " + model}
    monkeypatch.setattr(llm, "_post_chat", fake_post)
    msg = asyncio.run(llm._chat([{"role": "user", "content": "hi"}], None))
    assert msg["content"] == "hi from big"
    assert len(FakeSocket.sent) == 2

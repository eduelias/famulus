"""Wake-on-LAN for backends that live on a machine that sleeps.

A GPU box that suspends after some idle time saves a lot of power, but every backend on
it (Ollama, ComfyUI) then looks dead and famulus silently fails over to its small backstop
model, or the tool errors. This module wakes the box first.

Configure which hosts can be woken, as ``ip=mac`` pairs (hosts not listed are never probed
or woken, so this costs nothing for always-on backends)::

    WOL_HOSTS=192.168.2.242=30:9c:23:b4:9b:55

``ensure_awake(url)`` is called right before the first request to a backend. It probes
the service with a ~2 s HTTP GET (``/api/tags`` for Ollama, ``/system_stats`` for ComfyUI,
``/`` otherwise); if nothing answers it sends a magic packet and polls the same probe for
up to ``WOL_WAIT`` seconds (60) before giving up, in which case the caller falls back as
before. A host is woken at most once per ``WOL_COOLDOWN`` seconds (180): a second caller
inside that window waits for the first wake instead of sending another packet, and a host
that did not come up is not re-tried until the cooldown is over.

The packet goes as UDP to port ``WOL_PORT`` (9) both **unicast to the host's IP** and to
the limited broadcast 255.255.255.255. From a container on a Docker bridge network only
the unicast leaves the machine (the kernel does not forward broadcasts), and it needs the
host's MAC to be in the sender's ARP table: a sleeping NIC usually still answers ARP, a
hibernated or shut-down one does not, in which case a static neighbour entry on the
Docker host (``ip neigh replace <ip> lladdr <mac> dev eth0 nud permanent``) keeps the
unicast deliverable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("famulus")

WOL_HOSTS = os.environ.get("WOL_HOSTS", "")
WOL_WAIT = float(os.environ.get("WOL_WAIT", "60"))
WOL_COOLDOWN = float(os.environ.get("WOL_COOLDOWN", "180"))
WOL_PORT = int(os.environ.get("WOL_PORT", "9"))
PROBE_TIMEOUT = 2.0
POLL_INTERVAL = 3.0
# a host seen up this recently is not probed again (an agent turn makes several calls)
UP_TTL = 20.0

# what to GET to see whether the service (not just the host) is up, by port
PROBES = {11434: "/api/tags", 8188: "/system_stats"}

_MAC = re.compile(r"^[0-9a-f]{12}$")
_lock = threading.Lock()
_last_wake: dict[str, float] = {}
_seen_up: dict[str, float] = {}
# indirections so tests can run without sleeping or a real clock
_sleep = time.sleep
_now = time.monotonic


def hosts() -> dict[str, str]:
    """{host: mac} from WOL_HOSTS; a malformed entry is logged and skipped."""
    out: dict[str, str] = {}
    for item in WOL_HOSTS.split(","):
        item = item.strip()
        if not item:
            continue
        host, sep, mac = item.partition("=")
        host, mac = host.strip(), _norm_mac(mac)
        if not sep or not host or not mac:
            log.warning("WOL_HOSTS: ignoring %r (want ip=aa:bb:cc:dd:ee:ff)", item)
            continue
        out[host] = mac
    return out


def _norm_mac(mac: str) -> str:
    mac = re.sub(r"[^0-9a-fA-F]", "", mac or "").lower()
    return mac if _MAC.match(mac) else ""


def host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def probe_url(url: str) -> str:
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    return base + PROBES.get(parts.port or 0, "/")


def is_up(url: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """True when the service answers HTTP at all (any status: it is awake)."""
    try:
        httpx.get(probe_url(url), timeout=timeout)
        return True
    except Exception:
        return False


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(_norm_mac(mac))
    return b"\xff" * 6 + raw * 16


def _udp_socket() -> socket.socket:  # replaced in tests
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send_wake(host: str, mac: str, port: int | None = None) -> None:
    """Unicast the magic packet to the host and broadcast it on the LAN."""
    pkt = magic_packet(mac)
    port = WOL_PORT if port is None else port
    for dst in (host, "255.255.255.255"):
        s = _udp_socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(pkt, (dst, port))
        except OSError as e:
            log.warning("WoL: could not send to %s:%d (%s)", dst, port, e)
        finally:
            s.close()


def ensure_awake(url: str, wait: float | None = None) -> bool:
    """Make sure the backend at ``url`` is up, waking its host if WOL_HOSTS knows it.

    Returns True when the service answers (or the host is not one we can wake, so the
    caller just tries), False when it is asleep and did not come up in time."""
    host = host_of(url)
    mac = hosts().get(host)
    if not mac:
        return True
    wait = WOL_WAIT if wait is None else wait
    now = _now()
    with _lock:
        if now - _seen_up.get(host, float("-inf")) < UP_TTL:
            return True
    if is_up(url):
        with _lock:
            _seen_up[host] = _now()
        return True
    with _lock:
        last = _last_wake.get(host)
        if last is None or now - last >= WOL_COOLDOWN:
            send_wake(host, mac)
            _last_wake[host] = last = now
            log.warning("WoL: %s is asleep, magic packet sent to %s (%s); waiting up to %.0fs",
                        url, host, mac, wait)
        deadline = last + wait
    if _now() >= deadline:
        log.info("WoL: %s still down %.0fs after the last wake, not waiting again",
                 url, _now() - last)
        return False
    while True:
        remaining = deadline - _now()
        if remaining <= 0:
            log.warning("WoL: %s did not come up within %.0fs of the magic packet", url, wait)
            return False
        _sleep(min(POLL_INTERVAL, remaining))
        if is_up(url):
            with _lock:
                _seen_up[host] = _now()
            log.info("WoL: %s is up, %.0fs after the magic packet", url, _now() - last)
            return True


async def ensure_awake_async(url: str, wait: float | None = None) -> bool:
    """`ensure_awake` off the event loop (the probe and the wait are blocking)."""
    return await asyncio.to_thread(ensure_awake, url, wait)

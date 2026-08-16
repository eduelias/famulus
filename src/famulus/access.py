"""Per-user tool access control — slice tools into domains, grant per user.

A *domain* is a plugin (tutor, homeassistant, torrent, …). Each allowed user may
be restricted to a subset of domains, so a friend can get only the tutoring
tools while never seeing email, home automation, or the server shell.

Policy:
  - the owner has every domain;
  - a user WITH a grants entry has exactly those domains;
  - a user WITHOUT an entry has every domain except the owner-only ones
    (preserves existing users; nobody but the owner ever gets shell/users).

Enforcement is two-layered: the toolset shown to the model is filtered to the
user's domains before routing, and Registry.execute re-checks every call.
"""
from __future__ import annotations

import json
import os

from . import config

GRANTS_FILE = os.environ.get(
    "USER_GRANTS_FILE", os.path.join(os.environ.get("DATA_DIR", "/data"), "user_grants.json"))

# domains only the owner ever gets — not grantable to anyone else
OWNER_ONLY = {d.strip() for d in os.environ.get(
    "OWNER_ONLY_DOMAINS", "shell,users,selfdev").split(",") if d.strip()}

# natural words → domain (plugin) name, so the owner can say "only Dutch"
DOMAIN_ALIASES = {
    "dutch": "tutor", "language": "tutor", "languages": "tutor", "lesson": "tutor",
    "lessons": "tutor", "tutor": "tutor", "tutoring": "tutor", "teacher": "tutor",
    "art": "tutor", "study": "tutor", "learning": "tutor", "learn": "tutor",
    "email": "gmail", "gmail": "gmail", "mail": "gmail", "google": "gmail",
    "outlook": "outlook", "msn": "outlook", "hotmail": "outlook",
    "home": "homeassistant", "house": "homeassistant", "lights": "homeassistant",
    "iot": "homeassistant", "homeassistant": "homeassistant", "ha": "homeassistant",
    "devices": "homeassistant", "music": "homeassistant", "thermostat": "homeassistant",
    "plex": "overseerr", "media": "overseerr", "movies": "overseerr", "shows": "overseerr",
    "episodes": "overseerr", "overseerr": "overseerr", "series": "overseerr",
    "torrent": "torrent", "torrents": "torrent", "seedbox": "torrent", "seeding": "torrent",
    "tl": "torrent", "torrentleech": "torrent", "qbittorrent": "torrent", "ratio": "torrent",
    "weather": "weather", "forecast": "weather",
    "web": "web", "search": "web", "internet": "web",
    "linkedin": "linkedin",
    "budget": "budget", "finance": "budget", "expenses": "budget",
    "shell": "shell", "server": "shell", "terminal": "shell", "ssh": "shell",
}

_FULL_WORDS = {"all", "everything", "full", "anything", "any"}


def _load() -> dict:
    try:
        with open(GRANTS_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(GRANTS_FILE), exist_ok=True)
    tmp = GRANTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, GRANTS_FILE)


def resolve_domains(text) -> list[str]:
    """Map free text or a token list to canonical domain names. '' for none."""
    import re
    if isinstance(text, (list, tuple, set)):
        tokens = [str(t) for t in text]
    else:
        tokens = re.split(r"[^a-zA-Z]+", str(text or ""))
    out: list[str] = []
    for tok in tokens:
        t = tok.strip().lower()
        d = DOMAIN_ALIASES.get(t)
        if d and d not in out:
            out.append(d)
    return out


def wants_full_access(text: str) -> bool:
    low = f" {str(text or '').lower()} "
    return any(f" {w} " in low for w in _FULL_WORDS)


def get_grants(number: str) -> list | None:
    return _load().get(config._norm_number(number))


def set_grants(number: str, domains) -> list[str]:
    """Restrict a user to `domains` (canonical names or free text). Empty raises."""
    doms = [d for d in resolve_domains(domains) if d not in OWNER_ONLY]
    if not doms:
        raise ValueError("no recognisable domains — e.g. 'Dutch', 'home', 'torrent'")
    d = _load()
    d[config._norm_number(number)] = doms
    _save(d)
    return doms


def clear_grants(number: str) -> bool:
    """Give a user full (non-owner-only) access again by removing restrictions."""
    d = _load()
    num = config._norm_number(number)
    if num not in d:
        return False
    del d[num]
    _save(d)
    return True


def allowed_plugins(user: str, all_plugin_names) -> set[str]:
    """The set of domains (plugin names) this user may use."""
    names = set(all_plugin_names)
    if config.is_owner(user):
        return names
    grantable = names - OWNER_ONLY
    entry = _load().get(config._norm_number(user))
    if entry is None:
        return grantable                      # legacy default: all but owner-only
    return {d for d in entry if d in grantable}


def user_allowed(user: str, plugin_name: str) -> bool:
    """Whether `user` may use a tool owned by `plugin_name`."""
    if config.is_owner(user):
        return True
    if plugin_name in OWNER_ONLY:
        return False
    entry = _load().get(config._norm_number(user))
    if entry is None:
        return True                           # legacy default
    return plugin_name in set(entry)

"""Reminders: one-off, recurring, and sun-aware ("after sunset in Amsterdam").

Design mirrors the tutor scheduler: the model only fills structured fields
(24h times, day lists, city names) and ALL date math happens here in code —
ambiguous input raises so the model asks the owner to clarify instead of
guessing. A host cron runs `python3 -m famulus.reminders_tick` every minute;
due reminders are sent over WhatsApp to the user who set them (never to
anyone else — no spam vector). State lives in DATA_DIR/reminders.json.

Sun times come from Open-Meteo (keyless): the city is geocoded once when the
reminder is set; the day's sunrise/sunset is fetched at most once per day per
reminder when the tick first checks it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
from zoneinfo import ZoneInfo

import httpx

from .. import config, context
from ..plugins.base import BasePlugin, spec

TZ = ZoneInfo(os.environ.get("FAMULUS_TZ", "Europe/Amsterdam"))
STORE = os.path.join(config.DATA_DIR, "reminders.json")

_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_SETS = {"daily": list(range(7)), "every day": list(range(7)),
             "weekdays": [0, 1, 2, 3, 4], "weekends": [5, 6]}


def _now() -> dt.datetime:
    return dt.datetime.now(TZ)


def _load() -> dict:
    try:
        with open(STORE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {"seq": 0, "items": []}
    except (OSError, ValueError):
        return {"seq": 0, "items": []}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STORE)


def _parse_days(days: str) -> list[int]:
    days = (days or "").strip().lower()
    if not days:
        return []
    if days in _DAY_SETS:
        return _DAY_SETS[days]
    out = []
    for part in re.split(r"[,\s]+", days):
        part = part[:3]
        if part not in _DAY_NAMES:
            raise ValueError(
                f"I don't recognise the day '{part}' — use names like 'mon, wed' "
                "or 'daily'/'weekdays'/'weekends'.")
        out.append(_DAY_NAMES.index(part))
    return sorted(set(out))


def _parse_hhmm(s: str) -> dt.time:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s.strip())
    if not m or not (0 <= int(m[1]) < 24 and 0 <= int(m[2]) < 60):
        raise ValueError(f"'{s}' is not a valid 24-hour time — use HH:MM, e.g. 21:30.")
    return dt.time(int(m[1]), int(m[2]))


def _geocode(city: str) -> dict:
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": city, "count": 1, "language": "en"},
                  timeout=15).json()
    if not g.get("results"):
        raise ValueError(f"I couldn't find the city '{city}'.")
    p = g["results"][0]
    return {"lat": p["latitude"], "lon": p["longitude"],
            "place": f"{p['name']}, {p.get('country', '')}".strip(", ")}


def _sun_time(item: dict, day: dt.date) -> dt.datetime | None:
    """Today's sunrise/sunset for the reminder's place, cached per day."""
    cache = item.get("sun_cache") or {}
    if cache.get("date") != day.isoformat():
        f = httpx.get("https://api.open-meteo.com/v1/forecast",
                      params={"latitude": item["lat"], "longitude": item["lon"],
                              "daily": "sunrise,sunset", "timezone": "auto",
                              "forecast_days": 2},
                      timeout=15).json()
        daily = f.get("daily", {})
        try:
            idx = daily["time"].index(day.isoformat())
            iso = daily[item["sun_event"]][idx]
        except (KeyError, ValueError, IndexError):
            return None
        item["sun_cache"] = {"date": day.isoformat(), "iso": iso}
    else:
        iso = cache["iso"]
    base = dt.datetime.fromisoformat(iso)
    if base.tzinfo is None:  # Open-Meteo returns local time for timezone=auto
        base = base.replace(tzinfo=TZ)
    return base + dt.timedelta(minutes=int(item.get("offset_min", 0)))


def _fmt(item: dict) -> str:
    if item["kind"] == "sun":
        when = (f"{item['sun_event']} {item.get('offset_min', 0):+d}min "
                f"in {item['place']}" if item.get("offset_min")
                else f"{item['sun_event']} in {item['place']}")
    elif item["kind"] == "recur":
        names = ", ".join(_DAY_NAMES[i] for i in item["days"])
        when = f"{item['time']} on {names}"
    else:
        when = item["at"].replace("T", " ")
    return f"#{item['id']} [{when}] {item['text']}"


class RemindersPlugin(BasePlugin):
    name = "reminders"
    tools = [
        spec("reminder_set",
             "Set a reminder for the CURRENT user (sent back to them on WhatsApp). "
             "One-off: pass at='21:30' (today, or tomorrow if past) or "
             "at='2026-12-01 09:00', or in_minutes=120. Recurring: also pass "
             "days='daily'/'weekdays'/'weekends'/'mon, wed'. Sun-aware ('after "
             "sunset'): pass sun_event='sunset' or 'sunrise' + city (and optional "
             "offset_minutes, e.g. 30 = half an hour after). If the user's wording "
             "is ambiguous (no am/pm, vague 'evening'), ask them to clarify "
             "instead of guessing.",
             {"text": {"type": "string", "description": "what to remind them of"},
              "at": {"type": "string", "description": "24h HH:MM, or 'YYYY-MM-DD HH:MM'"},
              "in_minutes": {"type": "integer", "description": "minutes from now"},
              "days": {"type": "string", "description": "recurrence, e.g. 'daily', 'mon, fri'"},
              "sun_event": {"type": "string", "enum": ["sunset", "sunrise"]},
              "city": {"type": "string", "description": "city for sun times, e.g. Amsterdam"},
              "offset_minutes": {"type": "integer",
                                 "description": "minutes after (+) / before (-) the sun event"}},
             ["text"]),
        spec("reminder_list", "List the current user's active reminders.", {}, []),
        spec("reminder_cancel", "Cancel one of the current user's reminders by id.",
             {"id": {"type": "integer"}}, ["id"]),
    ]

    def execute(self, tool: str, args: dict) -> object:
        user = context.current_user()
        if not user:
            raise ValueError("no user in context")
        if tool == "reminder_list":
            items = [i for i in _load()["items"] if i["user"] == user and i["active"]]
            return {"reminders": [_fmt(i) for i in items]} if items else \
                {"reminders": [], "message": "You have no active reminders."}
        if tool == "reminder_cancel":
            d = _load()
            for i in d["items"]:
                if i["id"] == int(args["id"]) and i["user"] == user and i["active"]:
                    i["active"] = False
                    _save(d)
                    return {"cancelled": _fmt(i)}
            raise ValueError(f"no active reminder #{args['id']} for you")
        if tool == "reminder_set":
            return self._set(user, args)
        raise ValueError(f"unknown tool {tool}")

    def _set(self, user: str, args: dict) -> dict:
        text = str(args.get("text", "")).strip()[:500]
        if not text:
            raise ValueError("the reminder needs a text")
        days = _parse_days(str(args.get("days", "")))
        d = _load()
        d["seq"] = int(d.get("seq", 0)) + 1
        item: dict = {"id": d["seq"], "user": user, "text": text, "active": True,
                      "created": _now().isoformat(timespec="minutes")}

        if args.get("sun_event"):
            ev = str(args["sun_event"]).lower()
            if ev not in ("sunset", "sunrise"):
                raise ValueError("sun_event must be 'sunset' or 'sunrise'")
            geo = _geocode(str(args.get("city") or "Amsterdam"))
            item.update({"kind": "sun", "sun_event": ev, "days": days,
                         "offset_min": int(args.get("offset_minutes", 0) or 0), **geo})
        elif days:
            if not args.get("at"):
                raise ValueError("a recurring reminder needs a time — what time of day?")
            item.update({"kind": "recur", "days": days,
                         "time": _parse_hhmm(str(args["at"])).strftime("%H:%M")})
        elif args.get("in_minutes"):
            when = _now() + dt.timedelta(minutes=int(args["in_minutes"]))
            item.update({"kind": "once", "at": when.isoformat(timespec="minutes")})
        elif args.get("at"):
            s = str(args["at"]).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}", s):
                date_s, time_s = re.split(r"[ T]", s)
                when = dt.datetime.combine(dt.date.fromisoformat(date_s),
                                           _parse_hhmm(time_s), tzinfo=TZ)
            else:
                t = _parse_hhmm(s)
                when = dt.datetime.combine(_now().date(), t, tzinfo=TZ)
                if when <= _now():
                    when += dt.timedelta(days=1)
            if when <= _now():
                raise ValueError("that time is in the past")
            item.update({"kind": "once", "at": when.isoformat(timespec="minutes")})
        else:
            raise ValueError(
                "when should I remind you? Give a time (at='21:30'), a delay "
                "(in_minutes=120), or a sun event (sun_event='sunset').")

        d["items"].append(item)
        _save(d)
        return {"set": _fmt(item)}


def _send(user: str, text: str) -> bool:
    from .. import wa
    return asyncio.run(wa.send_text(user, text))


def tick() -> list[str]:
    """Fire due reminders. Called every minute by host cron. Returns log lines."""
    now = _now()
    today = now.date()
    d = _load()
    fired: list[str] = []
    changed = False
    for item in d["items"]:
        if not item.get("active"):
            continue
        try:
            due, deactivate = False, False
            if item["kind"] == "once":
                due = dt.datetime.fromisoformat(item["at"]) <= now
                deactivate = due
            elif item["kind"] == "recur":
                due = (now.weekday() in item["days"]
                       and item.get("last_fired") != today.isoformat()
                       and now.time() >= _parse_hhmm(item["time"]))
            elif item["kind"] == "sun":
                if item.get("days") and now.weekday() not in item["days"]:
                    continue
                if item.get("last_fired") == today.isoformat():
                    continue
                target = _sun_time(item, today)
                changed = True  # sun_cache may have been refreshed
                due = bool(target) and now >= target
                deactivate = due and not item.get("days")
            if due:
                icon = {"sunset": "🌇", "sunrise": "🌅"}.get(item.get("sun_event", ""), "⏰")
                ok = _send(item["user"], f"{icon} Reminder: {item['text']}")
                item["last_fired"] = today.isoformat()
                if deactivate:
                    item["active"] = False
                changed = True
                fired.append(f"#{item['id']} -> {item['user']} sent={ok}")
        except Exception as e:  # one bad reminder must not block the rest
            fired.append(f"#{item.get('id')} ERROR {type(e).__name__}: {e}")
            changed = True
    if changed:
        _save(d)
    return fired

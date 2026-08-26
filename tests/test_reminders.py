"""Reminders: deterministic time math, per-user isolation, tick firing."""
import datetime as dt

import pytest

from famulus.builtin import reminders as rem


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(rem, "STORE", str(tmp_path / "reminders.json"))
    monkeypatch.setattr("famulus.context.current_user", lambda: "31600000001")
    # frozen clock: Tue 2026-08-26 10:00 local
    frozen = dt.datetime(2026, 8, 26, 10, 0, tzinfo=rem.TZ)
    monkeypatch.setattr(rem, "_now", lambda: frozen)
    return frozen


def _p():
    return rem.RemindersPlugin()


def test_once_at_hhmm_today_and_rollover():
    r = _p().execute("reminder_set", {"text": "tea", "at": "21:30"})
    assert "2026-08-26 21:30" in r["set"]
    r2 = _p().execute("reminder_set", {"text": "early", "at": "08:00"})  # past → tomorrow
    assert "2026-08-27 08:00" in r2["set"]


def test_once_in_minutes_and_full_date():
    r = _p().execute("reminder_set", {"text": "x", "in_minutes": 90})
    assert "2026-08-26 11:30" in r["set"]
    r2 = _p().execute("reminder_set", {"text": "y", "at": "2026-12-01 09:00"})
    assert "2026-12-01 09:00" in r2["set"]


def test_recurring_needs_time_and_parses_days():
    with pytest.raises(ValueError):
        _p().execute("reminder_set", {"text": "gym", "days": "weekdays"})
    r = _p().execute("reminder_set", {"text": "gym", "days": "mon, wed", "at": "07:00"})
    assert "07:00 on mon, wed" in r["set"]
    with pytest.raises(ValueError):
        _p().execute("reminder_set", {"text": "z", "days": "blursday", "at": "07:00"})


def test_ambiguous_time_rejected():
    with pytest.raises(ValueError):
        _p().execute("reminder_set", {"text": "x", "at": "7"})
    with pytest.raises(ValueError):
        _p().execute("reminder_set", {"text": "x"})   # no time at all


def test_sun_reminder_geocodes_once(monkeypatch):
    monkeypatch.setattr(rem, "_geocode",
                        lambda city: {"lat": 52.37, "lon": 4.9, "place": "Amsterdam, NL"})
    r = _p().execute("reminder_set",
                     {"text": "walk", "sun_event": "sunset", "city": "Amsterdam",
                      "offset_minutes": 30})
    assert "sunset +30min in Amsterdam" in r["set"]


def test_list_and_cancel_are_per_user(monkeypatch):
    _p().execute("reminder_set", {"text": "mine", "at": "21:00"})
    monkeypatch.setattr("famulus.context.current_user", lambda: "31600000002")
    out = _p().execute("reminder_list", {})
    assert out["reminders"] == []                      # other user sees nothing
    with pytest.raises(ValueError):
        _p().execute("reminder_cancel", {"id": 1})     # and can't cancel mine
    monkeypatch.setattr("famulus.context.current_user", lambda: "31600000001")
    assert "mine" in _p().execute("reminder_cancel", {"id": 1})["cancelled"]


def test_tick_fires_once_and_deactivates(monkeypatch):
    _p().execute("reminder_set", {"text": "tea", "at": "21:30"})
    sent = []
    monkeypatch.setattr(rem, "_send", lambda u, t: sent.append((u, t)) or True)
    # not due yet
    assert rem.tick() == [] and sent == []
    # jump past due time
    monkeypatch.setattr(rem, "_now",
                        lambda: dt.datetime(2026, 8, 26, 21, 31, tzinfo=rem.TZ))
    fired = rem.tick()
    assert len(fired) == 1 and sent[0][0] == "31600000001" and "tea" in sent[0][1]
    assert rem.tick() == []                            # deactivated, no refire


def test_tick_recurring_fires_once_per_day(monkeypatch):
    _p().execute("reminder_set", {"text": "gym", "days": "daily", "at": "07:00"})
    sent = []
    monkeypatch.setattr(rem, "_send", lambda u, t: sent.append(t) or True)
    monkeypatch.setattr(rem, "_now",
                        lambda: dt.datetime(2026, 8, 26, 7, 1, tzinfo=rem.TZ))
    assert len(rem.tick()) == 1
    assert rem.tick() == []                            # same day: no refire
    monkeypatch.setattr(rem, "_now",
                        lambda: dt.datetime(2026, 8, 27, 7, 1, tzinfo=rem.TZ))
    assert len(rem.tick()) == 1                        # next day: fires again


def test_tick_sun_fires_after_sunset(monkeypatch):
    monkeypatch.setattr(rem, "_geocode",
                        lambda city: {"lat": 52.37, "lon": 4.9, "place": "Amsterdam, NL"})
    _p().execute("reminder_set", {"text": "lights", "sun_event": "sunset"})
    monkeypatch.setattr(rem, "_sun_time",
                        lambda item, day: dt.datetime(2026, 8, 26, 20, 45, tzinfo=rem.TZ))
    sent = []
    monkeypatch.setattr(rem, "_send", lambda u, t: sent.append(t) or True)
    monkeypatch.setattr(rem, "_now",
                        lambda: dt.datetime(2026, 8, 26, 20, 0, tzinfo=rem.TZ))
    assert rem.tick() == []                            # before sunset
    monkeypatch.setattr(rem, "_now",
                        lambda: dt.datetime(2026, 8, 26, 20, 46, tzinfo=rem.TZ))
    assert len(rem.tick()) == 1 and "🌇" in sent[0]
    assert rem.tick() == []                            # one-shot: deactivated


def test_tick_survives_bad_item(monkeypatch):
    _p().execute("reminder_set", {"text": "ok", "at": "21:30"})
    d = rem._load()
    d["items"].append({"id": 99, "user": "31600000001", "text": "broken",
                       "active": True, "kind": "once", "at": "not-a-date"})
    rem._save(d)
    monkeypatch.setattr(rem, "_send", lambda u, t: True)
    monkeypatch.setattr(rem, "_now",
                        lambda: dt.datetime(2026, 8, 26, 21, 31, tzinfo=rem.TZ))
    lines = rem.tick()
    assert any("ERROR" in ln for ln in lines)          # bad one reported
    assert any("#1" in ln for ln in lines)             # good one still fired

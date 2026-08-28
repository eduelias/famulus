"""geo resolution, weather 'here', family where_is."""
from famulus import geo
from famulus.builtin import family, weather


def test_geo_unmapped_user_is_none(monkeypatch):
    monkeypatch.setattr(geo, "_MAP", {"316": "person.x"})
    assert geo.person_of("999") is None
    assert geo.locate("999") is None


def test_weather_here_uses_geo(monkeypatch):
    monkeypatch.setattr("famulus.context.current_user", lambda: "316")
    monkeypatch.setattr(geo, "locate", lambda u: (52.1, 5.1, "home"))
    seen = {}
    monkeypatch.setattr(weather, "_forecast_coords",
                        lambda lat, lon, label, days: seen.update(lat=lat, label=label) or {"ok": 1})
    out = weather.forecast("here")
    assert out == {"ok": 1} and seen["lat"] == 52.1 and "home" in seen["label"]


def test_weather_here_without_location(monkeypatch):
    monkeypatch.setattr("famulus.context.current_user", lambda: "316")
    monkeypatch.setattr(geo, "locate", lambda u: None)
    assert "error" in weather.forecast("")


def test_where_is(monkeypatch):
    monkeypatch.setattr(family, "_NAMES", {"wife": "31618337246"})
    monkeypatch.setattr(geo, "locate",
                        lambda n: (52.0, 5.0, "home") if n == "31618337246" else None)
    out = family.FamilyPlugin().execute("where_is", {"person": "Wife"})
    assert out["zone"] == "home"
    assert "error" in family.FamilyPlugin().execute("where_is", {"person": "ghost"})

"""Weather via Open-Meteo (free, no API key)."""
import httpx

from ..plugins.base import BasePlugin, spec

WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers",
    86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm w/ hail",
    99: "thunderstorm w/ heavy hail",
}


HERE_WORDS = {"", "here", "hier", "aqui", "my location", "current location"}


def forecast(location: str, days: int = 3) -> dict:
    days = max(1, min(int(days), 7))
    if (location or "").strip().lower() in HERE_WORDS:
        from .. import context, geo
        loc = geo.locate(context.current_user())
        if loc:
            return _forecast_coords(loc[0], loc[1], f"your location ({loc[2]})", days)
        return {"error": "no location given and I can't see where you are — "
                         "name a city, or share a location pin"}
    g = httpx.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1, "language": "en"},
        timeout=15,
    ).json()
    if not g.get("results"):
        return {"error": f"location '{location}' not found"}
    place = g["results"][0]
    return _forecast_coords(place["latitude"], place["longitude"],
                            f"{place['name']}, {place.get('country', '')}", days)


def _forecast_coords(lat: float, lon: float, label: str, days: int) -> dict:
    f = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "timezone": "auto",
            "forecast_days": days,
            "current": "temperature_2m,apparent_temperature,weather_code,"
                       "wind_speed_10m,relative_humidity_2m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,precipitation_sum,"
                     "sunrise,sunset",
        },
        timeout=15,
    ).json()
    cur = f.get("current", {})
    daily = f.get("daily", {})
    return {
        "place": label,
        "now": {
            "temp_c": cur.get("temperature_2m"),
            "feels_like_c": cur.get("apparent_temperature"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "conditions": WMO.get(cur.get("weather_code"), "unknown"),
        },
        "daily": [
            {
                "date": daily["time"][i],
                "conditions": WMO.get(daily["weather_code"][i], "unknown"),
                "min_c": daily["temperature_2m_min"][i],
                "max_c": daily["temperature_2m_max"][i],
                "rain_chance_pct": daily["precipitation_probability_max"][i],
                "rain_mm": daily["precipitation_sum"][i],
                "sunrise": daily["sunrise"][i][-5:],   # local HH:MM
                "sunset": daily["sunset"][i][-5:],
            }
            for i in range(len(daily.get("time", [])))
        ],
    }


class WeatherPlugin(BasePlugin):
    name = "weather"
    tools = [
        spec("weather_forecast",
             "Current weather and daily forecast for a location (city name).",
             {"location": {"type": "string"},
              "days": {"type": "integer", "description": "1-7, default 3"}},
             ["location"]),
    ]

    def execute(self, tool: str, args: dict) -> object:
        return forecast(args["location"], int(args.get("days", 3)))

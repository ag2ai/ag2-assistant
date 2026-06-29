"""Weather tool — one deterministic call for current weather + a short forecast,
already mapped to the A2UI WeatherPanel `condition` enum.

Source: wttr.in (?format=j2), the same provider the bundled `weather` skill uses.
The returned JSON drops straight into a WeatherPanel (location + condition + rows),
so the agent never has to web-search for weather or guess the banner condition.
"""

import json
from urllib.parse import quote

from autogen.beta import tool

from assistant.a2ui import WEATHER_CONDITIONS

# wttr.in exposes World Weather Online (WWO) numeric weather codes in
# current_condition[0].weatherCode. Map each to one WeatherPanel condition.
# Anything unmapped falls back to "cloudy". `windy` has no WWO code — it's derived
# from wind speed below.
_CODE_TO_CONDITION = {
    113: "sunny",  # Clear / Sunny
    116: "partly-cloudy",  # Partly cloudy
    119: "cloudy",
    122: "cloudy",  # Cloudy / Overcast
    143: "foggy",
    248: "foggy",
    260: "foggy",  # Mist / Fog / Freezing fog
    200: "thunderstorm",
    386: "thunderstorm",  # Thundery outbreaks / w-thunder
    389: "thunderstorm",
    392: "thunderstorm",
    395: "thunderstorm",
}
# Rain family (drizzle, rain, freezing rain/drizzle, showers).
for _c in (176, 185, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 311, 314, 353, 356, 359):
    _CODE_TO_CONDITION[_c] = "rainy"
# Snow / sleet / ice family.
for _c in (
    179,
    182,
    227,
    230,
    317,
    320,
    323,
    326,
    329,
    332,
    335,
    338,
    350,
    362,
    365,
    368,
    371,
    374,
    377,
):
    _CODE_TO_CONDITION[_c] = "snow"

# Above this sustained wind speed we call it "windy" (unless it's actively
# raining/storming/snowing — those conditions win). ~38 km/h ≈ Beaufort 6.
_WINDY_KMPH = 38.0


def condition_for(weather_code, windspeed_kmph=0.0) -> str:
    """Map a WWO weather code (+ wind speed) to a WeatherPanel condition enum value.

    Pure/deterministic — no network. Guaranteed to return a value in
    ``a2ui.WEATHER_CONDITIONS`` (default ``"cloudy"``).
    """
    try:
        code = int(weather_code)
    except (TypeError, ValueError):
        code = -1
    try:
        wind = float(windspeed_kmph)
    except (TypeError, ValueError):
        wind = 0.0

    condition = _CODE_TO_CONDITION.get(code, "cloudy")
    if condition in ("sunny", "partly-cloudy", "cloudy", "foggy") and wind >= _WINDY_KMPH:
        condition = "windy"
    return condition


def _area_label(data: dict, fallback: str) -> str:
    try:
        area = (data.get("nearest_area") or [{}])[0]
        name = (area.get("areaName") or [{}])[0].get("value", "")
        country = (area.get("country") or [{}])[0].get("value", "")
        if name and country:
            return f"{name}, {country}"
        return name or fallback
    except Exception:
        return fallback


def build_result(data: dict, location: str, units: str = "celsius") -> dict:
    """Turn a parsed wttr.in j2 payload into WeatherPanel-ready fields.

    Pure/deterministic — no network. Returns ``{location, condition, summary, rows}``.
    """
    metric = str(units).lower() != "fahrenheit"
    cc = (data.get("current_condition") or [{}])[0]
    desc = ((cc.get("weatherDesc") or [{}])[0].get("value") or "").strip() or "Unknown"

    temp = cc.get("temp_C") if metric else cc.get("temp_F")
    feels = cc.get("FeelsLikeC") if metric else cc.get("FeelsLikeF")
    wind_kmph = cc.get("windspeedKmph") or 0
    wind = cc.get("windspeedKmph") if metric else cc.get("windspeedMiles")
    t_unit, w_unit = ("°C", "km/h") if metric else ("°F", "mph")

    condition = condition_for(cc.get("weatherCode"), wind_kmph)
    label = _area_label(data, location)

    rows = []
    if temp is not None:
        tv = f"{temp}{t_unit}"
        if feels is not None and feels != temp:
            tv += f" (feels {feels}{t_unit})"
        rows.append({"label": "Temperature", "value": tv})
    rows.append({"label": "Conditions", "value": desc})
    if cc.get("humidity") is not None:
        rows.append({"label": "Humidity", "value": f"{cc['humidity']}%"})
    if wind is not None:
        rows.append({"label": "Wind", "value": f"{wind} {w_unit}"})

    today = (data.get("weather") or [{}])[0]
    hi = today.get("maxtempC") if metric else today.get("maxtempF")
    lo = today.get("mintempC") if metric else today.get("mintempF")
    if hi is not None and lo is not None:
        rows.append({"label": "Today", "value": f"High {hi}{t_unit} · Low {lo}{t_unit}"})

    temp_txt = f"{temp}{t_unit}" if temp is not None else "?"
    summary = f"{label}: {desc}, {temp_txt}"

    assert condition in WEATHER_CONDITIONS  # mapping can't drift from the schema
    return {"location": label, "condition": condition, "summary": summary, "rows": rows}


@tool
def get_weather(location: str, units: str = "celsius") -> str:
    """Get current weather and a short forecast for a location.

    Use this for ANY weather or forecast request — it is the fast, reliable path
    (one call), so do not web-search for weather. Returns a JSON object whose
    `condition` is already one of the WeatherPanel enum values and whose `rows`
    are ready to render: emit a WeatherPanel directly from `location`, `condition`,
    and `rows`, and use `summary` for your prose.

    Args:
        location: City, region, airport code, or "lat,lon".
        units: "celsius" (default) or "fahrenheit".

    Returns:
        JSON string: {"location", "condition", "summary", "rows": [{"label","value"}, …]}.
    """
    import httpx

    url = f"https://wttr.in/{quote(location.strip())}?format=j2"
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=20.0,
            headers={"User-Agent": "curl/8"},  # wttr.in serves JSON to curl-like UAs
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        return f"Could not get weather for {location}: {e}"
    except ValueError as e:
        return f"Could not parse weather for {location}: {e}"

    if not data.get("current_condition"):
        return f"No weather data found for '{location}'."

    return json.dumps(build_result(data, location, units), ensure_ascii=False)

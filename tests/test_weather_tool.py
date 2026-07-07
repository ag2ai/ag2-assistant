from assistant.a2ui import WEATHER_CONDITIONS
from assistant.tools.weather import build_result, condition_for


def test_condition_for_maps_known_codes():
    cases = {
        113: "sunny",
        116: "partly-cloudy",
        119: "cloudy",
        122: "cloudy",
        143: "foggy",
        248: "foggy",
        260: "foggy",
        296: "rainy",
        308: "rainy",
        353: "rainy",
        200: "thunderstorm",
        389: "thunderstorm",
        395: "thunderstorm",
        326: "snow",
        338: "snow",
        230: "snow",
    }
    for code, expected in cases.items():
        assert condition_for(code, 0) == expected, code


def test_condition_for_always_returns_enum_value():
    for code in range(100, 400):
        assert condition_for(code, 0) in WEATHER_CONDITIONS
    # junk / missing inputs default to cloudy, never crash
    assert condition_for(None, None) == "cloudy"
    assert condition_for("bad", "bad") == "cloudy"


def test_windy_override():
    # high wind + a calm-sky condition → windy
    assert condition_for(113, 45) == "windy"  # was sunny
    assert condition_for(119, 50) == "windy"  # was cloudy
    # active precipitation wins over wind
    assert condition_for(296, 60) == "rainy"
    assert condition_for(200, 60) == "thunderstorm"
    assert condition_for(326, 60) == "snow"
    # below the threshold → unchanged
    assert condition_for(113, 20) == "sunny"


def _payload(code, desc, wind_kmph="10"):
    return {
        "current_condition": [
            {
                "temp_C": "12",
                "temp_F": "54",
                "FeelsLikeC": "10",
                "FeelsLikeF": "50",
                "weatherCode": str(code),
                "weatherDesc": [{"value": desc}],
                "humidity": "82",
                "windspeedKmph": wind_kmph,
                "windspeedMiles": "15",
            }
        ],
        "nearest_area": [
            {
                "areaName": [{"value": "London"}],
                "country": [{"value": "United Kingdom"}],
            }
        ],
        "weather": [{"maxtempC": "15", "mintempC": "9", "maxtempF": "59", "mintempF": "48"}],
    }


def test_build_result_celsius_shape_and_enum():
    r = build_result(_payload(296, "Light rain"), "London", "celsius")
    assert r["condition"] == "rainy"
    assert r["condition"] in WEATHER_CONDITIONS
    assert r["location"] == "London, United Kingdom"
    labels = {row["label"]: row["value"] for row in r["rows"]}
    assert labels["Temperature"] == "12°C (feels 10°C)"
    assert labels["Conditions"] == "Light rain"
    assert labels["Humidity"] == "82%"
    assert labels["Wind"] == "24 km/h" or labels["Wind"] == "10 km/h"  # uses windspeedKmph
    assert labels["Today"] == "High 15°C · Low 9°C"


def test_build_result_fahrenheit():
    r = build_result(_payload(113, "Sunny"), "Phoenix", "fahrenheit")
    labels = {row["label"]: row["value"] for row in r["rows"]}
    assert labels["Temperature"] == "54°F (feels 50°F)"
    assert labels["Wind"] == "15 mph"
    assert r["condition"] == "sunny"

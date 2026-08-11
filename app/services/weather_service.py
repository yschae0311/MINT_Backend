"""Open-Meteo weather for the MINT daily corner (no API key required)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 30 * 60
_cache: dict[str, tuple[float, "WeatherSnapshot"]] = {}

_WMO_KO: list[tuple[set[int], str]] = [
    ({0}, "맑음"),
    ({1}, "대체로 맑음"),
    ({2}, "구름 조금"),
    ({3}, "흐림"),
    ({45, 48}, "안개"),
    ({51, 53, 55, 56, 57}, "이슬비"),
    ({61, 63, 65, 66, 67}, "비"),
    ({71, 73, 75, 77}, "눈"),
    ({80, 81, 82}, "소나기"),
    ({85, 86}, "눈소나기"),
    ({95, 96, 99}, "뇌우"),
]


def _code_label(code: int) -> str:
    for keys, label in _WMO_KO:
        if code in keys:
            return label
    return "날씨 정보"


@dataclass(frozen=True)
class WeatherSnapshot:
    location: str
    temperature_c: float
    feels_like_c: float | None
    humidity_pct: int | None
    wind_kmh: float | None
    condition: str
    high_c: float | None
    low_c: float | None
    weather_code: int


class WeatherService:
    def get_today(self) -> WeatherSnapshot | None:
        settings = get_settings()
        if not settings.weather_enabled:
            return None

        lat = settings.weather_latitude
        lon = settings.weather_longitude
        location = settings.weather_location_name.strip() or "서울"
        cache_key = f"{lat:.4f},{lon:.4f}"
        now = time.time()
        hit = _cache.get(cache_key)
        if hit and now - hit[0] < _CACHE_TTL_SEC:
            return hit[1]

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": "Asia/Seoul",
            "forecast_days": 1,
            "wind_speed_unit": "kmh",
        }
        try:
            with httpx.Client(timeout=12.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("Weather fetch failed: %s", exc)
            return hit[1] if hit else None

        current = data.get("current") or {}
        daily = data.get("daily") or {}
        code = int(current.get("weather_code") or 0)
        high = (daily.get("temperature_2m_max") or [None])[0]
        low = (daily.get("temperature_2m_min") or [None])[0]
        humidity = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        feels = current.get("apparent_temperature")
        temp = current.get("temperature_2m")
        if temp is None:
            return hit[1] if hit else None

        snap = WeatherSnapshot(
            location=location,
            temperature_c=float(temp),
            feels_like_c=float(feels) if feels is not None else None,
            humidity_pct=int(humidity) if humidity is not None else None,
            wind_kmh=float(wind) if wind is not None else None,
            condition=_code_label(code),
            high_c=float(high) if high is not None else None,
            low_c=float(low) if low is not None else None,
            weather_code=code,
        )
        _cache[cache_key] = (now, snap)
        return snap

"""Hub 侧的天气代理。

手机可能连在「只有局域网、没有外网」的 Wi-Fi 上（例如 P-5G / 10.83.22.0/24），
而跑 Hub 的机器通常有外网。所以天气由 Hub 代取，手机只跟 Hub 说话 —— 与
「手机只通过局域网 HTTP 与 Hub 通信」的原则保持一致。

数据源：Open-Meteo（免费、无需 key）。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "Shoreting-Hub/1.0 (+https://github.com/wxydejoy/Shoreting)"

CACHE_TTL_SEC = 600.0
HOURLY_CACHE_TTL_SEC = 1800.0
TIMEOUT_SEC = 8.0

# 与安卓端 WeatherPlaces 保持一致的常用城市（无需走地理编码）
CITIES: dict[str, tuple[float, float]] = {
    "北京": (39.90, 116.41),
    "上海": (31.23, 121.47),
    "天津": (39.13, 117.20),
    "重庆": (29.56, 106.55),
    "哈尔滨": (45.75, 126.63),
    "长春": (43.88, 125.32),
    "沈阳": (41.80, 123.43),
    "大连": (38.91, 121.61),
    "呼和浩特": (40.84, 111.75),
    "石家庄": (38.04, 114.51),
    "太原": (37.87, 112.55),
    "西安": (34.26, 108.94),
    "济南": (36.65, 117.12),
    "青岛": (36.07, 120.38),
    "乌鲁木齐": (43.83, 87.62),
    "拉萨": (29.65, 91.17),
    "西宁": (36.62, 101.78),
    "兰州": (36.06, 103.83),
    "银川": (38.49, 106.23),
    "郑州": (34.75, 113.65),
    "南京": (32.06, 118.80),
    "无锡": (31.49, 120.31),
    "苏州": (31.30, 120.62),
    "武汉": (30.59, 114.31),
    "杭州": (30.27, 120.16),
    "宁波": (29.87, 121.55),
    "温州": (28.00, 120.70),
    "合肥": (31.82, 117.23),
    "福州": (26.07, 119.30),
    "厦门": (24.48, 118.09),
    "南昌": (28.68, 115.86),
    "长沙": (28.23, 112.94),
    "贵阳": (26.65, 106.63),
    "成都": (30.67, 104.07),
    "广州": (23.13, 113.26),
    "深圳": (22.54, 114.06),
    "珠海": (22.27, 113.58),
    "佛山": (23.02, 113.12),
    "惠州": (23.11, 114.42),
    "东莞": (23.04, 113.75),
    "中山": (22.52, 113.39),
    "昆明": (25.04, 102.71),
    "南宁": (22.82, 108.37),
    "海口": (20.04, 110.32),
    "香港": (22.32, 114.17),
    "澳门": (22.20, 113.55),
    "台北": (25.03, 121.57),
}

_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*$")

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_hourly_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_last_city: str | None = None


def wmo_condition(code: int) -> str:
    if code == 0:
        return "晴"
    if code in (1, 2):
        return "少云"
    if code == 3:
        return "多云"
    if code in (45, 48):
        return "雾"
    if code in (51, 53, 55, 56, 57):
        return "毛毛雨"
    if code in (61, 63, 65, 66, 67):
        return "雨"
    if code in (71, 73, 75, 77):
        return "雪"
    if code in (80, 81, 82):
        return "阵雨"
    if code in (85, 86):
        return "阵雪"
    if code == 95:
        return "雷阵雨"
    if code in (96, 99):
        return "雷暴冰雹"
    return "阴"


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _resolve(city: str) -> tuple[str, float, float]:
    m = _COORD_RE.match(city)
    if m:
        return city.strip(), float(m.group(1)), float(m.group(2))
    hit = CITIES.get(city)
    if hit is not None:
        return city, hit[0], hit[1]
    query = urllib.parse.urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
    body = _get_json(GEOCODE_URL + "?" + query)
    results = (body or {}).get("results") or []
    if not results:
        raise ValueError("找不到城市：" + city)
    item = results[0]
    name = item.get("name") or item.get("admin1") or city
    return str(name), float(item["latitude"]), float(item["longitude"])


def fetch_weather(city: str) -> dict[str, Any]:
    """返回与安卓 WeatherInfo 同构的字段：city / celsius / condition / query。"""
    key = (city or "").strip()
    if not key:
        raise ValueError("城市为空")
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < CACHE_TTL_SEC:
        return cached[1]

    name, lat, lon = _resolve(key)
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code",
            "timezone": "auto",
        }
    )
    body = _get_json(FORECAST_URL + "?" + query)
    current = (body or {}).get("current") or {}
    temp = current.get("temperature_2m")
    if temp is None:
        raise ValueError("天气源没有返回温度")
    try:
        code = int(current.get("weather_code") or 3)
    except (TypeError, ValueError):
        code = 3

    data = {
        "city": name,
        "celsius": float(temp),
        "condition": wmo_condition(code),
        "query": key,
    }
    _cache[key] = (now, data)
    global _last_city
    _last_city = key
    return data


def last_city() -> str | None:
    """手机最近一次查过天气的城市；主动播报（天气突变）用同一个地方。"""
    return _last_city


def fetch_hourly(city: str) -> dict[str, Any]:
    """逐小时预报，给「天气突变」主动提醒用。

    返回 {"city", "hourly": {"time", "celsius", "code", "wind_kmh"}}，
    time 是城市当地时区（Open-Meteo timezone=auto）的 ISO 字符串。
    与 fetch_weather 分开缓存：逐小时数据大，且手机的主屏天气不依赖它。
    """
    key = (city or "").strip()
    if not key:
        raise ValueError("城市为空")
    now = time.monotonic()
    cached = _hourly_cache.get(key)
    if cached is not None and now - cached[0] < HOURLY_CACHE_TTL_SEC:
        return cached[1]

    name, lat, lon = _resolve(key)
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,weather_code,wind_speed_10m",
            "forecast_days": 2,
            "timezone": "auto",
        }
    )
    body = _get_json(FORECAST_URL + "?" + query)
    hourly = (body or {}).get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    codes = hourly.get("weather_code") or []
    winds = hourly.get("wind_speed_10m") or []
    if not times or not temps or len(times) != len(temps):
        raise ValueError("天气源没有返回逐小时预报")
    data = {
        "city": name,
        "hourly": {
            "time": [str(t) for t in times],
            "celsius": [float(t) for t in temps],
            "code": [int(c or 3) for c in codes] if len(codes) == len(times) else [],
            "wind_kmh": [float(w or 0) for w in winds] if len(winds) == len(times) else [],
        },
    }
    _hourly_cache[key] = (now, data)
    return data

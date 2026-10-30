"""天气代理：逐小时预报与「最近查过的城市」，解析不打真网。"""

import unittest

from dock_hub import weather


def fake_hourly_body() -> dict:
    return {
        "hourly": {
            "time": ["2026-10-30T14:00", "2026-10-30T15:00"],
            "temperature_2m": [20.5, 19.0],
            "weather_code": [1, 63],
            "wind_speed_10m": [12.0, None],
        }
    }


class TestFetchHourly(unittest.TestCase):
    def setUp(self) -> None:
        weather._hourly_cache.clear()
        self.calls: list[str] = []
        self._orig = weather._get_json
        weather._get_json = lambda url: (self.calls.append(url), fake_hourly_body())[1]
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        weather._get_json = self._orig
        weather._hourly_cache.clear()

    def test_parses_fields(self) -> None:
        data = weather.fetch_hourly("上海")
        self.assertEqual(data["city"], "上海")
        hourly = data["hourly"]
        self.assertEqual(hourly["time"], ["2026-10-30T14:00", "2026-10-30T15:00"])
        self.assertEqual(hourly["celsius"], [20.5, 19.0])
        self.assertEqual(hourly["code"], [1, 63])
        self.assertEqual(hourly["wind_kmh"], [12.0, 0.0])  # 缺的风速当 0

    def test_uses_cache(self) -> None:
        weather.fetch_hourly("上海")
        weather.fetch_hourly("上海")
        self.assertEqual(len(self.calls), 1)

    def test_empty_city_raises(self) -> None:
        with self.assertRaises(ValueError):
            weather.fetch_hourly("   ")

    def test_missing_temps_raises(self) -> None:
        weather._get_json = lambda url: {"hourly": {"time": ["2026-10-30T14:00"]}}
        with self.assertRaises(ValueError):
            weather.fetch_hourly("上海")


class TestLastCity(unittest.TestCase):
    """手机查过哪儿，天气提醒就说哪儿。"""

    def setUp(self) -> None:
        weather._last_city = None
        weather._cache.clear()
        self._orig = weather._get_json
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        weather._get_json = self._orig
        weather._last_city = None
        weather._cache.clear()

    def test_none_before_any_query(self) -> None:
        self.assertIsNone(weather.last_city())

    def test_follows_current_fetch(self) -> None:
        weather._get_json = lambda url: {"current": {"temperature_2m": 21.0, "weather_code": 1}}
        data = weather.fetch_weather("上海")
        self.assertEqual(data["city"], "上海")
        self.assertEqual(weather.last_city(), "上海")


if __name__ == "__main__":
    unittest.main()

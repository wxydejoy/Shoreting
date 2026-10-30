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


class TestFetchSun(unittest.TestCase):
    """日出日落（F16）：解析到 HH:MM，失败有记仇期。"""

    def setUp(self) -> None:
        weather._sun_cache.clear()
        weather._sun_fail_at.clear()
        self._orig = weather._get_json
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        weather._get_json = self._orig
        weather._sun_cache.clear()
        weather._sun_fail_at.clear()

    def test_parses_to_hhmm(self) -> None:
        weather._get_json = lambda url: {
            "daily": {"sunrise": ["2026-10-31T06:12"], "sunset": ["2026-10-31T17:42"]}
        }
        data = weather.fetch_sun("杭州")
        self.assertEqual(data["sunset"], "17:42")
        self.assertEqual(data["sunrise"], "06:12")

    def test_uses_cache(self) -> None:
        calls: list[str] = []
        body = {"daily": {"sunrise": ["2026-10-31T06:12"], "sunset": ["2026-10-31T17:42"]}}
        weather._get_json = lambda url: (calls.append(url), body)[1]
        weather.fetch_sun("杭州")
        weather.fetch_sun("杭州")
        self.assertEqual(len(calls), 1)

    def test_failure_has_grace_period(self) -> None:
        def boom(url: str) -> dict:
            raise ValueError("天气源没有返回日落时间")

        weather._get_json = boom
        with self.assertRaises(ValueError):
            weather.fetch_sun("杭州")
        with self.assertRaises(ValueError):
            weather.fetch_sun("杭州")
        # 记仇期过后再试一次
        weather._sun_fail_at.clear()
        weather._get_json = lambda url: {
            "daily": {"sunrise": ["2026-10-31T06:12"], "sunset": ["2026-10-31T17:42"]}
        }
        self.assertEqual(weather.fetch_sun("杭州")["sunset"], "17:42")

    def test_hhmm_helper(self) -> None:
        self.assertEqual(weather._hhmm("2026-10-31T09:05"), "09:05")
        self.assertEqual(weather._hhmm("2026-10-31 9:05"), "09:05")
        self.assertEqual(weather._hhmm("garbage"), "")
        self.assertEqual(weather._hhmm(""), "")

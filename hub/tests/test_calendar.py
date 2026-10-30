"""日程接入（F13）：ICS 解析、重复展开、缓存与记仇，全部不打真网。"""

import datetime as dt
import unittest

from dock_hub import calendar


def ics(*lines: str) -> str:
    return "\r\n".join(lines) + "\r\n"


class TestUnfold(unittest.TestCase):
    def test_joins_continuation(self) -> None:
        text = "SUMMARY:很长的一\r\n 个标题\r\nDTSTART:20261031T140000Z"
        self.assertEqual(
            calendar.unfold(text),
            ["SUMMARY:很长的一个标题", "DTSTART:20261031T140000Z"],
        )

    def test_blank_and_cr_only(self) -> None:
        self.assertEqual(calendar.unfold("A:1\r\n\r\nB:2\r"), ["A:1", "B:2"])


class TestParseIcs(unittest.TestCase):
    def test_timed_utc_event(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART:20261031T060000Z",
                "DTEND:20261031T070000Z",
                "SUMMARY:组会",
                "END:VEVENT",
            )
        )
        self.assertEqual(len(events), 1)
        start = events[0].start
        self.assertEqual(start.utcoffset(), dt.timedelta(0))
        # 北京时间下午两点
        self.assertEqual(start.astimezone().strftime("%H:%M"), "14:00")
        self.assertEqual(events[0].title, "组会")

    def test_all_day_event(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART;VALUE=DATE:20261101",
                "SUMMARY:缴房租",
                "END:VEVENT",
            )
        )
        self.assertEqual(calendar._local_date(events[0].start), dt.date(2026, 11, 1))
        self.assertEqual(calendar._local_hhmm(events[0].start), "")

    def test_tzid_event(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART;TZID=Asia/Shanghai:20261031T140000",
                "SUMMARY:复查",
                "END:VEVENT",
            )
        )
        self.assertEqual(calendar._local_hhmm(events[0].start), "14:00")

    def test_valarm_ignored(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART:20261031T060000Z",
                "SUMMARY:组会",
                "BEGIN:VALARM",
                "TRIGGER:-PT15M",
                "END:VALARM",
                "END:VEVENT",
            )
        )
        self.assertEqual(len(events), 1)

    def test_broken_event_skipped(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART:not-a-date",
                "SUMMARY:坏条目",
                "END:VEVENT",
                "BEGIN:VEVENT",
                "DTSTART:20261031T060000Z",
                "SUMMARY:好条目",
                "END:VEVENT",
            )
        )
        self.assertEqual([item.title for item in events], ["好条目"])


class TestRecurrence(unittest.TestCase):
    def _event(self, rrule: str, start: str = "20261102T010000Z") -> calendar.Event:
        """2026-11-02 是周一（UTC 01:00 = 北京 09:00）。"""
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                f"DTSTART:{start}",
                "DTEND:20261102T020000Z",
                "SUMMARY:站会",
                f"RRULE:{rrule}",
                "END:VEVENT",
            )
        )
        return events[0]

    def test_daily(self) -> None:
        event = self._event("FREQ=DAILY")
        days = calendar._occurrences(event, dt.date(2026, 11, 2), dt.date(2026, 11, 4))
        self.assertEqual(days, [dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)])

    def test_daily_interval_and_count(self) -> None:
        event = self._event("FREQ=DAILY;INTERVAL=2;COUNT=2")
        days = calendar._occurrences(event, dt.date(2026, 11, 1), dt.date(2026, 11, 10))
        # 11-02 起，隔一天一次，只两次：02、04
        self.assertEqual(days, [dt.date(2026, 11, 2), dt.date(2026, 11, 4)])

    def test_weekly_byday(self) -> None:
        event = self._event("FREQ=WEEKLY;BYDAY=MO,WE")
        days = calendar._occurrences(event, dt.date(2026, 11, 2), dt.date(2026, 11, 11))
        self.assertEqual(
            days,
            [
                dt.date(2026, 11, 2),  # 周一
                dt.date(2026, 11, 4),  # 周三
                dt.date(2026, 11, 9),  # 下周一
                dt.date(2026, 11, 11),
            ],
        )

    def test_until_stops(self) -> None:
        event = self._event("FREQ=DAILY;UNTIL=20261103T000000Z")
        days = calendar._occurrences(event, dt.date(2026, 11, 1), dt.date(2026, 11, 30))
        self.assertEqual(days, [dt.date(2026, 11, 2), dt.date(2026, 11, 3)])

    def test_exdate_skipped(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART:20261102T010000Z",
                "SUMMARY:站会",
                "RRULE:FREQ=DAILY",
                "EXDATE:20261103T010000Z",
                "END:VEVENT",
            )
        )
        days = calendar._occurrences(events[0], dt.date(2026, 11, 2), dt.date(2026, 11, 4))
        self.assertEqual(days, [dt.date(2026, 11, 2), dt.date(2026, 11, 4)])

    def test_unknown_freq_no_repeat(self) -> None:
        event = self._event("FREQ=SECONDLY")
        self.assertEqual(calendar._occurrences(event, dt.date(2026, 11, 2), dt.date(2026, 11, 3)), [])

    def test_long_anchor_bounded(self) -> None:
        """DTSTART 十年前、每天重复：展开照常落账，步数封顶不炸。"""
        event = self._event("FREQ=DAILY", start="20161102T010000Z")
        days = calendar._occurrences(event, dt.date(2026, 11, 2), dt.date(2026, 11, 2))
        self.assertEqual(days, [dt.date(2026, 11, 2)])


class TestDayEvents(unittest.TestCase):
    def test_sort_all_day_first(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART:20261031T060000Z",
                "SUMMARY:下午的事",
                "END:VEVENT",
                "BEGIN:VEVENT",
                "DTSTART;VALUE=DATE:20261031",
                "SUMMARY:全天的",
                "END:VEVENT",
            )
        )
        rows = calendar.day_events(events, dt.date(2026, 10, 31))
        self.assertEqual([row["title"] for row in rows], ["全天的", "下午的事"])
        self.assertEqual(rows[1]["start"], "14:00")

    def test_multiday_spans(self) -> None:
        events = calendar.parse_ics(
            ics(
                "BEGIN:VEVENT",
                "DTSTART;VALUE=DATE:20261102",
                "DTEND;VALUE=DATE:20261104",
                "SUMMARY:出差",
                "END:VEVENT",
            )
        )
        self.assertEqual(
            [row["title"] for row in calendar.day_events(events, dt.date(2026, 11, 3))],
            ["出差"],
        )
        self.assertEqual(calendar.day_events(events, dt.date(2026, 11, 4)), [])  # DTEND 排他

    def test_empty_title_dropped(self) -> None:
        events = calendar.parse_ics(
            ics("BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261031", "END:VEVENT")
        )
        self.assertEqual(calendar.day_events(events, dt.date(2026, 10, 31)), [])


class TestCalendarFeed(unittest.TestCase):
    def _feed(self, body: str) -> tuple[calendar.CalendarFeed, list[str], dict]:
        calls: list[str] = []

        def fetch(url: str) -> str:
            calls.append(url)
            return body

        now = {"t": 1000.0}
        feed = calendar.CalendarFeed(
            "https://example.com/cal.ics", fetch=fetch, clock=lambda: now["t"]
        )
        return feed, calls, now

    def test_fetch_once_within_ttl(self) -> None:
        feed, calls, now = self._feed(
            ics("BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261031", "SUMMARY:事", "END:VEVENT")
        )
        day = dt.date(2026, 10, 31)
        self.assertEqual(len(feed.events(day)), 1)
        now["t"] += 60.0
        feed.events(day)
        self.assertEqual(len(calls), 1)  # TTL 内不再拉

    def test_refetch_after_ttl(self) -> None:
        feed, calls, now = self._feed(
            ics("BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261031", "SUMMARY:事", "END:VEVENT")
        )
        day = dt.date(2026, 10, 31)
        feed.events(day)
        now["t"] += calendar.CACHE_TTL_SEC + 1
        feed.events(day)
        self.assertEqual(len(calls), 2)

    def test_failure_keeps_old_data_then_backoff(self) -> None:
        body = ics("BEGIN:VEVENT", "DTSTART;VALUE=DATE:20261031", "SUMMARY:事", "END:VEVENT")
        feed, calls, now = self._feed(body)
        day = dt.date(2026, 10, 31)
        self.assertEqual(len(feed.events(day)), 1)  # 首拉成功

        def broken(url: str) -> str:
            calls.append(url)
            raise OSError("源挂了")

        now["t"] += calendar.CACHE_TTL_SEC + 1
        feed._fetch = broken
        self.assertEqual(len(feed.events(day)), 1)  # 拉挂了：吞掉，用旧账
        self.assertEqual(len(calls), 2)
        now["t"] += 60.0  # 记仇期内：不再敲源
        self.assertEqual(len(feed.events(day)), 1)
        self.assertEqual(len(calls), 2)
        now["t"] += calendar.FAIL_BACKOFF_SEC  # 记仇期过了：再试一回
        self.assertEqual(len(feed.events(day)), 1)
        self.assertEqual(len(calls), 3)

    def test_empty_url_never_fetches(self) -> None:
        feed, calls, _ = self._feed("")
        feed.url = ""
        self.assertEqual(feed.events(dt.date(2026, 10, 31)), [])
        self.assertEqual(calls, [])


class TestConfigRoundtrip(unittest.TestCase):
    def _parse(self, raw: dict) -> object:
        from dock_hub.config import hub_config_to_raw, parse_config

        return parse_config(hub_config_to_raw(parse_config(raw)))

    def test_string_url(self) -> None:
        cfg = self._parse({"name": "s", "token": "t", "devices": [], "calendar": "https://x/y.ics"})
        self.assertEqual(cfg.calendar.url, "https://x/y.ics")

    def test_dict_with_name(self) -> None:
        cfg = self._parse(
            {"name": "s", "token": "t", "devices": [], "calendar": {"url": "https://x/y.ics", "name": "家"}}
        )
        self.assertEqual((cfg.calendar.url, cfg.calendar.name), ("https://x/y.ics", "家"))

    def test_unset_and_false(self) -> None:
        self.assertIsNone(self._parse({"name": "s", "token": "t", "devices": []}).calendar)
        self.assertIsNone(self._parse({"name": "s", "token": "t", "devices": [], "calendar": False}).calendar)

    def test_bad_value_rejected(self) -> None:
        from dock_hub.config import parse_config

        with self.assertRaises(ValueError):
            parse_config({"name": "s", "token": "t", "devices": [], "calendar": 42})
        with self.assertRaises(ValueError):
            parse_config({"name": "s", "token": "t", "devices": [], "calendar": {"name": "没链接"}})


if __name__ == "__main__":
    unittest.main()

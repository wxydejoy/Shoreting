"""主题与节日皮肤（F36）：过节、生日、考试周，她长得不一样。

分工定死：**日期的事 Hub 说了算**（生日、考试周是配置；春节写死近几年的
正月初一，引一套农历库不值当），**天气的事手机说了算**（下雨天安卓自己
看天气换皮，不用等 Hub 转）。两边拼起来才是今天的她。

Hub 只回答「今天是什么日子」，把 occasion 列表放进 snapshot；配色是安卓
的事。生日当天另外说一句，走主动开口的闸，一天最多一次。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# 近几年的春节（正月初一，公历）。表过期之前记得续——2026 年往后再补五年就够用很久
SPRING_FESTIVAL: dict[int, tuple[int, int]] = {
    2026: (2, 17),
    2027: (2, 6),
    2028: (1, 26),
    2029: (2, 13),
    2030: (2, 3),
    2031: (2, 23),
}

# 优先级从高到低：生日最大，节日其次，考试周垫底（该干嘛干嘛，皮肤冷一点就行）
OCCASION_ORDER = ("birthday", "spring_festival", "new_year", "christmas", "exam_week")


@dataclass
class ThemeConfig:
    """主题皮肤（F36）。生日和考试周是自己的日子，节日内置。"""

    birthday: str = ""  # "MM-DD"；空 = 不提生日
    exam_weeks: list[str] = field(default_factory=list)  # ["02-20..02-26"]，闭区间


def occasions_for(today: date, config: ThemeConfig) -> list[str]:
    """今天是什么日子。命中多个按 OCCASION_ORDER 排，皮肤由安卓挑第一个。"""
    hits: set[str] = set()
    if config.birthday and _mmdd(today) == config.birthday:
        hits.add("birthday")
    new_year = SPRING_FESTIVAL.get(today.year)
    if new_year is not None:
        onset = date(today.year, new_year[0], new_year[1])
        # 初一当天和除夕（前一天）都算过年
        if today in (onset, onset - timedelta(days=1)):
            hits.add("spring_festival")
    if (today.month, today.day) == (1, 1):
        hits.add("new_year")
    if (today.month, today.day) in {(12, 24), (12, 25), (12, 26)}:
        hits.add("christmas")
    for week in config.exam_weeks:
        if _in_week(today, week):
            hits.add("exam_week")
            break
    return [name for name in OCCASION_ORDER if name in hits]


def birthday_text() -> str:
    """生日那一句。就一句，不唱歌不念诗。"""
    return "生日快乐。"


def _mmdd(today: date) -> str:
    return f"{today.month:02d}-{today.day:02d}"


def _in_week(today: date, week: str) -> bool:
    """"02-20..02-26" 是否盖住今天。格式不对就当没配，不炸。"""
    try:
        left, right = week.split("..", 1)
        start = _parse_mmdd(left)
        end = _parse_mmdd(right)
    except ValueError:
        return False
    if start is None or end is None:
        return False
    year = today.year
    try:
        start = date(year, start[0], start[1])
        end = date(year, end[0], end[1])
    except ValueError:
        return False
    return start <= today <= end


def _parse_mmdd(raw: str) -> tuple[int, int] | None:
    parts = raw.strip().split("-")
    if len(parts) != 2:
        return None
    month, day = int(parts[0]), int(parts[1])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day

"""T7 退役回滚开关（Shorekeeper 迁移期专用）。

hub.yaml 的 `retire:` 段逐组关停待退役功能面；**默认全部 False = 维持 0.9.0 现行为**，
开关就位、一个都不关。翻转某组为 true → 对应路由 404 `retired`；翻回 false 即回滚。
每批退役必须同步改 docs/openapi.yaml（scripts/check_protocol.py 会红），回滚同理。

退役依据：docs/t7-route-dependency-matrix.md（路由 × 调用方矩阵 + 模块退役批次）。
替代件就位前不许翻开关——这是「先立后破」的闸门，不是裁剪本身。
"""

from __future__ import annotations

# 组名 → 说明（谁是替代件、何时可关）
RETIRE_GROUPS: dict[str, str] = {
    "mijia": "米家面（/v1/devices/* /v1/scenes/* /v1/weather /v1/leave）——T1 dsh-mihome 验收后可关",
    "companion": "伴侣面（/v1/companion/*）——T3 角色卡 + T4 音色 + T5 听书就位后可关",
    "music": "音乐面（/v1/music/*）——@doitian/dsh-music 验收后可关",
    "calendar_diary": "日历/日记存储面（/v1/calendar/* /v1/diary/*）——dsh-calendar / dsh-calendar-diary 迁移验收后可关",
    "monitoring": "监控上报面（/v1/telemetry /v1/power /metrics）——dsh-vitals 验收后可关",
}

# 路由前缀 → 组。/v1/snapshot 与 /health 永不退役（k20 主屏与探活通道）。
ROUTE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/v1/devices", "mijia"),
    ("/v1/scenes", "mijia"),
    ("/v1/weather", "mijia"),
    ("/v1/leave", "mijia"),
    ("/v1/companion", "companion"),
    ("/v1/music", "music"),
    ("/v1/calendar", "calendar_diary"),
    ("/v1/diary", "calendar_diary"),
    ("/v1/telemetry", "monitoring"),
    ("/v1/power", "monitoring"),
    ("/metrics", "monitoring"),
)

# [T7-RETIRE-CANDIDATE] 模块退役批次（盘点 §3；只标记不删代码，真正裁剪分批执行）
RETIRE_CANDIDATE_MODULES: dict[str, str] = {
    "mijia_bridge.py": "一批（dsh-mihome 验收后）",
    "mijia_catalog.py": "一批",
    "actions.py": "二批（模型 ACTION 行由 dsh 原生工具调用替代）",
    "companion.py": "二批（大脑归 dsh）",
    "companion_flow.py": "二批",
    "companion_llm.py": "二批",
    "llm.py": "二批",
    "ambience.py": "二批（dsh-mihome 场景接管）",
    "wallpaper.py": "二批（归 T2 k20.wallpaper）",
    "media.py": "二批（音量面另判，⏸ 等 T1/T2）",
    "pc.py": "一批（随探针/监控退役）",
    "reporter.py": "一批（随 mini 整件）",
    "theme.py": "三批（皮肤语义进 k20 插件）",
    "assets.py": "三批（死料确认后）",
    "calendar.py": "二批（dsh-calendar 替代）",
    "diary.py": "二批（dsh-calendar-diary 替代）",
    "books.py": "二批（归 T5 reading 插件）",
}

# 明确保留（盘点结论，防止误伤）：server.py 长轮询、accesslog.py、logs.py、tray.py（探针期）、
# launch.py（⏸ 等 T2 迁移）、ledstrip.py（⏸ 等 T1 判定）、probe.py、mini 相关。

RETIRE_404_CODE = "retired"


def parse_retire(raw: object) -> dict[str, bool]:
    """hub.yaml 的 retire: 段 → {组名: bool}。未知组名直接报错（typo 防护）。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("retire 应该是对象，如 retire: {mijia: true}")
    flags: dict[str, bool] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if name not in RETIRE_GROUPS:
            raise ValueError(f"retire.{name} 不是已知的退役组（可选：{', '.join(sorted(RETIRE_GROUPS))}）")
        if not isinstance(value, bool):
            raise ValueError(f"retire.{name} 只接受 true/false")
        flags[name] = value
    return flags


def retired_group_for(path: str, flags: dict[str, bool]) -> str | None:
    """该路径是否被关停；命中返回组名（→ 404 retired），未命中返回 None。"""
    if not flags:
        return None
    for prefix, group in ROUTE_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}/"):
            if flags.get(group):
                return group
            return None
    return None

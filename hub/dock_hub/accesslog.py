"""谁在什么时候敲了哪个口子：访问日志。

为什么要有它：现在 Hub 的访问日志默认不打，出问题时（灯不亮、对话没反应、
谁半夜在扫端口）只能靠当时有没有开着终端。留一份按天滚动的文件，事后能回溯。

两件事比「记录下来」更重要：

1. **不记 token、不记查询串。** `/chats/claim?ticket=...` 里的 ticket 就是登录
   凭据，`/v1/companion/chats?q=...` 里是用户搜过的原文。这些进了日志文件，
   等于把「不该留的东西」写到了磁盘上。所以只留路径，问号之后一律丢掉。
2. **写不进去也不能影响请求。** 磁盘满、目录只读、权限不对，都只吞掉这一行，
   绝不抛给调用方 —— 日志是副产品，不是主流程的一部分。

位置默认在配置目录下的 `logs/`，文件名 `access-YYYY-MM-DD.log`，留 7 天。
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from dock_hub.config import HubConfig

DEFAULT_KEEP_DAYS = 7
# 日志文件名里的日期，也用来算该删哪些
_PREFIX = "access-"


def _env_flag(name: str) -> str:
    return os.environ.get(name, "").strip().lower()


def redact(target: str) -> str:
    """抹掉查询串，只留路径。

    宁可直接砍掉整段查询也不做白名单：哪天多了个新参数，漏配白名单就是把
    密码写进文件；全砍掉最差也只是少了一条排查线索。
    """
    head, sep, _tail = target.partition("?")
    return f"{head}?…" if sep else head


class AccessLog:
    """按天滚动的访问日志。`directory=None` 表示不写文件。"""

    def __init__(
        self,
        directory: Path | None,
        *,
        keep_days: int = DEFAULT_KEEP_DAYS,
        service: str = "hub",
        echo: bool = False,
    ) -> None:
        self.directory = directory
        self.keep_days = max(1, keep_days)
        self.service = service
        # echo=True 时同时打到 stdout：本地调试不用再去翻文件
        self.echo = echo
        self._lock = threading.Lock()
        self._day = ""
        self._pruned_for = ""

    @classmethod
    def from_config(cls, config: HubConfig, *, service: str = "hub") -> AccessLog:
        """默认开。`DOCK_HUB_ACCESS_LOG=0` 关掉，`=1` 同时打到终端。

        没有配置文件（path 为空，一般是测试或内嵌用法）就不写：这时候只能去猜
        一个目录，而「往用户 home 里悄悄建文件」比少一份日志更糟。
        """
        mode = _env_flag("DOCK_HUB_ACCESS_LOG")
        if mode in {"0", "off", "false", "no"}:
            return cls(None, service=service)
        if config.path is None:
            return cls(None, service=service, echo=mode in {"1", "on", "true", "yes", "stdout"})
        base = config.path.parent
        keep_raw = _env_flag("DOCK_HUB_LOG_KEEP_DAYS")
        keep = int(keep_raw) if keep_raw.isdigit() else DEFAULT_KEEP_DAYS
        echo = mode in {"1", "on", "true", "yes", "stdout"}
        return cls(base / "logs", keep_days=keep, service=service, echo=echo)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def path_for(self, day: date) -> Path:
        assert self.directory is not None
        return self.directory / f"{_PREFIX}{day.isoformat()}.log"

    def write(
        self,
        client: str,
        request: str,
        status: int | str = "-",
        size: int | str = "-",
        ms: float = 0.0,
    ) -> None:
        line = (
            f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
            f"{self.service} {client or '-'} {status} {size} {ms:.0f}ms "
            f'"{redact(request or "-")}"'
        )
        if self.echo:
            print(line, flush=True)
        if self.directory is None:
            return
        try:
            self._append(line)
        except OSError:
            # 磁盘满 / 目录只读 / 权限不对：丢掉这一行，别把请求带崩
            pass

    def _append(self, line: str) -> None:
        today = date.today()
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            with self.path_for(today).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if self._pruned_for != today.isoformat():
                self._prune(today)
                self._pruned_for = today.isoformat()

    def _prune(self, today: date) -> None:
        cutoff = today - timedelta(days=self.keep_days - 1)
        assert self.directory is not None
        for path in self.directory.glob(f"{_PREFIX}*.log"):
            try:
                day = date.fromisoformat(path.stem[len(_PREFIX) :])
            except ValueError:
                continue  # 不是我们起的名字，别碰
            if day < cutoff:
                try:
                    path.unlink()
                except OSError:
                    continue

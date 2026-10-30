"""通知镜像（F35）：手机收到重要消息，书桌亮一下。

方向和 F33 相反：那一条是把话递给手机，这一条是把手机（K20 本身）收到的
通知送到摆件屏幕上——Dock 全屏运行时系统的横幅根本看不见，重要的消息
（白名单应用）得在摆件界面里「亮一下」。

为什么绕 Hub 一圈而不是监听器直接画：绕一圈换来自增 id 去重、重启不丢、
PC/网页也能看到到底来了什么（调「哪些消息值得打断」时用得上），而且
snapshot 的长轮询白拿。安卓侧的规矩：

- **默认关**。开关在设置页，白名单按包名一个一个填——「哪些消息值得打断」
  很难调，宁可一开始什么都不亮；
-  ongoing（进度条、常驻后台）一律不送；
- 同一条 (app, title, text) 60 秒内只送一次——应用爱在更新时反复 post。

书桌侧只亮不念：亮是「看见」，念是「打断」，后者要走主动开口的配额体系，
这条不碰。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Callable

MAX_ITEMS = 5
KEEP_HOURS = 6
DEDUP_SEC = 60
MAX_TEXT = 160
MAX_TITLE = 60
MAX_APP = 80


class MirrorBoard:
    """亮在书桌上的镜像通知。内存态，重启即清——重连后旧消息不值得再亮。"""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._items: list[dict[str, Any]] = []
        self._now = now or (lambda: datetime.now().astimezone())

    def post(
        self, app: str, text: str, title: str = "", *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """记一条。重复（60 秒内同 app+title+text）返回 None，不算新消息。"""
        app = (app or "").strip()[:MAX_APP]
        body = (text or "").strip()[:MAX_TEXT]
        head = (title or "").strip()[:MAX_TITLE]
        if not app or not body:
            raise ValueError("镜像通知缺 app 或正文")
        now = now or self._now()
        with self._lock:
            # 只跟最新一条比：应用反复 post 时是连着的，隔了别条的就当新消息
            if self._items:
                last = self._items[-1]
                if (
                    (last["app"], last["title"], last["text"]) == (app, head, body)
                    and (now - datetime.fromisoformat(last["at"])).total_seconds() < DEDUP_SEC
                ):
                    return None
            self._seq += 1
            item = {
                "id": self._seq,
                "app": app,
                "title": head,
                "text": body,
                "at": now.isoformat(timespec="seconds"),
            }
            self._items.append(item)
            self._prune(now)
            self._items = self._items[-MAX_ITEMS:]
            return dict(item)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "last_id": self._seq,
                "items": [dict(item) for item in self._items],
            }

    def _prune(self, now: datetime) -> None:
        cutoff = (now - timedelta(hours=KEEP_HOURS)).isoformat(timespec="seconds")
        self._items = [item for item in self._items if str(item.get("at") or "") >= cutoff]

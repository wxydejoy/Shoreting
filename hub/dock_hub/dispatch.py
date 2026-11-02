"""泰缇斯派发（F40）：把一件事交给外部 AI CLI 跑，跑完再让她说一句。

为什么单独一个文件：这条链路的全部内容是「记一行状态 + 起一个子进程 + 收尸」，
和 service.py 里那些装配逻辑没有关系。hub 起来时不连、不占端口、不建线程池，
只有真的派了任务才有一个线程在等子进程。

状态落在 hub.yaml 同目录的 ``dispatch-tasks.jsonl``：一行一个快照，
同一个 ``task_id`` 后写的覆盖前面的，读的时候取最后一行。这样 hub 重启后
还知道「上次那些任务到底怎么结束的」，不用猜。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

PLACEHOLDER = "{task}"
MAX_TASK_CHARS = 2000
TAIL_KEEP = 200


def dispatch_path(config_path: Path | None) -> Path | None:
    """和 companion-chats.jsonl 放一起，都在 hub.yaml 同目录。"""

    if config_path is None:
        return None
    return config_path.with_name("dispatch-tasks.jsonl")


def build_argv(runner: list[str], task: str) -> list[str]:
    """把 runner 模板里的 ``{task}`` 换成任务原文。单占位、不做 shell 展开。

    不用 shell=True：任务原文里出现 ``;`` ``$()`` 时，走 shell 等于让人人都能
    在 hub 机器上执行任意命令——这条链路的入口是 HTTP。
    """

    return [part.replace(PLACEHOLDER, task) for part in runner]


def start(argv: list[str], cwd: str) -> subprocess.Popen[str]:
    """起子进程。新会话 + 新进程组：收尸时 killpg 只杀这一棵树，不连累 hub。"""

    return subprocess.Popen(
        argv,
        cwd=cwd or None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def collect(
    proc: subprocess.Popen[str], *, timeout_seconds: int, max_chars: int
) -> tuple[str, int | None, str, bool]:
    """等结果。返回 (状态, 退出码, 正文, 是否被截断)。

    退出码 0 不算成功：dsh 缺 key 时会空跑完退 0，stdout 是空的。所以
    ``done`` 的条件是「退 0 且真的吐了字」，其余一律 ``failed``。
    超时先 SIGTERM 给 5 秒体面退出，再 SIGKILL。
    """

    try:
        out, err = proc.communicate(timeout=max(1, timeout_seconds))
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = "", ""
        return "timeout", proc.returncode, "", False

    text = (out or "").strip()
    if not text:
        # 没吐字就把 stderr 最后一段带上：缺 key、模型 404 都在这
        text = (err or "").strip()
    truncated = len(text) > max_chars
    if truncated:
        text = text[: max(200, max_chars)] + "…（后面还有，截了）"
    status = "done" if proc.returncode == 0 and (out or "").strip() else "failed"
    return status, proc.returncode, text, truncated


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """按进程组杀。拿不到组号（子进程已退出）就退回到杀单个 pid。"""

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


def kill_pid(pid: int) -> bool:
    """探活 + 按组杀。给 hub 重启后的收尾用。"""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return False
    return True


class DispatchStore:
    """追加写的任务台账。内存里留每个任务的最新快照，读状态是 O(1)。"""

    def __init__(self, path: Path | None, *, max_kept: int = TAIL_KEEP) -> None:
        self.path = path
        self.max_kept = max(10, int(max_kept))
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("task_id"):
                self._tasks[str(obj["task_id"])] = obj

    def put(self, record: dict[str, Any]) -> dict[str, Any]:
        """写一条快照（和已有字段合并），返回合并后的完整记录。"""

        task_id = str(record.get("task_id") or "")
        if not task_id:
            raise ValueError("task_id 不能为空")
        with self._lock:
            merged = dict(self._tasks.get(task_id) or {})
            merged.update(record)
            self._tasks[task_id] = merged
            self._append_unlocked(merged)
            self._trim_unlocked()
            return dict(merged)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(str(task_id or ""))
            return dict(task) if task else None

    def unfinished(self) -> list[dict[str, Any]]:
        """还没收尾的任务（hub 重启后要挨个杀掉）。"""

        with self._lock:
            return [
                dict(task)
                for task in self._tasks.values()
                if task.get("status") in ("queued", "running")
            ]

    def _append_unlocked(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _trim_unlocked(self) -> None:
        """台账只留最近 max_kept 个任务：它是状态缓存，不是账本。"""

        if self.path is None or len(self._tasks) <= self.max_kept:
            return
        ordered = sorted(
            self._tasks.items(),
            key=lambda item: str(item[1].get("created_at") or ""),
        )
        for task_id, _ in ordered[: len(ordered) - self.max_kept]:
            self._tasks.pop(task_id, None)
        lines = [
            json.dumps(task, ensure_ascii=False, separators=(",", ":")) for task in self._tasks.values()
        ]
        try:
            self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except OSError:
            pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

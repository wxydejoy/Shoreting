"""书桌状态落盘：latest JSON + 按日 jsonl。Hub 上报 Windows，本机写 Mini。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helm_mini.sampler import utc_now

DEFAULT_ROOT = Path.home() / ".config" / "helm-mini"
KEEP_DAYS = 2


def default_root() -> Path:
    return DEFAULT_ROOT


class TelemetryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = self._load_latest()

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._latest))

    def touch_mini(self, pc: dict[str, Any], *, persist_log: bool = False) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            latest = dict(self._latest)
            latest["mini"] = {"pc": pc, "updated_at": now}
            latest["updated_at"] = now
            self._commit(latest, persist_log=persist_log)
            return json.loads(json.dumps(latest))

    def ingest_windows(
        self,
        body: dict[str, Any],
        mini_pc: dict[str, Any],
        *,
        persist_log: bool = False,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            latest = dict(self._latest)
            win = latest.get("windows") if isinstance(latest.get("windows"), dict) else {}
            for key in ("pc", "media", "devices", "temperature"):
                if key in body:
                    win[key] = body[key]
            win["updated_at"] = now
            latest["windows"] = win
            latest["mini"] = {"pc": mini_pc, "updated_at": now}
            latest["updated_at"] = now
            self._commit(latest, persist_log=persist_log)
            return json.loads(json.dumps(latest))

    def _commit(self, latest: dict[str, Any], *, persist_log: bool) -> None:
        self._latest = latest
        path = self.root / "desk-state.json"
        path.write_text(
            json.dumps(latest, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        if persist_log:
            self._append_jsonl(latest)

    def _append_jsonl(self, latest: dict[str, Any]) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log = self.root / "log"
        log.mkdir(parents=True, exist_ok=True)
        self._prune_logs(log)
        line = json.dumps(latest, ensure_ascii=False, separators=(",", ":"))
        with (log / f"desk-{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _prune_logs(self, log: Path) -> None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=KEEP_DAYS)
        for item in log.glob("desk-*.jsonl"):
            stamp = item.stem.removeprefix("desk-")
            try:
                day = datetime.strptime(stamp, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                try:
                    item.unlink()
                except OSError:
                    pass

    def _load_latest(self) -> dict[str, Any]:
        path = self.root / "desk-state.json"
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

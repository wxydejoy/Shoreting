from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from dock_hub.config import DeviceConfig
from dock_hub.errors import HubError

GENERIC_PROCESS_NAMES = {
    "cmd.exe",
    "cmd",
    "powershell.exe",
    "powershell",
    "pwsh.exe",
    "pwsh",
    "open",
    "explorer.exe",
    "python.exe",
    "pythonw.exe",
    "python",
    "python3",
    "launcher.exe",
    "wegame.exe",
}


def expand_path(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path.strip()))


def is_url(path: str) -> bool:
    lower = path.strip().lower()
    if lower.startswith(("http://", "https://", "steam://", "wegame://", "mailto:")):
        return True
    return "://" in path and not Path(path).exists()


def launch_exists(cfg: DeviceConfig) -> bool:
    program = cfg.program or ""
    expanded = expand_path(program)
    if is_url(expanded):
        return True
    candidate = Path(expanded)
    if candidate.exists():
        return True
    return shutil.which(expanded) is not None


def process_names(cfg: DeviceConfig) -> list[str]:
    if cfg.process:
        return [name.strip().lower() for name in cfg.process if name.strip()]
    program = cfg.program or ""
    expanded = expand_path(program)
    if is_url(expanded):
        return []
    leaf = expanded.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    if not leaf or leaf in GENERIC_PROCESS_NAMES:
        return []
    return [leaf]


def running_image_names() -> set[str]:
    names: set[str] = set()
    try:
        import psutil
    except Exception:
        return names
    try:
        for proc in psutil.process_iter(["name"]):
            raw = (proc.info or {}).get("name") or ""
            if raw:
                names.add(str(raw).lower())
    except Exception:
        return names
    return names


class FinishedWatch:
    """盯进程列表：谁不见了就记一笔「跑完了」。

    首扫只记基线——开机时早就退出的东西不该被当成「刚跑完」。
    `now` 由调用方传（time.monotonic()），纯状态，方便拿假时刻验。
    """

    def __init__(self) -> None:
        self._since: dict[str, float] = {}

    def diff(self, current: set[str], now: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in sorted(self._since):
            if name in current:
                continue
            ran = max(0.0, now - self._since[name])
            out.append({"process": name, "ran_sec": round(ran, 1)})
            del self._since[name]
        for name in current:
            self._since.setdefault(name, now)
        return out


def launch_running(cfg: DeviceConfig, running: set[str] | None = None) -> bool:
    wanted = process_names(cfg)
    if not wanted:
        return False
    images = running if running is not None else running_image_names()
    return any(name in images for name in wanted)


def launch(cfg: DeviceConfig) -> None:
    program = cfg.program or ""
    extra = list(cfg.args or [])
    workdir = expand_path(cfg.cwd) if cfg.cwd else None
    expanded = expand_path(program)

    if is_url(expanded):
        if extra:
            raise HubError("action_error", "URL / 协议链接不能带 args")
        _startfile(expanded, workdir)
        return

    resolved = _resolve(expanded)
    if workdir is None and resolved.suffix and resolved.parent.exists():
        workdir = str(resolved.parent)

    suffix = resolved.suffix.lower()
    name = resolved.name.lower()
    if suffix == ".ps1":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(resolved),
            *extra,
        ]
        _popen(command, workdir, console=True, wait=cfg.wait, timeout_sec=cfg.timeout_sec)
        return

    if name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        command = [str(resolved), *extra]
        _popen(command, workdir, console=True, wait=cfg.wait, timeout_sec=cfg.timeout_sec)
        return

    if name in {"cmd", "cmd.exe"} or suffix in {".bat", ".cmd"}:
        command = [str(resolved), *extra]
        _popen(command, workdir, console=True, wait=cfg.wait, timeout_sec=cfg.timeout_sec)
        return

    command = [str(resolved), *extra]
    if os.name == "nt" and not cfg.wait:
        _startfile_exe(resolved, extra, workdir)
        return
    _popen(command, workdir, console=False, wait=cfg.wait, timeout_sec=cfg.timeout_sec)


def _resolve(expanded: str) -> Path:
    candidate = Path(expanded)
    if candidate.exists():
        return candidate
    which = shutil.which(expanded)
    if which:
        return Path(which)
    raise HubError("offline", f"程序不存在：{expanded}")


def _startfile(target: str, cwd: str | None) -> None:
    if os.name != "nt":
        _popen([target], cwd, console=False, wait=False, timeout_sec=8)
        return
    kwargs: dict = {}
    if cwd:
        kwargs["cwd"] = cwd
    try:
        os.startfile(target, **kwargs)  # type: ignore[attr-defined]
    except OSError as exc:
        raise HubError("action_error", _launch_os_error(exc)) from exc


def _startfile_exe(resolved: Path, extra: list[str], cwd: str | None) -> None:
    kwargs: dict = {}
    if extra:
        kwargs["arguments"] = subprocess.list2cmdline(extra)
    if cwd:
        kwargs["cwd"] = cwd
    try:
        os.startfile(str(resolved), **kwargs)  # type: ignore[attr-defined]
    except OSError as exc:
        raise HubError("action_error", _launch_os_error(exc)) from exc


def _launch_os_error(exc: OSError) -> str:
    if getattr(exc, "winerror", None) == 740:
        return "需要管理员权限才能启动"
    return f"无法启动：{exc}"


def _popen(
    command: list[str],
    cwd: str | None,
    console: bool,
    wait: bool,
    timeout_sec: float,
) -> None:
    kwargs: dict = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if console:
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            flags |= subprocess.DETACHED_PROCESS
        kwargs["creationflags"] = flags
        kwargs["close_fds"] = False
    else:
        kwargs["start_new_session"] = True
        kwargs["close_fds"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        raise HubError("action_error", _launch_os_error(exc)) from exc
    if not wait:
        return
    try:
        code = proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise HubError("action_error", f"脚本超时（{int(timeout_sec)} 秒）") from exc
    if code not in (0, None):
        raise HubError("action_error", f"脚本退出码 {code}")

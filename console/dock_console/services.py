"""Start, stop, and restart the Mac LaunchAgents this repo installs."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dock_console.windows import (
    display_address,
    normalize_windows_address,
    read_probe_file,
    windows_online,
    write_probe_file,
)


@dataclass(frozen=True)
class ServiceSpec:
    id: str
    name: str
    blurb: str
    label: str
    port: int
    health_url: str
    log_path: Path
    prefer: tuple[str, str]
    install: str


@dataclass(frozen=True)
class Plan:
    ok: bool
    commands: list[list[str]]
    message: str


Runner = Callable[..., Any]
Probe = Callable[[str], tuple[bool, dict[str, Any] | None, str | None]]


def service_specs(repo: Path) -> tuple[ServiceSpec, ...]:
    return (
        ServiceSpec(
            id="hub",
            name="Hub",
            blurb="门口。米家、伴侣和配置向导。",
            label="cn.weiekko.dock-hub",
            port=17890,
            health_url="http://127.0.0.1:17890/health",
            log_path=repo / "hub" / "dock-hub.log",
            prefer=("user", "gui"),
            install="hub/install-launchd.sh",
        ),
        ServiceSpec(
            id="mini",
            name="Mini 监控",
            blurb="主屏第二行的 CPU、内存和 GPU。",
            label="cn.weiekko.helm-mini",
            port=17891,
            health_url="http://127.0.0.1:17891/health",
            log_path=repo / "mini" / "helm-mini.log",
            prefer=("gui", "user"),
            install="mini/install-launchd.sh",
        ),
        ServiceSpec(
            id="tts",
            name="守岸人语音",
            blurb="本机喇叭。模型起来后探活才会变成就绪。",
            label="cn.weiekko.companion-tts",
            port=18100,
            health_url="http://127.0.0.1:18100/health",
            log_path=repo / "companion" / "tts" / "tts.log",
            prefer=("gui", "user"),
            install="companion/tts/install-launchd.sh",
        ),
        ServiceSpec(
            id="console",
            name="控制台",
            blurb="这个页面自己。常驻后 Win 托盘单击就能打开。",
            label="cn.weiekko.dock-console",
            port=17880,
            health_url="http://127.0.0.1:17880/health",
            log_path=repo / "console" / "dock-console.log",
            prefer=("user", "gui"),
            install="console/install-launchd.sh",
        ),
    )


def parse_launchctl_print(text: str) -> tuple[str | None, int | None]:
    state: str | None = None
    pid: int | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if state is None and line.startswith("state ="):
            state = line.split("=", 1)[1].strip() or None
        elif pid is None and line.startswith("pid ="):
            raw_pid = line.split("=", 1)[1].strip()
            try:
                pid = int(raw_pid)
            except ValueError:
                pid = None
    return state, pid


def plan_action(
    action: str,
    *,
    loaded: bool,
    running: bool,
    domain: str,
    label: str,
    plist: Path,
    installed: bool,
) -> Plan:
    target = f"{domain}/{label}"
    if action == "stop":
        if not loaded:
            return Plan(True, [], "已经是关闭的")
        return Plan(
            True,
            [["launchctl", "bootout", target]],
            "已关闭。这次登录里不会自己起来，下次登录仍会开机启动。",
        )
    if action == "start":
        if running:
            return Plan(True, [], "已经在运行")
        if not loaded and not installed:
            return Plan(False, [], "还没安装。请先运行 install-launchd.sh")
        if loaded:
            return Plan(True, [["launchctl", "kickstart", target]], "已启动")
        return Plan(True, _bootstrap(domain, label, plist), "已启动")
    if action == "restart":
        if loaded:
            return Plan(True, [["launchctl", "kickstart", "-k", target]], "已重启")
        if not installed:
            return Plan(False, [], "还没安装。请先运行 install-launchd.sh")
        return Plan(True, _bootstrap(domain, label, plist), "原先是关的，已改为启动")
    return Plan(False, [], "不支持这个操作")


def _bootstrap(domain: str, label: str, plist: Path) -> list[list[str]]:
    return [
        ["launchctl", "bootstrap", domain, str(plist)],
        ["launchctl", "enable", f"{domain}/{label}"],
    ]


def run_launchctl(args: list[str], timeout: float = 20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def default_probe(url: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=1.2) as response:
            payload = json.loads(response.read(8192).decode())
    except Exception as exc:
        return False, None, str(exc)
    if not isinstance(payload, dict):
        return False, None, "探活返回的不是对象"
    return bool(payload.get("ok")), payload, None


def _tail(path: Path, lines: int = 24, max_bytes: int = 8192) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _summary(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    parts: list[str] = []
    if payload.get("service"):
        parts.append(str(payload["service"]))
    if payload.get("name"):
        parts.append(str(payload["name"]))
    if "ready" in payload:
        parts.append("就绪" if payload.get("ready") else "模型未就绪")
    if payload.get("play") is True:
        parts.append("会出声")
    if payload.get("error"):
        parts.append(str(payload["error"]))
    return " · ".join(parts)


def _state_label(*, installed: bool, loaded: bool, running: bool, health_ok: bool) -> str:
    if not installed and not loaded:
        return "未安装"
    if running and health_ok:
        return "运行中"
    if running:
        return "进程在，端口未就绪"
    if loaded:
        return "已装载"
    return "已关闭"


class Controller:
    def __init__(
        self,
        *,
        repo: Path,
        home: Path,
        uid: int,
        run: Runner,
        probe: Probe | None = None,
        hub_yaml: Path | None = None,
    ) -> None:
        self.repo = repo
        self.home = home
        self.uid = uid
        self.run = run
        self.probe = probe or default_probe
        self.hub_yaml = hub_yaml

    def snapshot(self) -> list[dict[str, Any]]:
        return [self._row(spec) for spec in service_specs(self.repo)]

    def apply(self, service_id: str, action: str) -> dict[str, Any]:
        spec = self._spec(service_id)
        loaded, domain, state, _pid = self._locate(spec)
        plist = self._plist(spec)
        plan = plan_action(
            action,
            loaded=loaded,
            running=state == "running",
            domain=domain,
            label=spec.label,
            plist=plist,
            installed=plist.is_file(),
        )
        if not plan.ok:
            return {"ok": False, "id": spec.id, "action": action, "message": plan.message}
        for command in plan.commands:
            result = self.run(command, timeout=30)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "launchctl 失败").strip()
                return {"ok": False, "id": spec.id, "action": action, "message": detail}
        return {"ok": True, "id": spec.id, "action": action, "message": plan.message}

    def set_windows_address(self, address: str) -> dict[str, Any]:
        if self.hub_yaml is None:
            return {"ok": False, "message": "没有 hub.yaml 路径"}
        try:
            url = normalize_windows_address(address)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        current = read_probe_file(self.hub_yaml)
        if current == url:
            return {"ok": True, "url": url, "message": "地址没变"}
        try:
            write_probe_file(self.hub_yaml, url)
        except FileNotFoundError:
            return {"ok": False, "message": "找不到 hub.yaml"}
        except OSError as exc:
            return {"ok": False, "message": f"写不进 hub.yaml：{exc}"}
        restarted = self.apply("hub", "restart")
        if restarted["ok"]:
            return {"ok": True, "url": url, "message": f"已改成 {url}，Hub 已重启"}
        return {
            "ok": False,
            "url": url,
            "message": f"地址已写成 {url}，但 Hub 没重启：{restarted['message']}",
        }

    def apply_all(self, action: str) -> dict[str, Any]:
        ids = [spec.id for spec in service_specs(self.repo)]
        if action == "stop":
            ids.reverse()
        results = [self.apply(service_id, action) for service_id in ids]
        ok = all(item["ok"] for item in results)
        return {
            "ok": ok,
            "action": action,
            "results": results,
            "message": "完成" if ok else "有服务没成功",
        }

    def _spec(self, service_id: str) -> ServiceSpec:
        for spec in service_specs(self.repo):
            if spec.id == service_id:
                return spec
        raise KeyError(service_id)

    def _plist(self, spec: ServiceSpec) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{spec.label}.plist"

    def _domains(self, spec: ServiceSpec) -> list[str]:
        ordered: list[str] = []
        for kind in (*spec.prefer, "gui", "user"):
            domain = f"{kind}/{self.uid}"
            if domain not in ordered:
                ordered.append(domain)
        return ordered

    def _locate(self, spec: ServiceSpec) -> tuple[bool, str, str | None, int | None]:
        domains = self._domains(spec)
        for domain in domains:
            result = self.run(["launchctl", "print", f"{domain}/{spec.label}"], timeout=8)
            if result.returncode == 0:
                state, pid = parse_launchctl_print(result.stdout or "")
                return True, domain, state, pid
        return False, domains[0], None, None

    def _row(self, spec: ServiceSpec) -> dict[str, Any]:
        loaded, domain, state, pid = self._locate(spec)
        plist = self._plist(spec)
        installed = plist.is_file()
        health_ok, payload, health_error = self.probe(spec.health_url)
        running = state == "running"
        try:
            log_file = str(spec.log_path.relative_to(self.repo))
        except ValueError:
            log_file = str(spec.log_path)
        row = {
            "id": spec.id,
            "name": spec.name,
            "blurb": spec.blurb,
            "label": spec.label,
            "port": spec.port,
            "install": spec.install,
            "log_file": log_file,
            "loaded": loaded,
            "domain": domain if loaded else None,
            "state": state,
            "pid": pid,
            "installed": installed,
            "health_ok": health_ok,
            "health_summary": _summary(payload),
            "health_error": None if health_ok else health_error,
            "log_tail": _tail(spec.log_path),
            "state_label": _state_label(
                installed=installed,
                loaded=loaded,
                running=running,
                health_ok=health_ok,
            ),
        }
        if spec.id == "hub":
            row["windows"] = self._windows_view()
        return row

    def _windows_view(self) -> dict[str, Any]:
        url = read_probe_file(self.hub_yaml) if self.hub_yaml else None
        if not url:
            return {"url": "", "online": False, "label": "还没填"}
        online = windows_online(url)
        return {
            "url": url,
            "address": display_address(url),
            "online": online,
            "label": "探针在线" if online else "探针没响应",
        }

from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from dock_hub import PROTOCOL
from dock_hub.chat_log import ChatLog, chat_log_path
from dock_hub.companion import Companion


class _StorySpeaker:
    def __init__(self, companion: Companion) -> None:
        self._companion = companion

    def cue(self, text: str) -> bool:
        return self._companion.cue_story_sentence(text)

    def stop(self) -> None:
        self._companion.stop_story_audio()

    def cancel_short_reads(self) -> None:
        self._companion.begin_story_audio()
from dock_hub.config import DeviceConfig, HubConfig, MediaConfig, MiniLinkConfig, PcConfig, PropConfig, TemperatureConfig, default_config_path
from dock_hub.errors import HubError
from dock_hub.metrics import METRICS
from dock_hub.launch import launch, launch_exists, launch_running, running_image_names
from dock_hub.ledstrip import apply_config, percent_to_raw, raw_to_percent, read_config
from dock_hub.pc import PcSampler, utc_now
from dock_hub.ratelimit import Gate
from dock_hub.media import MEDIA_ACTIONS, MediaSession
from dock_hub.reporter import DeskReporter
from dock_hub.schedule import Scheduler
from dock_hub.mijia_bridge import (
    BoundDevice,
    MijiaSession,
    as_bool,
    as_brightness,
    bind_from_config,
    coerce_write,
    error_message,
    is_offline,
    is_ok,
    lookup,
    query_params,
    read_batch,
    result_map,
    updated_at,
)


def _safe_next_path(raw: str | None) -> str:
    text = str(raw or "/setup").strip() or "/setup"
    if not text.startswith("/") or text.startswith("//") or "://" in text or "\\" in text:
        return "/setup"
    return text


class DockHub:
    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self._change_cond = threading.Condition()
        self._change_seq = 0
        self.session: MijiaSession | None = None
        self._mijia_lock = threading.Lock()
        self._device_locks: dict[str, threading.Lock] = {}
        self._device_locks_guard = threading.Lock()
        self._last_temp: dict[str, Any] | None = None
        self._login_message = "请在配置向导中扫码登录米家"
        self._last_run_at: dict[str, str] = {}
        self._led_state: dict[str, dict[str, Any]] = {}
        self._led_restore: dict[str, int] = {}
        self._load_led_restore()
        pc_cfg = config.pc or PcConfig(enabled=False)
        self.pc = PcSampler(
            enabled=pc_cfg.enabled and not self.uses_probe(),
            sample_ms=pc_cfg.sample_ms,
            cpu_temp=pc_cfg.cpu_temp,
            gpu=pc_cfg.gpu,
        )
        self.pc.start()
        self._probe_lock = threading.Lock()
        self._remote_pc: dict[str, Any] | None = None
        self._remote_running: set[str] = set()
        self._remote_at = 0.0
        self._desk_pc_online: bool | None = None
        self._ticket_lock = threading.Lock()
        self._login_tickets: dict[str, tuple[float, str]] = {}
        # 没开限流时是 None，调用方按「不限」处理，不用到处判断开关
        self.gate = Gate.from_config(config.limits)
        media_cfg = config.media or MediaConfig(enabled=True)
        self.media = MediaSession(enabled=media_cfg.enabled)
        self.companion = Companion(config.companion)
        self.chat_log = ChatLog(chat_log_path(config.path))
        self.companion.set_chat_log(self.chat_log)
        self.companion.set_reads_dir((config.path or default_config_path()).parent / "reads")
        self._attach_books()
        self._desk_lock = threading.Lock()
        self._desk_temp: dict[str, Any] | None = None
        self._desk_devices: list[dict[str, Any]] = []
        self._mijia_at = 0.0
        self._refreshing_mijia = False
        mini_cfg = config.mini or MiniLinkConfig(enabled=False)
        self.reporter = DeskReporter(mini_cfg, self._windows_payload)
        self._wire_companion()
        self.reporter.start()
        self.scheduler = Scheduler(config.schedules, self._run_schedule)
        self.scheduler.start()

    def _attach_books(self) -> None:
        from dock_hub.books import Books

        root = (self.config.path or default_config_path()).parent / "books"
        existing = getattr(self, "books", None)
        if existing is None:
            self.books = Books(root, speaker=_StorySpeaker(self.companion))
        else:
            existing.set_root(root)
        self.books.voice_speed = self._voice_speed
        self.companion.set_story_books(self.books)

    def _voice_speed(self) -> float:
        comp = self.config.companion
        if comp is None:
            return 1.0
        return float(comp.voice_speed or 1.0)

    def login(self, *, force: bool = False) -> None:
        try:
            from mijiaAPI import mijiaAPI

            from dock_hub.mijia_bridge import clear_mijia_auth

            if force:
                clear_mijia_auth()
                with self._mijia_lock:
                    self.session = None
            client = mijiaAPI()
            if not client.available:
                self.session = None
                self._login_message = "请在配置向导中扫码登录米家"
                return
            with self._mijia_lock:
                self.session = bind_from_config(client, self.config.devices, self.config.temperature)
            self._login_message = "请在配置向导中扫码登录米家"
            self._wire_companion()
        except Exception as exc:
            self.session = None
            self._login_message = f"米家登录失败：{exc}"
            print(self._login_message)
            return
        self._print_bind_warnings()

    def relogin(self) -> None:
        """清除本地米家缓存（扫码在配置向导内完成）。"""
        print("已清除米家登录，请在配置向导扫码。", flush=True)
        from dock_hub.mijia_bridge import clear_mijia_auth

        clear_mijia_auth()
        with self._mijia_lock:
            self.session = None
        self._login_message = "请在配置向导中扫码登录米家"

    def reload_config(self, config: HubConfig) -> None:
        """Apply hub.yaml changes without restarting the HTTP server."""
        self.config = config
        with self._mijia_lock:
            self.session = None
        pc_cfg = config.pc or PcConfig(enabled=False)
        self.pc.stop()
        self.pc = PcSampler(
            enabled=pc_cfg.enabled and not self.uses_probe(),
            sample_ms=pc_cfg.sample_ms,
            cpu_temp=pc_cfg.cpu_temp,
            gpu=pc_cfg.gpu,
        )
        self.pc.start()
        with self._probe_lock:
            self._remote_pc = None
            self._remote_running = set()
            self._remote_at = 0.0
        self._desk_pc_online = None
        media_cfg = config.media or MediaConfig(enabled=True)
        self.media = MediaSession(enabled=media_cfg.enabled)
        self.companion.configure(config.companion)
        self.reporter.stop()
        mini_cfg = config.mini or MiniLinkConfig(enabled=False)
        self.reporter = DeskReporter(mini_cfg, self._windows_payload)
        self._wire_companion()
        self.reporter.start()
        self.scheduler.stop()
        self.scheduler = Scheduler(config.schedules, self._run_schedule)
        self.scheduler.start()
        threading.Thread(target=self.login, name="mijia-reload", daemon=True).start()

    def _print_bind_warnings(self) -> None:
        if self.session is None:
            return
        if self.session.temperature and self.session.temperature.missing:
            print(f"温度源未绑定：{self.session.temperature.missing}")
        for bound in self.session.devices.values():
            if bound.missing and not bound.voice_only:
                print(f"设备 {bound.cfg.id} 未绑定：{bound.missing}")

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "dock-hub",
            "protocol": PROTOCOL,
            "name": self.config.name,
        }

    def mint_login_ticket(self, next_path: str | None = None) -> dict[str, Any]:
        nxt = _safe_next_path(next_path)
        ticket = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._ticket_lock:
            expired = [key for key, (exp, _nxt) in self._login_tickets.items() if exp <= now]
            for key in expired:
                self._login_tickets.pop(key, None)
            self._login_tickets[ticket] = (now + 60.0, nxt)
        from urllib.parse import quote

        path = f"/chats/claim?ticket={quote(ticket, safe='')}&next={quote(nxt, safe='/')}"
        return {"ok": True, "ticket": ticket, "path": path}

    def claim_login_ticket(self, ticket: str) -> str | None:
        ident = str(ticket or "").strip()
        if not ident:
            return None
        now = time.monotonic()
        with self._ticket_lock:
            item = self._login_tickets.pop(ident, None)
        if item is None:
            return None
        exp, nxt = item
        if exp <= now:
            return None
        return nxt

    def uses_probe(self) -> bool:
        return bool((self.config.probe.url or "").strip())

    def ingest_telemetry(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HubError("bad_request", "JSON 无效")
        raw_pc = body.get("pc")
        sample = dict(raw_pc) if isinstance(raw_pc, dict) else {}
        sample["online"] = bool(sample.get("online", True))
        sample.setdefault("updated_at", utc_now())
        names: set[str] = set()
        running_raw = body.get("running") or []
        if isinstance(running_raw, list):
            names = {str(item).strip().lower() for item in running_raw if str(item).strip()}
        with self._probe_lock:
            self._remote_pc = sample
            self._remote_running = names
            self._remote_at = time.monotonic()
        self._set_desk_pc_online(bool(sample.get("online")))
        self.note_change()
        return {"ok": True}

    def pc_snapshot(self) -> dict[str, Any] | None:
        if not self.uses_probe():
            return self.pc.snapshot()
        stale = max(0.2, self.config.probe.stale_ms / 1000.0)
        with self._probe_lock:
            age = time.monotonic() - self._remote_at if self._remote_at else None
            sample = dict(self._remote_pc) if self._remote_pc else None
        if age is None or sample is None or age > stale:
            self._set_desk_pc_online(False)
            return {"online": False, "updated_at": utc_now()}
        self._set_desk_pc_online(bool(sample.get("online")))
        return sample

    def _running_names(self) -> set[str]:
        if self.uses_probe():
            with self._probe_lock:
                return set(self._remote_running)
        return running_image_names()

    def _set_desk_pc_online(self, on: bool) -> None:
        if self._desk_pc_online is on:
            return
        self._desk_pc_online = on
        self.follow_pc(on)
        self.note_change()

    def snapshot(self) -> dict[str, Any]:
        started = time.perf_counter()
        status, message, temperature, devices = self._mijia_desk_slice()
        body = self._snapshot_body(status, message, temperature, devices)
        # 手机每 3s 来一次，这是最容易拖慢体验的路径，所以只盯它一个
        METRICS.observe("hub_snapshot_ms", round((time.perf_counter() - started) * 1000.0, 1))
        return body

    def metrics_text(self) -> str:
        """给 /metrics：计数 + 当前状态。状态现算，不缓存。"""
        status, _message = self._mijia_status()
        gauges = {
            "hub_mijia_login_ok": 1.0 if status == "ok" else 0.0,
            "hub_devices_configured": float(len(self.config.devices)),
            "hub_companion_enabled": 1.0 if self.companion.config.enabled else 0.0,
        }
        return METRICS.render(gauges)

    def _mijia_desk_slice(
        self,
    ) -> tuple[str, str | None, dict[str, Any] | None, list[dict[str, Any]]]:
        running = self._running_names()
        status, message = self._mijia_status()
        if status == "login_required":
            return status, message, None, self._collect_devices(running, [])

        assert self.session is not None
        try:
            with self._mijia_lock:
                params = query_params(self.session)
                rows = read_batch(self.session.api, params) if params else []
            mapped = result_map(rows)
        except Exception as exc:
            self.session.last_error = str(exc)
            extra = [
                self._device_from_cache(bound, online=False)
                for bound in self.session.devices.values()
                if not bound.voice_only
            ]
            return "error", f"米家调用失败：{exc}", None, self._collect_devices(running, extra)

        temperature = self._temperature_from_map(mapped)
        extra = [
            self._device_from_map(bound, mapped)
            for bound in self.session.devices.values()
            if not bound.voice_only
        ]
        running = self._running_names()
        devices = self._collect_devices(running, extra)
        if temperature:
            devices = self._overlay_temperature(devices, temperature)
        return "ok", None, temperature, devices

    def _collect_devices(self, running: set[str] | None, extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {item["id"]: item for item in extra}
        devices: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cfg in self.config.devices:
            if cfg.is_action:
                devices.append(self._action_payload(cfg, running))
            elif cfg.is_ws2812:
                devices.append(self._ws2812_payload(cfg))
            elif cfg.id in by_id:
                devices.append(by_id[cfg.id])
            elif cfg.is_sensor:
                devices.append(self._sensor_placeholder(cfg))
            elif cfg.is_mijia:
                devices.append(self._mijia_placeholder(cfg))
            else:
                continue
            seen.add(cfg.id)
        for item in extra:
            if item["id"] not in seen:
                devices.append(item)
                seen.add(item["id"])
        temp = self.config.temperature
        if temp is not None and temp.id not in seen:
            live = by_id.get(temp.id)
            devices.append(live or self._sensor_from_temperature(temp, self._last_temp if self._last_temp and self._last_temp.get("id") == temp.id else None))
        return devices

    def _overlay_temperature(
        self,
        devices: list[dict[str, Any]],
        temperature: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ident = str(temperature.get("id") or "")
        cfg = self.config.temperature
        if not ident or cfg is None:
            return devices
        updated = self._sensor_from_temperature(cfg, temperature)
        out: list[dict[str, Any]] = []
        replaced = False
        for item in devices:
            if item.get("id") == ident:
                out.append(updated)
                replaced = True
            else:
                out.append(item)
        if not replaced:
            out.append(updated)
        return out

    def _wire_companion(self) -> None:
        skills = []
        for cfg in self.config.devices:
            if cfg.is_mijia:
                item: dict[str, Any] = {"id": cfg.id, "name": cfg.name, "kind": "toggle"}
                if cfg.type == "light" and cfg.brightness_prop:
                    item["brightness"] = True
                temp_prop = next(
                    (
                        prop
                        for prop in cfg.effective_props()
                        if prop.write
                        and (
                            "temp" in prop.id
                            or "temperature" in (prop.prop or "")
                        )
                    ),
                    None,
                )
                if temp_prop is not None:
                    item["temp"] = True
                    item["temp_id"] = temp_prop.id
                    if temp_prop.min is not None:
                        item["temp_min"] = int(temp_prop.min)
                    if temp_prop.max is not None:
                        item["temp_max"] = int(temp_prop.max)
                skills.append(item)
            elif cfg.is_ws2812:
                skills.append({"id": cfg.id, "name": cfg.name, "kind": "toggle", "brightness": True})
            elif cfg.is_sensor:
                skills.append({"id": cfg.id, "name": cfg.name, "kind": "sensor"})
            elif cfg.is_action:
                skills.append({"id": cfg.id, "name": cfg.name, "kind": "run"})
        known = {item["id"] for item in skills}
        session = self.session
        if session is not None:
            for bound in session.devices.values():
                if not bound.voice_only or bound.cfg.id in known:
                    continue
                if bound.did is None or bound.on_prop is None:
                    continue
                item: dict[str, Any] = {
                    "id": bound.cfg.id,
                    "name": bound.cfg.name,
                    "kind": "toggle",
                }
                if bound.brightness_prop is not None:
                    item["brightness"] = True
                skills.append(item)
        aliases_path = (self.config.path or default_config_path()).with_name("companion-aliases.yaml")
        self.companion.set_reads_dir((self.config.path or default_config_path()).parent / "reads")
        self._attach_books()
        self.companion.set_skills(
            skills,
            self._run_companion_skill,
            aliases_path,
            world={
                "temperature": lambda: self._last_temp,
                "pc": self.pc_snapshot,
                "device": self._companion_device_state,
                "media": self.media.snapshot,
                "media_command": self.media.command,
                "volume": self.media.volume,
                "game": self._game_running,
                "desk": self.reporter.snapshot,
            },
        )

    def _run_schedule(self, sched: Any) -> None:
        """到点了：按顺序开关设备，最后再开口（说话最慢，放最后才不挡动作）。"""

        for ident in getattr(sched, "on", []) or []:
            self.command(ident, {"on": True})
        for ident in getattr(sched, "off", []) or []:
            self.command(ident, {"on": False})
        say = str(getattr(sched, "say", "") or "").strip()
        if say:
            self.companion_announce({"text": say})

    def note_presence(self) -> None:
        self.companion.note_presence()

    def note_change(self) -> None:
        """状态变了就吱一声：挂着长轮询的 snapshot 会立刻醒过来。"""

        with self._change_cond:
            self._change_seq += 1
            self._change_cond.notify_all()

    def snapshot_seq(self) -> int:
        with self._change_cond:
            return self._change_seq

    def wait_change(self, since: int, timeout: float) -> bool:
        """等到 seq 变了或超时。True 表示期间真有变化。"""

        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._change_cond:
            if self._change_seq != since:
                return True
            while self._change_seq == since:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._change_cond.wait(left)
            return True

    def _game_running(self) -> bool:
        actions = [cfg for cfg in self.config.devices if cfg.is_action]
        if not actions:
            return False
        running = self._running_names()
        if not running:
            return True
        return any(launch_running(cfg, running) for cfg in actions)

    def _companion_device_state(self, device_id: str) -> dict[str, Any] | None:
        cfg = None
        try:
            cfg = self.config.device(device_id)
        except HubError:
            cfg = None
        if cfg is not None and cfg.is_ws2812:
            item = self._ws2812_payload(cfg)
            return {
                "id": item["id"],
                "name": item["name"],
                "on": item.get("on"),
                "brightness": item.get("brightness"),
            }
        session = self.session
        bound = session.devices.get(device_id) if session is not None else None
        if bound is None:
            return None
        return {
            "id": bound.cfg.id,
            "name": bound.cfg.name,
            "on": bound.last_on,
            "brightness": bound.last_brightness,
        }

    def _run_companion_skill(
        self,
        device_id: str,
        on: bool,
        brightness: int | None = None,
        props: dict[str, Any] | None = None,
    ) -> None:
        cfg = self._lookup_device(device_id)
        if cfg.is_action:
            if not on:
                return
            self.command(device_id, {"run": True})
            return
        body: dict[str, Any] = {}
        if props:
            body["props"] = props
            if on:
                body["on"] = True
        elif brightness is not None:
            body["brightness"] = brightness
            if on:
                body["on"] = True
        else:
            body["on"] = on
        self.command(device_id, body)

    def companion_chat(self, body: dict[str, Any] | None) -> dict[str, Any]:
        out = self.companion.chat(body or {}, "")
        self.note_change()
        return out

    def companion_chats(self, *, limit: int = 200, query: str = "") -> dict[str, Any]:
        return {"items": self.chat_log.list(limit=limit, query=query)}

    def companion_audio(self, audio_id: str) -> bytes:
        return self.companion.audio(audio_id)

    def companion_stop(self) -> dict[str, Any]:
        out = self.companion.stop()
        self.note_change()
        return out

    def companion_announce(self, body: dict[str, Any] | None) -> dict[str, Any]:
        out = self.companion.announce(body or {})
        self.note_change()
        return out

    def companion_read(self, body: dict[str, Any] | None) -> dict[str, Any]:
        out = self.companion.read(body or {})
        self.note_change()
        return out

    def companion_read_jobs(self) -> dict[str, Any]:
        return self.companion.read_jobs()

    def companion_read_job_action(self, ident: str, action: str) -> dict[str, Any]:
        out = self.companion.read_job_action(ident, action)
        self.note_change()
        return out

    def _lookup_device(self, device_id: str) -> DeviceConfig:
        try:
            return self.config.device(device_id)
        except HubError:
            bound = self.session.devices.get(device_id) if self.session else None
            if bound is not None:
                return bound.cfg
            raise

    def command(self, device_id: str, body: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(body, dict) or not body:
            raise HubError("bad_request", "命令不能为空")
        payload = self._command_locked(device_id, body)
        self.note_change()
        return payload

    def _command_locked(self, device_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if device_id == "media":
            return self._command_media(body)
        cfg = self._lookup_device(device_id)
        lock = self._lock_for(device_id)
        with lock:
            if cfg.is_action:
                return self._command_action(cfg, body)
            if cfg.is_ws2812:
                return self._command_ws2812(cfg, body)
            return self._command_mijia(cfg, body)

    def _command_media(self, body: dict[str, Any]) -> dict[str, Any]:
        if "on" in body or "run" in body or "brightness" in body:
            raise HubError("unsupported", "media 只接受 toggle / next / previous")
        action = body.get("media")
        if action not in MEDIA_ACTIONS:
            raise HubError("bad_request", "media 只接受 toggle / next / previous")
        return self.media.command(str(action))

    def _command_action(self, cfg: DeviceConfig, body: dict[str, Any]) -> dict[str, Any]:
        if "on" in body or "brightness" in body:
            raise HubError("unsupported", f"{cfg.name} 是启动项，不能开关或调亮度")
        if "media" in body:
            raise HubError("unsupported", f"{cfg.name} 不支持 media")
        if "run" not in body:
            raise HubError("bad_request", "启动项请发送 {\"run\": true}")
        if body["run"] is not True:
            raise HubError("unsupported", "run 必须为 true")
        payload = self._action_payload(cfg)
        if not payload["online"]:
            if self.uses_probe():
                raise HubError("offline", "电脑未开机")
            raise HubError("offline", f"{cfg.name} 的程序不存在")
        try:
            if self.uses_probe():
                self._probe_launch(cfg)
            else:
                launch(cfg)
        except HubError:
            raise
        except Exception as exc:
            raise HubError("action_error", f"无法启动 {cfg.name}：{exc}") from exc
        self._last_run_at[cfg.id] = utc_now()
        return self._action_payload(cfg)

    def _probe_launch(self, cfg: DeviceConfig) -> None:
        base = (self.config.probe.url or "").rstrip("/")
        if not base:
            raise HubError("offline", "未配置 Windows 探针")
        token = (self.config.probe.token or self.config.token).strip()
        url = base + "/v1/launch"
        raw = json.dumps({"id": cfg.id, "run": True}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
        timeout = max(2.0, float(cfg.timeout_sec or 8.0))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
            raise HubError("offline", "电脑未开机或探针无响应") from exc
        if isinstance(body, dict) and body.get("ok"):
            return
        message = ""
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "")
        raise HubError("action_error", message or f"无法启动 {cfg.name}")

    def follow_pc(self, on: bool) -> None:
        for cfg in self.config.devices:
            if not cfg.is_ws2812 or not cfg.follow_pc:
                continue
            try:
                if on:
                    remote = read_config(cfg.base_url or "", timeout_sec=cfg.timeout_sec)
                    raw = int(remote.get("brightness") or 0)
                    if raw > 0:
                        self._remember_led(cfg, remote, online=True)
                        self._led_restore[cfg.id] = raw
                        continue
                    self._command_ws2812(cfg, {"on": True})
                else:
                    self._command_ws2812(cfg, {"on": False})
            except HubError as exc:
                print(f"灯带联动失败（{cfg.name}）：{exc.message}", flush=True)
            except Exception as exc:
                print(f"灯带联动失败（{cfg.name}）：{exc}", flush=True)

    def _command_ws2812(self, cfg: DeviceConfig, body: dict[str, Any]) -> dict[str, Any]:
        if "run" in body:
            raise HubError("unsupported", f"{cfg.name} 不支持 run")
        if "media" in body:
            raise HubError("unsupported", f"{cfg.name} 不支持 media")
        if "on" not in body and "brightness" not in body:
            raise HubError("bad_request", "命令不能为空")

        brightness = body.get("brightness")
        if brightness is not None:
            if isinstance(brightness, float) and brightness.is_integer():
                brightness = int(brightness)
            if not isinstance(brightness, int) or isinstance(brightness, bool) or not (1 <= brightness <= 100):
                raise HubError("bad_request", "brightness 必须是 1–100 的整数")

        on = body.get("on")
        if on is not None and not isinstance(on, bool):
            raise HubError("bad_request", "on 必须是 true 或 false")

        base = cfg.base_url or ""
        timeout = cfg.timeout_sec
        current_raw = self._led_state.get(cfg.id, {}).get("raw")
        if current_raw is None:
            try:
                remote = read_config(base, timeout_sec=timeout)
                current_raw = int(remote.get("brightness") or 0)
                self._remember_led(cfg, remote, online=True)
            except HubError:
                current_raw = 0

        if on is False:
            if current_raw and current_raw > 0:
                self._note_led_restore(cfg.id, int(current_raw))
            apply_config(base, {"brightness": 0}, timeout_sec=timeout)
            return self._remember_led(cfg, {"brightness": 0}, online=True)

        raw = None
        if brightness is not None:
            raw = percent_to_raw(brightness)
            self._note_led_restore(cfg.id, raw)
        elif on is True:
            raw = int(self._led_restore.get(cfg.id) or cfg.on_brightness)
        if raw is None:
            raise HubError("bad_request", "命令不能为空")
        apply_config(base, {"brightness": raw}, timeout_sec=timeout)
        payload = {"brightness": raw}
        return self._remember_led(cfg, payload, online=True)

    def _remember_led(self, cfg: DeviceConfig, remote: dict[str, Any], *, online: bool) -> dict[str, Any]:
        try:
            raw = int(remote.get("brightness") or 0)
        except (TypeError, ValueError):
            raw = 0
        raw = max(0, min(255, raw))
        on = raw > 0
        percent = raw_to_percent(raw) if on else 0
        if on:
            self._note_led_restore(cfg.id, raw)
        state = {
            "online": online,
            "on": on,
            "brightness": percent if on else None,
            "raw": raw,
            "at": time.monotonic(),
        }
        self._led_state[cfg.id] = state
        return self._ws2812_payload(cfg, refresh=False)

    def _ws2812_payload(self, cfg: DeviceConfig, *, refresh: bool = True) -> dict[str, Any]:
        state = self._led_state.get(cfg.id)
        now = time.monotonic()
        if refresh and (state is None or now - float(state.get("at") or 0) > 15):
            try:
                remote = read_config(cfg.base_url or "", timeout_sec=min(cfg.timeout_sec, 1.5))
                return self._remember_led(cfg, remote, online=True)
            except HubError:
                if state is None:
                    state = {"online": False, "on": False, "brightness": None, "raw": 0, "at": now}
                else:
                    state = {**state, "online": False, "at": now}
                self._led_state[cfg.id] = state
        state = state or {"online": False, "on": False, "brightness": None, "raw": 0}
        payload: dict[str, Any] = {
            "id": cfg.id,
            "name": cfg.name,
            "type": "light",
            "online": bool(state.get("online")),
            "on": bool(state.get("on")),
        }
        if state.get("brightness") is not None:
            payload["brightness"] = state["brightness"]
        elif state.get("on"):
            payload["brightness"] = raw_to_percent(int(state.get("raw") or cfg.on_brightness))
        return payload

    def _led_restore_path(self):
        path = self.config.path
        if path is None:
            return None
        return path.with_name("ledstrip-state.json")

    def _note_led_restore(self, device_id: str, raw: int) -> None:
        raw = max(1, min(255, int(raw)))
        if self._led_restore.get(device_id) == raw:
            return
        self._led_restore[device_id] = raw
        self._save_led_restore()

    def _load_led_restore(self) -> None:
        path = self._led_restore_path()
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        for key, value in payload.items():
            try:
                raw = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= raw <= 255:
                self._led_restore[str(key)] = raw

    def _save_led_restore(self) -> None:
        path = self._led_restore_path()
        if path is None:
            return
        try:
            path.write_text(
                json.dumps(self._led_restore, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _command_mijia(self, cfg: DeviceConfig, body: dict[str, Any]) -> dict[str, Any]:
        if cfg.is_sensor:
            raise HubError("unsupported", f"{cfg.name} 是传感器，不能开关")
        if "run" in body:
            raise HubError("unsupported", f"{cfg.name} 不支持 run")
        if "media" in body:
            raise HubError("unsupported", f"{cfg.name} 不支持 media")
        if "brightness" in body and cfg.type == "switch":
            raise HubError("unsupported", f"{cfg.name} 是开关，不支持亮度")

        brightness = None
        if "brightness" in body:
            brightness = body["brightness"]
            if isinstance(brightness, float) and brightness.is_integer():
                brightness = int(brightness)
            if not isinstance(brightness, int) or isinstance(brightness, bool) or not (1 <= brightness <= 100):
                raise HubError("bad_request", "brightness 必须是 1–100 的整数")

        on = body.get("on")
        if on is not None and not isinstance(on, bool):
            raise HubError("bad_request", "on 必须是 true 或 false")

        raw_props = body.get("props")
        if raw_props is not None and not isinstance(raw_props, dict):
            raise HubError("bad_request", "props 必须是对象")
        extra_writes = dict(raw_props or {})
        if "on" not in body and "brightness" not in body and not extra_writes:
            raise HubError("bad_request", "命令不能为空")

        status, _message = self._mijia_status()
        if status == "login_required":
            raise HubError("login_required", self._login_message)

        assert self.session is not None
        bound = self.session.devices.get(cfg.id)
        if bound is None or bound.did is None or bound.on_prop is None:
            raise HubError("mijia_error", bound.missing if bound else f"未绑定 {cfg.name}")

        writes: list[dict[str, Any]] = []
        target_on = on
        extra_values = dict(bound.extra_values)
        for ident, value in extra_writes.items():
            spec = next((item for item in cfg.effective_props() if item.id == ident), None)
            if spec is None or not spec.write:
                raise HubError("unsupported", f"{cfg.name} 不能写 {ident}")
            ref = bound.extra_props.get(ident)
            if ref is None:
                raise HubError("unsupported", f"{cfg.name} 未绑定 {ident}")
            writes.append(
                {
                    "did": bound.did,
                    "siid": ref.siid,
                    "piid": ref.piid,
                    "value": coerce_write(ref, value),
                }
            )
            extra_values[ident] = value
            bound.extra_values[ident] = value
        if brightness is not None:
            if bound.brightness_prop is None:
                raise HubError("unsupported", f"{cfg.name} 不支持亮度")
            if target_on is None:
                target_on = True
            writes.append(
                {
                    "did": bound.did,
                    "siid": bound.brightness_prop.siid,
                    "piid": bound.brightness_prop.piid,
                    "value": coerce_write(bound.brightness_prop, brightness),
                }
            )
        if target_on is not None:
            writes.append(
                {
                    "did": bound.did,
                    "siid": bound.on_prop.siid,
                    "piid": bound.on_prop.piid,
                    "value": coerce_write(bound.on_prop, target_on),
                }
            )
        # turn on together with brightness: set on last so the lamp actually lights
        if brightness is not None and target_on:
            writes = [w for w in writes if not (w["siid"] == bound.on_prop.siid and w["piid"] == bound.on_prop.piid)] + [
                w for w in writes if w["siid"] == bound.on_prop.siid and w["piid"] == bound.on_prop.piid
            ]

        try:
            with self._mijia_lock:
                set_result = self.session.api.set_devices_prop(writes)
                set_rows = [set_result] if isinstance(set_result, dict) else list(set_result)
                read_params = [{"did": bound.did, "siid": bound.on_prop.siid, "piid": bound.on_prop.piid}]
                if bound.brightness_prop:
                    read_params.append(
                        {"did": bound.did, "siid": bound.brightness_prop.siid, "piid": bound.brightness_prop.piid}
                    )
                try:
                    read_rows = read_batch(self.session.api, read_params)
                except Exception:
                    read_rows = []
        except HubError:
            raise
        except Exception as exc:
            raise HubError("mijia_error", f"米家调用失败：{exc}") from exc

        for row in set_rows:
            code = int(row.get("code", -1))
            if code in {0, 1}:
                continue
            if is_offline(row):
                raise HubError("offline", f"{cfg.name}离线")
            raise HubError("mijia_error", error_message(row, f"{cfg.name} 设置失败"))

        mapped = result_map(read_rows)
        payload = self._device_from_map(bound, mapped)
        # 米家立刻回读常是旧值；写成功则以请求目标为准，避免安卓把过期亮度画上去。
        on_value = target_on if target_on is not None else bool(payload.get("on"))
        bri_value = brightness if brightness is not None else payload.get("brightness")
        payload = self._device_payload(
            cfg,
            online=True,
            on=on_value,
            brightness=bri_value,
            extra_values=extra_values,
        )
        bound.last_on = on_value
        if bri_value is not None:
            bound.last_brightness = bri_value
        return payload

    def _snapshot_body(
        self,
        mijia: str,
        message: str | None,
        temperature: dict[str, Any] | None,
        devices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        body = {
            "protocol": PROTOCOL,
            "seq": self.snapshot_seq(),
            "hub": {
                "name": self.config.name,
                "mijia": mijia,
                "message": message,
            },
            "temperature": temperature,
            "pc": self.pc_snapshot(),
            "media": self.media.snapshot(),
            "companion": self.companion.snapshot(),
            "devices": devices,
        }
        self._note_desk_snapshot(body)
        return body

    def _note_desk_snapshot(self, snap: dict[str, Any]) -> None:
        devices = snap.get("devices")
        temp = snap.get("temperature")
        with self._desk_lock:
            self._desk_temp = temp if isinstance(temp, dict) else None
            self._desk_devices = list(devices) if isinstance(devices, list) else []
            self._mijia_at = time.monotonic()

    def _maybe_refresh_mijia(self) -> None:
        cfg = self.config.mini or MiniLinkConfig()
        interval = max(5.0, cfg.mijia_ms / 1000.0)
        if self._mijia_at > 0 and time.monotonic() - self._mijia_at < interval:
            return
        if self._refreshing_mijia:
            return
        self._refreshing_mijia = True
        try:
            self._pull_mijia_desk()
        except Exception:
            pass
        finally:
            self._refreshing_mijia = False

    def _pull_mijia_desk(self) -> None:
        _status, _message, temperature, devices = self._mijia_desk_slice()
        self._note_desk_snapshot({"temperature": temperature, "devices": devices})

    def _windows_payload(self) -> dict[str, Any]:
        self._maybe_refresh_mijia()
        running = self._running_names()
        with self._desk_lock:
            temperature = self._desk_temp
            cached = list(self._desk_devices)
        by_id = {
            item["id"]: item
            for item in cached
            if isinstance(item, dict) and item.get("id")
        }
        devices: list[dict[str, Any]] = []
        for cfg in self.config.devices:
            if cfg.is_action:
                devices.append(self._action_payload(cfg, running))
                continue
            item = by_id.get(cfg.id)
            if item is None:
                continue
            bound = self.session.devices.get(cfg.id) if self.session is not None else None
            if bound is not None:
                item = dict(item)
                if bound.last_on is not None:
                    item["on"] = bound.last_on
                if bound.last_brightness is not None:
                    item["brightness"] = bound.last_brightness
            devices.append(item)
        seen = {item.get("id") for item in devices}
        for item in cached:
            if isinstance(item, dict) and item.get("id") and item["id"] not in seen:
                devices.append(item)
        return {
            "pc": self.pc_snapshot(),
            "media": self.media.snapshot(),
            "temperature": temperature,
            "devices": devices,
        }

    def _mijia_status(self) -> tuple[str, str | None]:
        if self.session is None or not self.session.available():
            return "login_required", self._login_message
        return "ok", None

    def _temperature_from_map(self, mapped: dict) -> dict[str, Any] | None:
        bound = self.session.temperature if self.session else None
        if bound is None:
            return None
        cfg = bound.cfg
        if bound.did is None or bound.celsius_prop is None:
            return None
        celsius_row = lookup(mapped, bound.did, bound.celsius_prop)
        if not is_ok(celsius_row):
            return None
        payload: dict[str, Any] = {
            "id": cfg.id,
            "name": cfg.name,
            "celsius": round(float(celsius_row["value"]), 1),
            "updated_at": updated_at(celsius_row),
            "online": not is_offline(celsius_row),
        }
        if bound.humidity_prop:
            humidity_row = lookup(mapped, bound.did, bound.humidity_prop)
            if is_ok(humidity_row):
                humidity = humidity_row["value"]
                payload["humidity"] = int(humidity) if float(humidity).is_integer() else float(humidity)
        self._last_temp = payload
        return payload

    def _device_from_map(self, bound: BoundDevice, mapped: dict) -> dict[str, Any]:
        cfg = bound.cfg
        if cfg.is_sensor:
            return self._sensor_from_bound(bound, mapped)
        if bound.did is None or bound.on_prop is None:
            return self._device_from_cache(bound, online=False)
        on_row = lookup(mapped, bound.did, bound.on_prop)
        online = is_ok(on_row) and not is_offline(on_row)
        on_value = as_bool(on_row["value"]) if is_ok(on_row) else bound.last_on
        if on_value is None:
            on_value = False
        brightness = bound.last_brightness
        if bound.brightness_prop:
            bright_row = lookup(mapped, bound.did, bound.brightness_prop)
            if is_ok(bright_row):
                parsed = as_brightness(bright_row["value"])
                if parsed is not None:
                    brightness = parsed
            elif is_offline(bright_row):
                online = False
        if is_ok(on_row):
            bound.last_on = on_value
        if brightness is not None:
            bound.last_brightness = brightness
        extra_values = dict(bound.extra_values)
        for ident, prop in bound.extra_props.items():
            row = lookup(mapped, bound.did, prop)
            if is_ok(row) and not is_offline(row):
                extra_values[ident] = row["value"]
                bound.extra_values[ident] = row["value"]
        return self._device_payload(
            cfg,
            online=online,
            on=on_value,
            brightness=brightness,
            extra_values=extra_values,
        )

    def _device_from_cache(self, bound: BoundDevice, online: bool) -> dict[str, Any]:
        if bound.cfg.is_sensor:
            return self._sensor_from_bound(bound, mapped=None, online=online)
        on = bound.last_on if bound.last_on is not None else False
        return self._device_payload(
            bound.cfg,
            online=online,
            on=on,
            brightness=bound.last_brightness,
            extra_values=bound.extra_values,
        )

    def _mijia_placeholder(self, cfg: DeviceConfig) -> dict[str, Any]:
        return self._device_payload(cfg, online=False, on=False, brightness=None)

    def _sensor_placeholder(self, cfg: DeviceConfig) -> dict[str, Any]:
        return self._sensor_payload(cfg, online=False, values={})

    def _sensor_from_temperature(
        self,
        cfg: TemperatureConfig,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if payload:
            if payload.get("celsius") is not None:
                values["celsius"] = payload["celsius"]
            if payload.get("humidity") is not None:
                values["humidity"] = payload["humidity"]
        fake = DeviceConfig(
            id=cfg.id,
            name=cfg.name,
            type="sensor",
            mijia_name=cfg.mijia_name,
            props=[
                PropConfig(id="celsius", prop=cfg.celsius_prop, name="温度", write=False, unit="°C"),
                PropConfig(id="humidity", prop=cfg.humidity_prop or "humidity", name="湿度", write=False, unit="%"),
            ],
        )
        item = self._sensor_payload(fake, online=bool(payload and payload.get("online")), values=values)
        return item

    def _sensor_from_bound(
        self,
        bound: BoundDevice,
        mapped: dict | None,
        online: bool | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = dict(bound.extra_values)
        live = online
        if mapped is not None and bound.did:
            live = False
            for ident, prop in bound.extra_props.items():
                row = lookup(mapped, bound.did, prop)
                if is_ok(row) and not is_offline(row):
                    live = True
                    values[ident] = row["value"]
                    bound.extra_values[ident] = row["value"]
                elif is_ok(row):
                    values[ident] = row["value"]
        if live is None:
            live = bool(bound.did) and bool(values)
        return self._sensor_payload(bound.cfg, online=bool(live), values=values)

    def _prop_entries(
        self,
        cfg: DeviceConfig,
        values: dict[str, Any],
        *,
        skip: set[str],
    ) -> list[dict[str, Any]]:
        props = []
        for item in cfg.effective_props():
            if item.id in skip:
                continue
            raw = values.get(item.id)
            entry: dict[str, Any] = {
                "id": item.id,
                "name": item.name or item.id,
                "prop": item.prop,
                "type": "number" if item.role in {"auto", "number"} else item.role,
                "writable": item.write,
            }
            if item.unit:
                entry["unit"] = item.unit
            if item.min is not None:
                entry["min"] = item.min
            if item.max is not None:
                entry["max"] = item.max
            if raw is not None:
                if isinstance(raw, bool):
                    entry["flag"] = raw
                    entry["type"] = "bool"
                elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    number = float(raw)
                    entry["number"] = round(number, 1) if abs(number) >= 1 else number
                    entry["value"] = entry["number"]
                else:
                    entry["text"] = str(raw)
                    entry["value"] = str(raw)
                    entry["type"] = "enum" if item.role == "enum" else "text"
            props.append(entry)
        return props

    def _sensor_payload(self, cfg: DeviceConfig, *, online: bool, values: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": cfg.id,
            "name": cfg.name,
            "type": "sensor",
            "online": online,
            "props": self._prop_entries(cfg, values, skip=set()),
        }
        celsius = values.get("celsius")
        if celsius is None:
            celsius = values.get("temperature")
        if isinstance(celsius, (int, float)) and not isinstance(celsius, bool):
            payload["celsius"] = round(float(celsius), 1)
        humidity = values.get("humidity")
        if isinstance(humidity, (int, float)) and not isinstance(humidity, bool):
            payload["humidity"] = int(humidity) if float(humidity).is_integer() else float(humidity)
        return payload

    def _device_payload(
        self,
        cfg: DeviceConfig,
        online: bool,
        on: bool,
        brightness: int | None,
        extra_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": cfg.id,
            "name": cfg.name,
            "type": cfg.type,
            "online": online,
            "on": on,
        }
        if cfg.type == "light" and cfg.brightness_prop and brightness is not None:
            payload["brightness"] = brightness
        extras = self._prop_entries(cfg, extra_values or {}, skip={"on", "brightness"})
        if extras:
            payload["props"] = extras
        return payload

    def _action_payload(self, cfg: DeviceConfig, running: set[str] | None = None) -> dict[str, Any]:
        if self.uses_probe():
            pc = self.pc_snapshot() or {}
            online = bool(pc.get("online"))
            names = running if running is not None else self._running_names()
            on = launch_running(cfg, names) if online else False
        else:
            online = launch_exists(cfg)
            on = launch_running(cfg, running)
        payload: dict[str, Any] = {
            "id": cfg.id,
            "name": cfg.name,
            "type": "action",
            "online": online,
            "on": on,
        }
        if cfg.icon:
            payload["icon"] = cfg.icon
        last_run = self._last_run_at.get(cfg.id)
        if last_run:
            payload["last_run_at"] = last_run
        return payload

    def _lock_for(self, device_id: str) -> threading.Lock:
        with self._device_locks_guard:
            return self._device_locks.setdefault(device_id, threading.Lock())

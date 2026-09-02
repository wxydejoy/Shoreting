from __future__ import annotations

import threading
import time
from typing import Any

from dock_hub import PROTOCOL
from dock_hub.chat_log import ChatLog, chat_log_path
from dock_hub.companion import Companion
from dock_hub.config import DeviceConfig, HubConfig, MediaConfig, MiniLinkConfig, PcConfig, default_config_path
from dock_hub.errors import HubError
from dock_hub.launch import launch, launch_exists, launch_running, running_image_names
from dock_hub.pc import PcSampler, utc_now
from dock_hub.media import MEDIA_ACTIONS, MediaSession
from dock_hub.reporter import DeskReporter
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


class DockHub:
    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self.session: MijiaSession | None = None
        self._mijia_lock = threading.Lock()
        self._device_locks: dict[str, threading.Lock] = {}
        self._device_locks_guard = threading.Lock()
        self._last_temp: dict[str, Any] | None = None
        self._login_message = "请在配置向导中扫码登录米家"
        self._last_run_at: dict[str, str] = {}
        pc_cfg = config.pc or PcConfig(enabled=False)
        self.pc = PcSampler(
            enabled=pc_cfg.enabled,
            sample_ms=pc_cfg.sample_ms,
            cpu_temp=pc_cfg.cpu_temp,
            gpu=pc_cfg.gpu,
        )
        self.pc.start()
        media_cfg = config.media or MediaConfig(enabled=True)
        self.media = MediaSession(enabled=media_cfg.enabled)
        self.companion = Companion(config.companion)
        self.chat_log = ChatLog(chat_log_path(config.path))
        self.companion.set_chat_log(self.chat_log)
        self._desk_lock = threading.Lock()
        self._desk_temp: dict[str, Any] | None = None
        self._desk_devices: list[dict[str, Any]] = []
        self._mijia_at = 0.0
        self._refreshing_mijia = False
        mini_cfg = config.mini or MiniLinkConfig(enabled=False)
        self.reporter = DeskReporter(mini_cfg, self._windows_payload)
        self._wire_companion()
        self.reporter.start()

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
            enabled=pc_cfg.enabled,
            sample_ms=pc_cfg.sample_ms,
            cpu_temp=pc_cfg.cpu_temp,
            gpu=pc_cfg.gpu,
        )
        self.pc.start()
        media_cfg = config.media or MediaConfig(enabled=True)
        self.media = MediaSession(enabled=media_cfg.enabled)
        self.companion.configure(config.companion)
        self.reporter.stop()
        mini_cfg = config.mini or MiniLinkConfig(enabled=False)
        self.reporter = DeskReporter(mini_cfg, self._windows_payload)
        self._wire_companion()
        self.reporter.start()
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

    def snapshot(self) -> dict[str, Any]:
        status, message, temperature, devices = self._mijia_desk_slice()
        return self._snapshot_body(status, message, temperature, devices)

    def _mijia_desk_slice(
        self,
    ) -> tuple[str, str | None, dict[str, Any] | None, list[dict[str, Any]]]:
        running = running_image_names()
        actions = [self._action_payload(cfg, running) for cfg in self.config.devices if cfg.is_action]
        status, message = self._mijia_status()
        if status == "login_required":
            return status, message, None, actions

        assert self.session is not None
        try:
            with self._mijia_lock:
                params = query_params(self.session)
                rows = read_batch(self.session.api, params) if params else []
            mapped = result_map(rows)
        except Exception as exc:
            self.session.last_error = str(exc)
            devices = actions + [
                self._device_from_cache(bound, online=False)
                for bound in self.session.devices.values()
                if not bound.voice_only
            ]
            return "error", f"米家调用失败：{exc}", None, devices

        temperature = self._temperature_from_map(mapped)
        mijia_devices = [self._device_from_map(bound, mapped) for bound in self.session.devices.values()]
        running = running_image_names()
        by_id = {item["id"]: item for item in mijia_devices}
        devices = []
        for cfg in self.config.devices:
            if cfg.is_action:
                devices.append(self._action_payload(cfg, running))
            elif cfg.id in by_id:
                devices.append(by_id[cfg.id])
        return "ok", None, temperature, devices

    def _wire_companion(self) -> None:
        skills = []
        for cfg in self.config.devices:
            if cfg.is_mijia:
                item: dict[str, Any] = {"id": cfg.id, "name": cfg.name, "kind": "toggle"}
                if cfg.type == "light" and cfg.brightness_prop:
                    item["brightness"] = True
                skills.append(item)
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
        self.companion.set_skills(
            skills,
            self._run_companion_skill,
            aliases_path,
            world={
                "temperature": lambda: self._last_temp,
                "pc": self.pc.snapshot,
                "device": self._companion_device_state,
                "media": self.media.snapshot,
                "media_command": self.media.command,
                "volume": self.media.volume,
                "game": self._game_running,
                "desk": self.reporter.snapshot,
            },
        )

    def note_presence(self) -> None:
        self.companion.note_presence()

    def _game_running(self) -> bool:
        actions = [cfg for cfg in self.config.devices if cfg.is_action]
        if not actions:
            return False
        running = running_image_names()
        if not running:
            return True
        return any(launch_running(cfg, running) for cfg in actions)

    def _companion_device_state(self, device_id: str) -> dict[str, Any] | None:
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
        self, device_id: str, on: bool, brightness: int | None = None
    ) -> None:
        cfg = self._lookup_device(device_id)
        if cfg.is_action:
            if not on:
                return
            self.command(device_id, {"run": True})
            return
        body: dict[str, Any] = {}
        if brightness is not None:
            body["brightness"] = brightness
            if on:
                body["on"] = True
        else:
            body["on"] = on
        self.command(device_id, body)

    def companion_chat(self, body: dict[str, Any] | None) -> dict[str, Any]:
        return self.companion.chat(body or {}, "")

    def companion_chats(self, *, limit: int = 200, query: str = "") -> dict[str, Any]:
        return {"items": self.chat_log.list(limit=limit, query=query)}

    def companion_audio(self, audio_id: str) -> bytes:
        return self.companion.audio(audio_id)

    def companion_stop(self) -> dict[str, Any]:
        return self.companion.stop()

    def companion_announce(self, body: dict[str, Any] | None) -> dict[str, Any]:
        return self.companion.announce(body or {})

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
        if device_id == "media":
            return self._command_media(body)
        cfg = self._lookup_device(device_id)
        lock = self._lock_for(device_id)
        with lock:
            if cfg.is_action:
                return self._command_action(cfg, body)
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
            raise HubError("offline", f"{cfg.name} 的程序不存在")
        try:
            launch(cfg)
        except HubError:
            raise
        except Exception as exc:
            raise HubError("action_error", f"无法启动 {cfg.name}：{exc}") from exc
        self._last_run_at[cfg.id] = utc_now()
        return self._action_payload(cfg)

    def _command_mijia(self, cfg: DeviceConfig, body: dict[str, Any]) -> dict[str, Any]:
        if "run" in body:
            raise HubError("unsupported", f"{cfg.name} 不支持 run")
        if "media" in body:
            raise HubError("unsupported", f"{cfg.name} 不支持 media")
        if "brightness" in body and cfg.type == "switch":
            raise HubError("unsupported", f"{cfg.name} 是开关，不支持亮度")
        if "on" not in body and "brightness" not in body:
            raise HubError("bad_request", "命令不能为空")

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

        status, _message = self._mijia_status()
        if status == "login_required":
            raise HubError("login_required", self._login_message)

        assert self.session is not None
        bound = self.session.devices.get(cfg.id)
        if bound is None or bound.did is None or bound.on_prop is None:
            raise HubError("mijia_error", bound.missing if bound else f"未绑定 {cfg.name}")

        writes: list[dict[str, Any]] = []
        target_on = on
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
        payload = self._device_payload(cfg, online=True, on=on_value, brightness=bri_value)
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
            "hub": {
                "name": self.config.name,
                "mijia": mijia,
                "message": message,
            },
            "temperature": temperature,
            "pc": self.pc.snapshot(),
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
        running = running_image_names()
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
            "pc": self.pc.snapshot(),
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
        return self._device_payload(cfg, online=online, on=on_value, brightness=brightness)

    def _device_from_cache(self, bound: BoundDevice, online: bool) -> dict[str, Any]:
        on = bound.last_on if bound.last_on is not None else False
        return self._device_payload(bound.cfg, online=online, on=on, brightness=bound.last_brightness)

    def _device_payload(
        self,
        cfg: DeviceConfig,
        online: bool,
        on: bool,
        brightness: int | None,
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
        return payload

    def _action_payload(self, cfg: DeviceConfig, running: set[str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": cfg.id,
            "name": cfg.name,
            "type": "action",
            "online": launch_exists(cfg),
            "on": launch_running(cfg, running),
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

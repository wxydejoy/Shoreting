from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

from dock_hub import PROTOCOL, logs
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
from dock_hub.dusk import DuskRamp
from dock_hub.alarm import AlarmRamp
from dock_hub.system_volume import read_output_volume, write_output_volume
from dock_hub.theme import birthday_text, occasions_for
from dock_hub.wallpaper import wallpaper_line
from dock_hub.photos import list_photos, photo_bytes as read_photo
from dock_hub.errors import HubError
from dock_hub.memory import MEMORY_LIMIT, MemoryStore
from dock_hub.metrics import METRICS
from dock_hub.notify import NotifyBoard, parse_relay, wechat_say_text
from dock_hub.weekly import DailyLedger, weekly_line
from dock_hub.lanes import Lanes
from dock_hub.launch import launch, launch_exists, launch_running, running_image_names
from dock_hub.ledstrip import apply_config, percent_to_raw, raw_to_percent, read_config
from dock_hub.pc import PcSampler, utc_now
from dock_hub.ratelimit import Gate
from dock_hub.persona import speak_temp
from dock_hub.proactive import Proactive, Said, SedentaryWatch, Sustained, finished_text
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
        self._remote_idle: float | None = None
        self._desk_pc_online: bool | None = None
        self._ticket_lock = threading.Lock()
        self._login_tickets: dict[str, tuple[float, str]] = {}
        # 没开限流时是 None，调用方按「不限」处理，不用到处判断开关
        self.gate = Gate.from_config(config.limits)
        # 慢请求（等模型、等 TTS）单独几个名额，满了回 503 而不是无限开线程
        self.lanes = Lanes.from_config(config.limits)
        media_cfg = config.media or MediaConfig(enabled=True)
        self.media = MediaSession(enabled=media_cfg.enabled)
        self.companion = Companion(config.companion)
        self.chat_log = ChatLog(chat_log_path(config.path))
        self.companion.set_chat_log(self.chat_log)
        self.companion.set_reads_dir((config.path or default_config_path()).parent / "reads")
        # F28 长期记忆：hub.yaml 同目录一份 companion-memory.yaml，设置页可见可删
        self.memory = MemoryStore(
            (config.path or default_config_path()).with_name("companion-memory.yaml")
        )
        self.companion.set_memory(self.memory)
        # F27 桌面周报：对话/朗读/在桌/开关机的边沿按天记账，周日晚她自己总结
        self.ledger = DailyLedger(
            (config.path or default_config_path()).with_name("companion-stats.jsonl")
        )
        self.companion.on_read_done = self.ledger.note_read
        # F33/F4/F34：往手机递话的一条通道（对讲反向、语音留言、微信共用）
        self.notify = NotifyBoard()
        self._attach_books()
        self._desk_lock = threading.Lock()
        self._desk_temp: dict[str, Any] | None = None
        self._desk_devices: list[dict[str, Any]] = []
        self._mijia_at = 0.0
        self._refreshing_mijia = False
        mini_cfg = config.mini or MiniLinkConfig(enabled=False)
        self.reporter = DeskReporter(mini_cfg, self._windows_payload)
        self._wire_companion()
        self.proactive = Proactive(
            config.proactive,
            speak=self.companion.speak_up,
            compose_morning=self._morning_text,
            compose_weekly=self._weekly_text,
        )
        self._alert_state = Sustained()
        self._sedentary = SedentaryWatch()
        self._weather_check_at = 0.0
        self._air_check_at = 0.0
        self._air_bad = False  # PM2.5 现在在坏区间里？只在进区间那一下开口
        self._finished_lock = threading.Lock()
        self._finished_queue: list[dict[str, Any]] = []
        self._presence_lock = threading.Lock()
        self._presence: str | None = None  # None = 还没报过，第一报不问候
        self._presence_at: str | None = None
        self._at_desk_lock = threading.Lock()
        self._at_desk: str | None = None
        self._at_desk_at: str | None = None
        self._proactive_stop = threading.Event()
        self._start_proactive()
        self._focus_active: bool | None = None  # None = 还没看过一眼，不触发
        self._focus_stop = threading.Event()
        # F22：进游戏静音、退出恢复。读写都走实例属性，测试好换假件
        self._volume_before_game: int | None = None
        self._read_volume = read_output_volume
        self._write_volume = write_output_volume
        self._start_focus()
        self._dusk: DuskRamp | None = None
        self._start_dusk()
        self._alarm: AlarmRamp | None = None
        self._start_alarm()
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

    def _wallpaper_payload(self) -> dict[str, str] | None:
        """一句话壁纸（F39）：今天那句。关着就不带，别让安卓画空气。"""
        if not self.config.wallpaper.enabled:
            return None
        today = datetime.now().astimezone().date()
        return {"text": wallpaper_line(today), "date": today.isoformat()}

    def _theme_payload(self) -> dict[str, list[str]] | None:
        """今天是什么日子（F36）。没配生日和考试周、又不是节日，就不用带。"""
        occasions = occasions_for(datetime.now().astimezone().date(), self.config.theme)
        return {"occasions": occasions} if occasions else None

    def _photos_dir(self) -> Path | None:
        """回忆相框（F37）的图库：config 里解析过的目录，None = 关着。"""
        return self.config.photos

    def photo_ids(self) -> list[str]:
        return list_photos(self._photos_dir())

    def _photos_payload(self) -> dict[str, list[str]] | None:
        """相框名单进 snapshot。没 photos/ 目录或里面没图，就不带这个字段。"""
        ids = self.photo_ids()
        return {"ids": ids} if ids else None

    def photo_bytes(self, photo_id: str) -> tuple[bytes, str, str]:
        """照片字节 + Content-Type + ETag。id 必须在名单里，翻不进上级目录。"""
        folder = self._photos_dir()
        try:
            data, ctype = read_photo(folder, photo_id)
        except ValueError as exc:
            raise HubError("not_found", str(exc)) from exc
        stamp = (folder / Path(photo_id).name).stat()
        etag = f'W/"{int(stamp.st_mtime)}-{stamp.st_size}"'
        return data, ctype, etag

    def _evaluate_alerts(self) -> None:
        """温度太高、设备失联：坏够久才开口，一天一件事最多一句。"""
        cfg = self.proactive.config
        if not cfg.enabled:
            return
        now = datetime.now().astimezone()
        if self.config.theme.birthday and occasions_for(
            now.date(), self.config.theme
        )[:1] == ["birthday"]:
            said = self.proactive.alert("birthday", birthday_text(), now)
            print(f"[proactive] 生日问候：{said.text or said.reason}")
        alerts = cfg.alerts
        if alerts.temp_celsius > 0:
            cpu = (self.pc_snapshot() or {}).get("cpu")
            temp = cpu.get("temp_celsius") if isinstance(cpu, dict) else None
            if temp is not None and self._alert_state.check(
                "temp", float(temp) >= alerts.temp_celsius, now, 180.0
            ):
                said = self.proactive.alert(
                    "temp", f"Windows CPU {float(temp):.0f} 度，有点热。", now
                )
                if said.spoke:
                    self._alert_state.clear("temp")  # 说过了，降温前不再提
                print(f"[proactive] 温度提醒：{said.text or said.reason}")
        if alerts.memory_percent > 0:
            memory = (self.pc_snapshot() or {}).get("memory")
            percent = memory.get("percent") if isinstance(memory, dict) else None
            if percent is not None and self._alert_state.check(
                "mem", float(percent) >= alerts.memory_percent, now, 180.0
            ):
                said = self.proactive.alert(
                    "mem", f"内存用到 {float(percent):.0f}% 了，后台该收一收。", now
                )
                if said.spoke:
                    self._alert_state.clear("mem")  # 说过了，降下来之前不再提
                print(f"[proactive] 内存提醒：{said.text or said.reason}")
        if alerts.sedentary_minutes > 0 and self.proactive.config.enabled:
            if self._sedentary.tick(self._idle_seconds(), now, alerts.sedentary_minutes):
                said = self.proactive.alert("sedentary", "坐了一阵了，起来走走。", now)
                if said.spoke:
                    self._sedentary.reset()  # 提过就重新计时，别连环催
                print(f"[proactive] 久坐提醒：{said.text or said.reason}")
        if alerts.weather:
            self._evaluate_weather_alert(now)
        if alerts.air_pm25 > 0:
            self._evaluate_air_alert(now)
        self._evaluate_finished_alerts(now)
        if alerts.offline_minutes > 0:
            for item in self._offline_mijia_devices():
                key = f"offline:{item['id']}"
                if self._alert_state.check(key, True, now, alerts.offline_minutes * 60.0):
                    said = self.proactive.alert(key, f"{item['name']}有一阵没应答了。", now)
                    if said.spoke:
                        self._alert_state.clear(key)
                    print(f"[proactive] 离线提醒：{said.text or said.reason}")

    def _finished_watch(self) -> set[str]:
        """关注列表：配置里点的名 + 启动项的进程（她本来就在盯着这些）。"""
        watch = {
            name.strip().lower()
            for name in self.proactive.config.alerts.finished
            if name.strip()
        }
        for device in self.config.devices:
            if device.is_action:
                for name in device.process or []:
                    if str(name).strip():
                        watch.add(str(name).strip().lower())
        return watch

    def _evaluate_finished_alerts(self, now: datetime) -> None:
        """任务完成播报（F20）：探针报谁跑完了，名单里的说一句。

        队列在这里统一倒（proactive 线程），别的线程只入队；
        免打扰、每日配额、一天一个进程最多一句都由 proactive.alert 兑底。
        """
        watch = self._finished_watch()
        if not watch:
            with self._finished_lock:
                self._finished_queue.clear()
            return
        with self._finished_lock:
            pending, self._finished_queue = self._finished_queue, []
        for event in pending:
            if event["process"].lower() not in watch:
                continue
            said = self.proactive.alert(
                f"finished:{event['process']}",
                finished_text(event["process"], event["ran_sec"]),
                now,
            )
            print(f"[proactive] 任务完成：{said.text or said.reason}")

    WEATHER_CHECK_SEC = 3600.0
    DESK_AWAY_MIN_SEC = 300  # 离座不足这个数不问候（安卓侧会先去抖，这里兜底）
    AIR_CHECK_SEC = 3600.0
    AIR_HYSTERESIS_UGM3 = 20.0  # 回落这么多才算翻篇，卡在阈值边上不反复横跳

    def _evaluate_weather_alert(self, now: datetime) -> None:
        """天气突变（F15）：每小时比对一次逐小时预报，只推「变化」。

        城市用手机最近一次查过的那个，没查过就不提醒。07:00–21:00 之外不看，
        免打扰时段与每天最多一次由 proactive.alert 兑底。
        """
        if now.hour < 7 or now.hour >= 21:
            return
        if time.monotonic() - self._weather_check_at < self.WEATHER_CHECK_SEC:
            return
        self._weather_check_at = time.monotonic()
        from dock_hub.proactive import weather_change_text
        from dock_hub.weather import fetch_hourly, fetch_weather, last_city

        city = last_city()
        if not city:
            return
        try:
            hourly = fetch_hourly(city)
            current = fetch_weather(city)
        except Exception as exc:  # noqa: BLE001
            print(f"[proactive] 天气预报拿不到：{exc}")
            return
        text = weather_change_text(
            now,
            float(current.get("celsius") or 0.0),
            str(current.get("condition") or ""),
            hourly,
        )
        if not text:
            return
        said = self.proactive.alert("weather", text, now)
        print(f"[proactive] 天气提醒：{said.text or said.reason}")

    def _evaluate_air_alert(self, now: datetime) -> None:
        """空气超标（F14）：每小时看一眼，只在由好变坏那一下说。

        一直坏就一直闭嘴（跟天气突变同一条规矩：推变化不推状态），
        回落 AIR_HYSTERESIS_UGM3 才重新武装。城市用手机最近查过的那座。
        07:00–21:00 之外不看；配额、免打扰由 proactive.alert 兜底。
        """
        if now.hour < 7 or now.hour >= 21:
            return
        if time.monotonic() - self._air_check_at < self.AIR_CHECK_SEC:
            return
        self._air_check_at = time.monotonic()
        from dock_hub.proactive import air_alert_text
        from dock_hub.weather import fetch_air, last_city

        city = last_city()
        if not city:
            return
        try:
            air = fetch_air(city)
        except Exception as exc:  # noqa: BLE001
            print(f"[proactive] 空气质量拿不到：{exc}")
            return
        pm25 = float(air.get("pm25") or 0.0)
        threshold = float(self.proactive.config.alerts.air_pm25)
        if self._air_bad:
            if pm25 < threshold - self.AIR_HYSTERESIS_UGM3:
                self._air_bad = False  # 翻篇了，下次变坏再说
            return
        if pm25 < threshold:
            return
        self._air_bad = True
        said = self.proactive.alert("air", air_alert_text(pm25), now)
        print(f"[proactive] 空气提醒：{said.text or said.reason}")

    def _idle_seconds(self) -> float | None:
        """键鼠空闲秒数。只有探针在报且报得新鲜才有；拿不到就 None。"""
        with self._probe_lock:
            age = time.monotonic() - self._remote_at if self._remote_at else None
            stale = max(0.2, self.config.probe.stale_ms / 1000.0)
            if age is None or age > stale:
                return None
            return self._remote_idle

    def _offline_mijia_devices(self) -> list[dict[str, str]]:
        """配置里走米家、此刻离线的设备。启动项、灯带、探针电脑不算——
        电脑关机是常态，说「电脑离线了」只会讨人嫌。"""
        with self._desk_lock:
            cached = list(self._desk_devices)
        by_id = {
            item["id"]: item
            for item in cached
            if isinstance(item, dict) and item.get("id")
        }
        out: list[dict[str, str]] = []
        for cfg in self.config.devices:
            if cfg.is_action or not cfg.mijia_name:
                continue
            item = by_id.get(cfg.id)
            if item is not None and item.get("online") is False:
                out.append({"id": cfg.id, "name": cfg.name})
        return out

    def _morning_text(self, now: datetime) -> str:
        """早间播报正文：问好 + 室温。拿不到的那一样就跳过，不硬凑。"""
        parts: list[str] = []
        if now.hour < 11:
            parts.append("早上好。")
        elif now.hour < 14:
            parts.append("中午好。")
        else:
            parts.append("下午好。")
        with self._desk_lock:
            temp = self._desk_temp
        spoken = speak_temp(temp, "")
        if "还没有" not in spoken:
            parts.append(spoken)
        parts.append("今天也慢慢来。")
        return "".join(parts)

    def _start_proactive(self) -> None:
        """每半分钟问一次「有没有到点的话要说」。关着就不起线程。"""
        if not self.proactive.config.enabled:
            self._proactive_stop = threading.Event()
            return
        self._proactive_stop = threading.Event()
        stop = self._proactive_stop

        def loop() -> None:
            while not stop.wait(30.0):
                try:
                    self.ledger.tick(datetime.now().astimezone())
                except Exception as exc:  # noqa: BLE001 - 台账坏了不该连累播报
                    logs.swallowed("周报台账tick", exc)
                try:
                    said = self.proactive.tick(datetime.now().astimezone())
                    if said.spoke:
                        print(f"[proactive] {said.text}")
                except Exception as exc:  # 播报挂了不能拖垮 Hub
                    print(f"[proactive] 出错：{exc}")
                try:
                    self._evaluate_alerts()
                except Exception as exc:
                    print(f"[proactive] 提醒出错：{exc}")

        threading.Thread(target=loop, name="proactive", daemon=True).start()

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
            self._remote_idle = None
        self._desk_pc_online = None
        media_cfg = config.media or MediaConfig(enabled=True)
        self.media = MediaSession(enabled=media_cfg.enabled)
        # 限流与并发名额也按新配置重建，否则改了 yaml 得重启才生效
        self.gate = Gate.from_config(config.limits)
        self.lanes = Lanes.from_config(config.limits)
        self.companion.configure(config.companion)
        self._proactive_stop.set()
        self.proactive.configure(config.proactive)
        self._start_proactive()
        self.reporter.stop()
        mini_cfg = config.mini or MiniLinkConfig(enabled=False)
        self.reporter = DeskReporter(mini_cfg, self._windows_payload)
        self._wire_companion()
        self._start_focus()
        self._start_dusk()
        self._start_alarm()
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
        idle_raw = body.get("idle_sec")
        try:
            idle = float(idle_raw) if idle_raw is not None else None
        except (TypeError, ValueError):
            idle = None
        with self._probe_lock:
            self._remote_pc = sample
            self._remote_running = names
            self._remote_at = time.monotonic()
            self._remote_idle = idle
        self._set_desk_pc_online(bool(sample.get("online")))
        self.note_change()
        finished_raw = body.get("finished") or []
        if isinstance(finished_raw, list):
            for item in finished_raw:
                if not isinstance(item, dict):
                    continue
                # 名字原样留着：说的时候是「Blender 跑完了」，不是小写
                name = str(item.get("process") or "").strip()
                if not name:
                    continue
                raw_ran = item.get("ran_sec")
                try:
                    ran_sec = float(raw_ran) if raw_ran is not None else None
                except (TypeError, ValueError):
                    ran_sec = None
                with self._finished_lock:
                    self._finished_queue.append({"process": name, "ran_sec": ran_sec})
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
        if on:
            # F27：False→True 这条边沿就是「开了一次机」
            try:
                self.ledger.note_boot()
            except Exception as exc:  # noqa: BLE001 - 台账坏了不该连累灯控
                logs.swallowed("周报开机记账", exc)
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
                "scene": self.run_scene,
                "leave": self.leave_home,
                "notify": self.relay_to_phone,
                "power": self.power_action,
                "noise": self.companion_noise,
                "scenes": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "say": item.say,
                        "phrases": item.triggers(),
                    }
                    for item in self.config.scenes
                ],
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
        # F28：她早就记住的事随对话注入，自然用上
        facts = self.memory.facts()
        out = self.companion.chat(body or {}, facts)
        self.ledger.note_chat()
        self.note_change()
        return out

    def companion_week(self) -> dict[str, Any]:
        """F27 周报：/week 页的数据。"""
        summary = self.ledger.summary()
        return {"summary": summary, "line": weekly_line(summary)}

    def _weekly_text(self) -> str:
        return weekly_line(self.ledger.summary())

    def relay_to_phone(self, text: str, source: str = "desk") -> dict[str, Any]:
        """往手机递一句话（F33 反向 / F4 留言）。递完叫醒长轮询。"""
        item = self.notify.post(text, source=source)
        self.note_change()
        return item

    def companion_noise(self, act: dict[str, Any]) -> dict[str, Any]:
        """F31 白噪音：转给 Mini 的 TTS 服务（`:18100 /v1/noise`）。

        放不上就抛错，守岸人会照着 world 回调的规矩说「这次没动成」，
        不假装放上了。
        """
        name = "off" if act.get("action") == "off" else str(act.get("name") or "rain")
        state = self.companion.tts.noise(name, act.get("minutes"))
        if not state or not state.get("ok"):
            raise HubError("tts_unavailable", "白噪音没放成")
        return {
            "ok": True,
            "name": state.get("name"),
            "minutes": state.get("minutes") or 30,
            "seconds_left": state.get("seconds_left"),
        }

    def notify_phone(self, body: dict[str, Any]) -> dict[str, Any]:
        """`POST /v1/notify/phone`：脚本、电脑、任何拿得到 token 的东西都能递话。"""
        text = str((body or {}).get("text") or "").strip()
        if not text:
            raise HubError("bad_request", "text 不能为空")
        source = str((body or {}).get("from") or "script").strip()[:20] or "script"
        return self.relay_to_phone(text, source=source)

    def ingest_wechat(self, body: dict[str, Any]) -> dict[str, Any]:
        """`POST /v1/wechat`（F34）：微信机器人转发一句过来，她念出来。

        单向播报，不做双向对话（不做清单第 3 条）。念的这句走 announce 通道
        （不进对话历史、不追问），同时留一份在 notify 队列里给主屏当字幕备查。
        """
        data = body or {}
        text = str(data.get("text") or "").strip()
        if not text:
            raise HubError("bad_request", "text 不能为空")
        sender = str(data.get("from") or "").strip() or None
        said = wechat_say_text(text, sender)
        spoken: str | None = None
        try:
            spoken = self.companion.announce({"text": said}).get("text")
        except HubError as exc:
            if exc.code != "not_found":
                raise
            # 伴侣没开：微信就不出声了，但仍留一条到手机
        item = self.relay_to_phone(said or text, source="wechat")
        return {"ok": True, "said": spoken, "notify_id": item["id"]}

    def companion_memory(self) -> dict[str, Any]:
        """设置页「她记得的事」：列表 + 上限，前端渲染成可删的条目。"""
        return {"items": self.memory.entries(), "limit": MEMORY_LIMIT}

    def companion_memory_delete(self, key: str) -> dict[str, Any]:
        if not self.memory.forget_key(key):
            raise HubError("not_found", f"没有这条记忆：{key}")
        return {"ok": True, "key": key}

    def companion_chats(
        self, *, limit: int = 200, query: str = "", day: str = "", with_days: bool = False
    ) -> dict[str, Any]:
        # F30 时间线：day 挑一天翻旧账，with_days 把记过话的日子一并带回
        body: dict[str, Any] = {"items": self.chat_log.list(limit=limit, query=query, day=day)}
        if with_days:
            body["days"] = self.chat_log.days()
        return body

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

    def run_scene(self, scene_id: str, *, say: bool = True) -> dict[str, Any]:
        """把一组开关一次做完；单个设备失败不影响其余的。"""
        scene = self.config.scene(scene_id)
        steps: list[tuple[str, dict[str, Any]]] = []
        for device_id in scene.on:
            steps.append((device_id, {"on": True}))
        for device_id, level in scene.brightness.items():
            steps.append((device_id, {"on": True, "brightness": level}))
        for device_id in scene.off:
            steps.append((device_id, {"on": False}))
        actions: list[dict[str, Any]] = []
        for device_id, body in steps:
            actions.append(self._scene_step(scene_id, device_id, body))
        for device_id in scene.run:
            actions.append(self._scene_run_step(scene_id, device_id))
        spoke = bool(say) and bool(scene.say) and self._scene_say(scene.say or "")
        return {
            "id": scene.id,
            "name": scene.name,
            "ok": all(item["ok"] for item in actions),
            "actions": actions,
            "said": spoke,
        }

    def leave_home(self) -> dict[str, Any]:
        """「我出门了」（F18）：能关的全关，回头报告没关掉的。

        对象是所有能开关的设备（灯 / 插座 / 灯带）；启动项是「开」的东西，
        传感器没有开关，都不碰。报的结果里 `left` 是没关掉的，
        话里点两名（最多两个，多了念不完）。
        """
        actions: list[dict[str, Any]] = []
        for device in self.config.devices:
            if not device.is_switchable:
                continue
            actions.append(self._scene_step("leave", device.id, {"on": False}))
        left = [item for item in actions if not item["ok"]]
        label = {cfg.id: cfg.name for cfg in self.config.devices}
        missed = [label.get(item["id"], item["id"]) for item in left]
        if not actions:
            spoken = "家里没有归我管的灯和插座。"
        elif not missed:
            spoken = f"都关好了，{len(actions)} 个，路上小心。"
        elif len(missed) == len(actions):
            spoken = f"一个都没关成，{'、'.join(missed[:2])}还开着，回头看看。"
        else:
            spoken = f"{'、'.join(missed[:2])}没关上，其他的都关了。"
        return {
            "ok": not missed and bool(actions),
            "actions": actions,
            "left": [item["id"] for item in left],
            "spoken": spoken,
        }

    def report_presence(self, state: str, now: datetime | None = None) -> dict[str, Any]:
        """在场上报（F25）：手机连上 / 离开家里 WiFi。

        与 `note_presence()` 不同：那个记的是「K20 刚来过 snapshot，人还在
        链路上」，这里记的是「人在不在家」这个状态本身。只在 away → home
        那一刻问候一句；unknown → home（Hub 刚起来的第一报）不问候，免得
        每次重启都「欢迎回家」。同一状态重复上报幂等。被配额或免打扰拦下
        就不说，不补发。
        """
        if state not in ("home", "away"):
            raise HubError("bad_request", "state 只能是 home 或 away")
        now = now or datetime.now().astimezone()
        with self._presence_lock:
            previous = self._presence
            self._presence = state
            self._presence_at = now.isoformat(timespec="seconds")
        said: Said | None = None
        if previous == "away" and state == "home":
            text = self.proactive.config.presence.home_text
            if text:
                said = self.proactive.presence(text, now)
                print(f"[proactive] 回家问候：{said.text or said.reason}")
        return {
            "ok": True,
            "state": state,
            "previous": previous,
            "said": said.text if said else None,
        }

    def _presence_payload(self) -> dict[str, Any]:
        with self._presence_lock:
            return {"state": self._presence, "since": self._presence_at}

    def report_desk(self, present: bool, away_sec: int | None = None, now: datetime | None = None) -> dict[str, Any]:
        """在桌上报（F24）：摄像头本地检测的「有人 / 没人」。

        图像不出设备，这里只收一个布尔值。离座满 5 分钟后回来
        （away → present 且复算离座够长）问候一句；坐下→离开只记状态
        不说话。同一状态重复上报幂等。去抖在安卓侧，这里只把关闸
        （DESK_AWAY_MIN_SEC）复算一遍。
        """
        if not isinstance(present, bool):
            raise HubError("bad_request", "present 必须是布尔值")
        if away_sec is not None:
            if not isinstance(away_sec, int) or isinstance(away_sec, bool) or away_sec < 0:
                raise HubError("bad_request", "away_sec 必须是非负整数")
        now = now or datetime.now().astimezone()
        with self._at_desk_lock:
            previous = self._at_desk
            self._at_desk = "present" if present else "away"
            self._at_desk_at = now.isoformat(timespec="seconds")
        # F27：在桌时长按边沿积分，tick 里带上限防卡死
        try:
            self.ledger.note_desk(present, now)
        except Exception as exc:  # noqa: BLE001 - 台账坏了不该连累问候
            logs.swallowed("周报在桌记账", exc)
        said: Said | None = None
        if previous == "away" and present:
            gap = away_sec if away_sec is not None else 0
            if gap >= self.DESK_AWAY_MIN_SEC:
                text = self.proactive.config.presence.desk_text
                if text:
                    said = self.proactive.desk(text, now)
                    print(f"[proactive] 回座问候：{said.text or said.reason}")
        return {
            "ok": True,
            "present": present,
            "away_sec": away_sec,
            "said": said.text if said else None,
        }

    def _at_desk_payload(self) -> dict[str, Any]:
        with self._at_desk_lock:
            return {"state": self._at_desk, "since": self._at_desk_at}

    def _scene_step(
        self, scene_id: str, device_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        item: dict[str, Any] = {"id": device_id, "on": body.get("on"), "ok": True}
        if "brightness" in body:
            item["brightness"] = body["brightness"]
        try:
            self.command(device_id, body)
        except HubError as exc:
            item["ok"] = False
            item["code"] = exc.code
            item["message"] = exc.message
        return item

    def _scene_run_step(self, scene_id: str, device_id: str) -> dict[str, Any]:
        """场景里的启动项：复用单台启动那条路（探针 / 本地一个样）。"""
        item: dict[str, Any] = {"id": device_id, "run": True, "ok": True}
        try:
            self._command_action(self.config.device(device_id), {"run": True})
        except HubError as exc:
            item["ok"] = False
            item["code"] = exc.code
            item["message"] = exc.message
        return item

    def _scene_say(self, text: str) -> bool:
        """场景里的那句话交给伴侣去说，别挡着 HTTP 返回。"""
        companion = self.config.companion
        if companion is None or not companion.enabled:
            return False

        def speak() -> None:
            try:
                self.companion.announce({"text": text})
            except HubError:
                pass

        threading.Thread(target=speak, name="scene-say", daemon=True).start()
        return True

    def _start_focus(self) -> None:
        """游戏开起来自动切场景。没配就不起线程；改了配置重建线程。"""
        self._focus_stop.set()
        self._focus_active = None  # 换了场景配置，边沿重新算
        # 换配置时游戏音量别被留在 0：上一局静音还没恢复就先恢复
        if not (self.config.focus.enabled and self.config.focus.mute):
            self._restore_game_volume()
        if not self.config.focus.enabled or not self.config.focus.on:
            self._focus_stop = threading.Event()
            return
        self._focus_stop = threading.Event()
        stop = self._focus_stop

        def loop() -> None:
            while not stop.wait(15.0):
                try:
                    self._evaluate_focus()
                except Exception as exc:  # 联动挂了不能拖垮 Hub
                    print(f"[focus] 出错：{exc}")

        threading.Thread(target=loop, name="focus", daemon=True).start()

    def _start_dusk(self) -> None:
        """日落联动（F16）。没配就不起线程；改了配置重建线程。"""
        if self._dusk is not None:
            self._dusk.stop()
        self._dusk = None
        if not self.config.dusk.enabled or not self.config.dusk.device:
            return
        self._dusk = DuskRamp(
            self.config.dusk,
            sunset=self._dusk_sunset,
            set_brightness=self._dusk_set,
            device_on=self._dusk_device_on,
        )
        self._dusk.start()

    def _dusk_sunset(self) -> str | None:
        """今天这座城的日落，"HH:MM"。城市按配置写死的，没写就跟着手机查天气的那座。"""
        from dock_hub.weather import fetch_sun, last_city

        city = self.config.dusk.city or last_city()
        if not city:
            return None
        try:
            return str(fetch_sun(city).get("sunset") or "") or None
        except Exception as exc:  # noqa: BLE001 - 天气源挂了等下一分钟再问
            print(f"[dusk] 日落时间没拿到：{exc}")
            return None

    def _dusk_set(self, device_id: str, level: int) -> None:
        self.command(device_id, {"on": True, "brightness": level})

    def _dusk_device_on(self, device_id: str) -> bool:
        """灯现在亮不亮。拿不到状态就当亮着——联动不因读不到状态就罢工。"""
        state = self._companion_device_state(device_id)
        return bool(state is None or state.get("on") is not False)

    def _start_alarm(self) -> None:
        """起床闹钟（F11）。没配就不起线程；改了配置重建线程。"""
        if self._alarm is not None:
            self._alarm.stop()
        self._alarm = None
        if not self.config.alarm.at:
            return
        self._alarm = AlarmRamp(
            self.config.alarm,
            set_brightness=self._dusk_set,
            speak=self._alarm_speak,
        )
        self._alarm.start()

    def _alarm_speak(self) -> None:
        """爬坡最后一格的那一声。走主动开口的闸，手机不在就不喊，不硬来。"""
        spoken = str(self.config.alarm.say or "").strip()
        if spoken:
            self.companion.speak_up(spoken)

    def _evaluate_focus(self) -> None:
        """边沿触发：从「没在玩」变「在玩」切 on 场景，反过来切 off 场景。

        启动时如果游戏已经开着，只记录状态、不触发——那不是「刚开起来」。
        """
        focus = self.config.focus
        running = self._game_running()
        previous = self._focus_active
        self._focus_active = running
        if previous is None or previous == running:
            return
        if running:
            self._mute_for_game()
        else:
            self._restore_game_volume()
        scene_id = focus.on if running else focus.off
        if not scene_id:
            return
        try:
            out = self.run_scene(scene_id)
        except HubError as exc:
            print(f"[focus] 场景 {scene_id} 没跑成：{exc.message}")
            return
        print(f"[focus] {'进' if running else '退'}游戏 → {scene_id}：{out.get('name')}")

    def _mute_for_game(self) -> None:
        """进游戏把音量收到 0（F22）。读不到当前音量就不动——别瞎恢复。"""
        if not self.config.focus.mute:
            return
        current = self._read_volume()
        if current is None:
            return
        try:
            self._write_volume(0)
        except Exception as exc:  # noqa: BLE001 - 静音失败不算事
            logs.swallowed("游戏静音", exc)
            return
        self._volume_before_game = current
        print(f"[focus] 进游戏，音量先收到 0（原来 {current}）")

    def _restore_game_volume(self) -> None:
        """退游戏把音量放回原样。没静过音就什么都不做。"""
        level = self._volume_before_game
        if level is None:
            return
        self._volume_before_game = None
        try:
            self._write_volume(level)
        except Exception as exc:  # noqa: BLE001
            logs.swallowed("恢复游戏前音量", exc)
            return
        print(f"[focus] 退游戏，音量恢复到 {level}")

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

    def power_action(self, action: str) -> None:
        """F21：关机 / 睡眠 / 锁屏，让探针去按。二次确认在 Companion 侧，
        这一层只管把话递到。"""
        if action not in ("shutdown", "sleep", "lock"):
            raise HubError("bad_request", "power 只认 shutdown / sleep / lock")
        base = (self.config.probe.url or "").rstrip("/")
        if not base:
            raise HubError("offline", "未配置 Windows 探针，动不了电脑电源")
        token = (self.config.probe.token or self.config.token).strip()
        raw = json.dumps({"power": action}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base + "/v1/power", data=raw, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
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
        raise HubError("action_error", message or "电源动作没执行成")

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
            "scenes": [
                {"id": item.id, "name": item.name} for item in self.config.scenes
            ],
            "presence": self._presence_payload(),
            "at_desk": self._at_desk_payload(),
            "notify": self.notify.snapshot(),
            "alarm": self._alarm.snapshot() if self._alarm else None,
            "theme": self._theme_payload(),
            "photos": self._photos_payload(),
            "wallpaper": self._wallpaper_payload(),
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
        except Exception as exc:  # noqa: BLE001 - 后台刷新失败不能拖垮请求线程
            logs.swallowed("后台刷新米家设备缓存", exc)
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

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from dock_hub import logs

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round(value: float, digits: int = 1) -> float:
    return round(float(value), digits)


class PcSampler:
    def __init__(
        self,
        enabled: bool,
        sample_ms: int,
        cpu_temp: bool,
        gpu: bool,
    ) -> None:
        self.enabled = enabled
        self.sample_ms = max(200, int(sample_ms))
        self.want_cpu_temp = cpu_temp
        self.want_gpu = gpu
        self._lock = threading.Lock()
        self._sample: dict[str, Any] | None = None
        # 还没有任何样本时的「离线」时间戳。每次读都打当前时间的话，
        # /v1/snapshot 的 payload 会每 3s 变一次，客户端每次都得重建状态。
        self._since = utc_now()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._nvml_ok = False
        self._lhm = None
        self._lhm_failed = False
        self._cpu_temp_warned = False

    def start(self) -> None:
        if not self.enabled:
            return
        if psutil is not None:
            psutil.cpu_percent(interval=None)
        self._init_nvml()
        self._thread = threading.Thread(target=self._loop, name="dock-pc-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        with self._lock:
            if self._sample is None:
                return {"online": False, "updated_at": self._since}
            return dict(self._sample)

    def _loop(self) -> None:
        interval = self.sample_ms / 1000.0
        self._init_lhm()
        while True:
            try:
                self._sample_once()
            except Exception:
                self._note_offline()
            if self._stop.wait(interval):
                break

    def _note_offline(self) -> None:
        """记一次「采不到」。已经是离线态就别再改写时间戳了。"""
        with self._lock:
            current = self._sample
            if isinstance(current, dict) and current.get("online") is False:
                return
            self._sample = {"online": False, "updated_at": utc_now()}

    def _sample_once(self) -> None:
        if psutil is None:
            self._note_offline()
            return

        cpu: dict[str, Any] = {"percent": _round(psutil.cpu_percent(interval=None))}
        if self.want_cpu_temp:
            temp = self._cpu_temp_celsius()
            if temp is not None:
                cpu["temp_celsius"] = _round(temp)
            elif not self._cpu_temp_warned:
                self._cpu_temp_warned = True
                print(
                    "CPU 温度读不到。Windows 需要管理员权限，请用 hub\\run-admin.ps1 启动 Hub。",
                    flush=True,
                )

        mem = psutil.virtual_memory()
        memory: dict[str, Any] = {
            "percent": _round(mem.percent),
            "used_gb": _round(mem.used / (1024**3)),
            "total_gb": _round(mem.total / (1024**3)),
        }

        sample: dict[str, Any] = {
            "online": True,
            "updated_at": utc_now(),
            "cpu": cpu,
            "memory": memory,
        }
        if self.want_gpu:
            gpu = self._gpu_sample()
            if gpu:
                sample["gpu"] = gpu
        with self._lock:
            self._sample = sample

    def _init_nvml(self) -> None:
        if not self.want_gpu:
            return
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml_ok = True
        except Exception:
            self._nvml_ok = False

    def _gpu_sample(self) -> dict[str, Any] | None:
        if not self._nvml_ok:
            return None
        try:
            import pynvml

            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            name = _short_gpu_name(str(name))
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpu: dict[str, Any] = {}
            if name:
                gpu["name"] = name
            gpu["percent"] = _round(float(util.gpu))
            try:
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                gpu["temp_celsius"] = _round(float(temp))
            except Exception as exc:  # noqa: BLE001 - 部分驱动不给温度，有占用率就够
                logs.swallowed("读取 GPU 温度", exc)
            if mem.total:
                gpu["vram_percent"] = _round(mem.used / mem.total * 100)
                gpu["vram_used_gb"] = _round(mem.used / (1024**3))
                gpu["vram_total_gb"] = _round(mem.total / (1024**3))
            return gpu or None
        except Exception:
            return None


    def _init_lhm(self) -> None:
        if not self.want_cpu_temp or self._lhm_failed:
            return
        try:
            from HardwareMonitor.Util import OpenComputer

            self._lhm = OpenComputer(cpu=True)
        except Exception:
            self._lhm = None
            self._lhm_failed = True

    def _cpu_temp_celsius(self) -> float | None:
        if self._lhm is None:
            return _wmi_cpu_temp()
        try:
            from HardwareMonitor.Hardware import SensorType

            package = None
            average = None
            others: list[float] = []
            for hw in self._lhm.Hardware:
                hw.Update()
                for sensor in hw.Sensors:
                    if sensor.SensorType != SensorType.Temperature:
                        continue
                    value = sensor.Value
                    if value is None:
                        continue
                    celsius = float(value)
                    if not (0 < celsius < 120):
                        continue
                    name = str(sensor.Name)
                    if "TjMax" in name:
                        continue
                    if "Package" in name:
                        package = celsius
                    elif "Average" in name:
                        average = celsius
                    else:
                        others.append(celsius)
            if package is not None:
                return package
            if average is not None:
                return average
            if others:
                return sum(others) / len(others)
        except Exception as exc:  # noqa: BLE001 - LHM 没装/没开就走 WMI
            logs.swallowed("读取 CPU 温度（LHM）", exc)
        return _wmi_cpu_temp()


def _short_gpu_name(name: str) -> str:
    return name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip()


def _wmi_cpu_temp() -> float | None:
    if psutil is None:
        return None
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    try:
        pythoncom.CoInitialize()
        wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        svc = wmi.ConnectServer(".", r"root\wmi")
        rows = svc.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
        for row in rows:
            tenths = float(row.CurrentTemperature)
            # WMI is in tenths of Kelvin
            celsius = tenths / 10.0 - 273.15
            if 0 < celsius < 120:
                return celsius
    except Exception:
        return None
    return None



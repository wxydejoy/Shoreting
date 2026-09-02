"""Windows 系统媒体键 / 音量键。snapshot.media；命令走保留 id media。"""

from __future__ import annotations

import sys
import threading
from typing import Any

from dock_hub.errors import HubError
from dock_hub.pc import utc_now

MEDIA_ACTIONS = frozenset({"toggle", "next", "previous"})
VOLUME_ACTIONS = frozenset({"up", "down", "mute"})

_VK = {
    "toggle": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "up": 0xAF,
    "down": 0xAE,
    "mute": 0xAD,
}
_KEYEVENTF_KEYUP = 0x0002


def send_media_key(action: str) -> None:
    vk = _VK.get(action)
    if vk is None:
        raise HubError("unsupported", f"不认识的媒体键：{action}")
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


class MediaSession:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        self._playing = False
        self._send = send_media_key

    def snapshot(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        with self._lock:
            playing = self._playing
        return {
            "online": True,
            "playing": playing,
            "updated_at": utc_now(),
        }

    def command(self, action: str) -> dict[str, Any]:
        if not self.enabled:
            raise HubError("not_found", "未开启系统媒体")
        if action not in MEDIA_ACTIONS:
            raise HubError("unsupported", "media 只接受 toggle / next / previous")
        try:
            self._send(action)
        except HubError:
            raise
        except OSError as exc:
            raise HubError("media_error", "系统媒体键发送失败") from exc
        with self._lock:
            if action == "toggle":
                self._playing = not self._playing
            else:
                self._playing = True
        snap = self.snapshot()
        assert snap is not None
        return snap

    def volume(self, action: str) -> None:
        if action not in VOLUME_ACTIONS:
            raise HubError("unsupported", "音量只接受 up / down / mute")
        try:
            self._send(action)
        except HubError:
            raise
        except OSError as exc:
            raise HubError("media_error", "系统音量键发送失败") from exc

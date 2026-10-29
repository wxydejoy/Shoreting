"""Windows system tray for 岸亭 Hub."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from dock_hub.service import DockHub


def _resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / "assets" / name


def load_tray_image():
    from PIL import Image

    icon = _resource_path("icon.png")
    if icon.is_file():
        return Image.open(icon)
    # fallback: solid mark
    img = Image.new("RGBA", (64, 64), (32, 120, 200, 255))
    return img


def run_tray(hub: "DockHub", stop_server: Callable[[], None], status_line: str) -> None:
    """Block on the tray event loop until the user quits."""
    import pystray
    from pystray import MenuItem as Item

    def open_setup(icon: pystray.Icon, _item: object) -> None:
        from dock_hub.setup_ui import open_setup_browser

        open_setup_browser(hub, force=True)

    def open_chats(icon: pystray.Icon, _item: object) -> None:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{hub.config.port}/chats")

    def open_read(icon: pystray.Icon, _item: object) -> None:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{hub.config.port}/read")

    def quit_app(icon: pystray.Icon, _item: object) -> None:
        icon.stop()
        stop_server()

    menu = pystray.Menu(
        Item(status_line, None, enabled=False),
        Item("打开配置向导", open_setup),
        Item("打开聊天记录", open_chats),
        Item("打开朗读", open_read),
        pystray.Menu.SEPARATOR,
        Item("退出", quit_app),
    )
    icon = pystray.Icon("dock-hub", load_tray_image(), "Shoreting Hub", menu)
    icon.run()


def run_probe_tray(
    config,
    stop_server: Callable[[], None],
    status_line: str,
    pages: dict[str, str] | None = None,
) -> None:
    """Windows 探针托盘：本机读取 yaml token，自动登录 Mini 配置页。"""
    import webbrowser

    import pystray
    from pystray import MenuItem as Item

    from dock_hub.probe import hub_lan_pages

    pages = pages or hub_lan_pages(config)
    setup_url = pages.get("setup_login") or pages["setup"]

    def open_url(url: str):
        def _open(_icon: pystray.Icon, _item: object) -> None:
            webbrowser.open(url)

        return _open

    def copy_setup(_icon: pystray.Icon, _item: object) -> None:
        _copy_text(setup_url)

    def quit_app(icon: pystray.Icon, _item: object) -> None:
        icon.stop()
        stop_server()

    menu = pystray.Menu(
        Item(status_line, None, enabled=False),
        Item("打开配置向导", open_url(setup_url)),
        Item("打开聊天记录", open_url(pages["chats"])),
        Item("打开朗读", open_url(pages["read"])),
        Item("复制配置地址", copy_setup),
        pystray.Menu.SEPARATOR,
        Item("退出", quit_app),
    )
    icon = pystray.Icon("dock-probe", load_tray_image(), "岸亭探针", menu)
    icon.run()


def _copy_text(text: str) -> None:
    if sys.platform != "win32":
        return
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Set-Clipboard", "-LiteralText", text],
        check=False,
        creationflags=flags,
    )

"""Open the local Shoreting service console."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from dock_console.server import read_hub_token, serve
from dock_console.services import Controller, run_launchctl

HUB_YAML = Path.home() / ".config" / "dock-hub" / "hub.yaml"

PORT = 17880


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parent_is_origin(origin: int) -> bool:
    """父进程还在原位上吗。抽出来是为了能测，不用起线程。"""
    return os.getppid() == origin


def follow_parent(poll: float = 2.0) -> threading.Thread | None:
    """父进程没了就自己退出。

    app 被强杀（或崩溃）时，它拉起的子进程会被 launchd 收养，ppid 变成 1，然后一直占着
    17880——下次开 app 一探「端口有人应答」就不再拉起新的，页面永远停在旧代码上。
    """
    origin = os.getppid()
    if origin == 1:
        return None  # 本来就是 launchd 直接拉起的，没有父进程可跟

    def watch() -> None:
        while True:
            time.sleep(poll)
            if not parent_is_origin(origin):
                os._exit(0)  # noqa: SLF001 - 收尾全靠内核，别留半截线程

    thread = threading.Thread(target=watch, name="parent-watch", daemon=True)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="岸亭本机服务控制台")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        print("这个控制台只管理 Mac 上的 LaunchAgent。", file=sys.stderr)
        return 1
    controller = Controller(
        repo=repo_root(),
        home=Path.home(),
        uid=os.getuid(),
        run=run_launchctl,
        hub_yaml=HUB_YAML,
    )
    # 局域网访问凭 hub.yaml 的 token；没有 token 就只许本机，不能裸奔
    token = read_hub_token(HUB_YAML)
    host = args.host
    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        print("hub.yaml 里没读到 token，控制台退回只听本机。", file=sys.stderr)
        host = "127.0.0.1"
    try:
        httpd = serve(controller, host=host, port=args.port, token=token or None)
    except OSError as exc:
        print(f"端口 {args.port} 打不开：{exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"岸亭控制台 {url}", flush=True)
    if host in ("127.0.0.1", "localhost", "::1"):
        print("只在本机打开。Ctrl+C 退出控制台，不会关掉 Hub。", flush=True)
    else:
        print(
            f"局域网打开：http://<本机地址>:{args.port}/?key=<hub.yaml 的 token>。",
            flush=True,
        )
    follow_parent()
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n控制台已退出", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

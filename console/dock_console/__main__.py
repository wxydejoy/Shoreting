"""Open the local Shoreting service console."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from dock_console.server import serve
from dock_console.services import Controller, run_launchctl

HUB_YAML = Path.home() / ".config" / "dock-hub" / "hub.yaml"

PORT = 17880


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="岸亭本机服务控制台")
    parser.add_argument("--port", type=int, default=PORT)
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
    try:
        httpd = serve(controller, port=args.port)
    except OSError as exc:
        print(f"端口 {args.port} 打不开：{exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"岸亭控制台 {url}", flush=True)
    print("只在本机打开。Ctrl+C 退出控制台，不会关掉 Hub。", flush=True)
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

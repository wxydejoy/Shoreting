from __future__ import annotations

import argparse
import os
import signal
import sys
import threading

from helm_mini import DEFAULT_TOKEN
from helm_mini.sampler import MiniSampler
from helm_mini.server import lan_ips, serve
from helm_mini.telemetry import TelemetryStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="岸亭 Mini 本机监控")
    parser.add_argument("--host", default=os.environ.get("HELM_MINI_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HELM_MINI_PORT", "17891")))
    parser.add_argument("--name", default=os.environ.get("HELM_MINI_NAME", "mini"))
    parser.add_argument("--sample-ms", type=int, default=int(os.environ.get("HELM_MINI_SAMPLE_MS", "1000")))
    args = parser.parse_args(argv)

    token = os.environ.get("HELM_MINI_TOKEN", DEFAULT_TOKEN).strip() or DEFAULT_TOKEN
    sampler = MiniSampler(sample_ms=args.sample_ms)
    sampler.start()
    store = TelemetryStore()
    persist_stop = threading.Event()

    def persist_mini() -> None:
        while not persist_stop.wait(1.0):
            try:
                store.touch_mini(sampler.snapshot(), persist_log=True)
            except Exception:
                continue

    threading.Thread(target=persist_mini, name="mini-desk-log", daemon=True).start()
    httpd = serve(args.host, args.port, sampler, token, args.name, store)

    def shutdown(_signum=None, _frame=None) -> None:
        persist_stop.set()
        sampler.stop()
        httpd.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    ips = lan_ips()
    print(f"岸亭 Mini  :{args.port}  ({args.name})", flush=True)
    for ip in ips:
        print(f"  探活  http://{ip}:{args.port}/health", flush=True)
        print(f"  状态  http://{ip}:{args.port}/v1/snapshot", flush=True)
        print(f"  书桌  http://{ip}:{args.port}/v1/desk", flush=True)
    print(f"手机设置默认地址 {ips[0]} 端口 {args.port}，Token 已启用。", flush=True)
    print("已启用 Bearer token。", flush=True)
    try:
        httpd.serve_forever()
    finally:
        persist_stop.set()
        sampler.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

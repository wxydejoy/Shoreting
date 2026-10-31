/**
 * Hub 的数据接口。请求走控制台自己的 /api/hub/*（同源转发），不跨端口，
 * Token 也不进浏览器 —— 见 dock_console/hub_proxy.py。
 */

import { useCallback, useEffect, useRef, useState } from "react";

const ROOT = "/api/hub";

export class HubError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "HubError";
    this.status = status;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = init?.headers ?? (init?.body ? { "Content-Type": "application/json" } : undefined);
  const response = await fetch(`${ROOT}${path}`, {
    cache: "no-store",
    ...init,
    headers,
  });
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new HubError(text.slice(0, 200) || `Hub 回了 ${response.status}`, response.status);
  }
  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "message" in payload
        ? String((payload as { message?: unknown }).message ?? "")
        : payload &&
            typeof payload === "object" &&
            "error" in payload &&
            typeof (payload as { error?: { message?: unknown } }).error === "object"
          ? String((payload as { error?: { message?: string } }).error?.message ?? "")
          : "";
    throw new HubError(message || `Hub 回了 ${response.status}`, response.status);
  }
  return payload as T;
}

export function hubGet<T>(path: string): Promise<T> {
  return call<T>(path);
}

export function hubPost<T>(path: string, body?: unknown): Promise<T> {
  return call<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export function hubDelete<T>(path: string): Promise<T> {
  return call<T>(path, { method: "DELETE" });
}

/** 把一整本书丢进 Hub 的书架。文件名走 X-Filename，body 是原始字节。 */
export function hubUpload<T>(path: string, file: File, name = file.name): Promise<T> {
  return file.arrayBuffer().then((buffer) =>
    call<T>(path, {
      method: "POST",
      body: buffer,
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Filename": encodeURIComponent(name),
      },
    }),
  );
}

/** 音频、wav 下载这类要给 <audio>/<a> 用的地址。 */
export function hubFile(path: string): string {
  return `${ROOT}${path}`;
}

type HubState<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (next: T) => void;
};

/** 读一份 Hub 数据；path 为 null 时不发请求。pollMs > 0 就轮询。 */
export function useHub<T>(path: string | null, pollMs = 0): HubState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [tick, setTick] = useState(0);
  const alive = useRef(0);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    if (!path) {
      setLoading(false);
      return;
    }
    const seq = ++alive.current;
    let cancelled = false;

    const load = async (first: boolean) => {
      if (first) setLoading(true);
      try {
        const next = await hubGet<T>(path);
        if (cancelled || seq !== alive.current) return;
        setData(next);
        setError(null);
      } catch (err) {
        if (cancelled || seq !== alive.current) return;
        // 轮询失败不抹掉上一份数据，前端还能看着旧的
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled && seq === alive.current) setLoading(false);
      }
    };

    void load(true);
    if (pollMs <= 0) return () => void (cancelled = true);

    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(false);
    }, pollMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [path, pollMs, tick]);

  return { data, error, loading, reload, setData };
}

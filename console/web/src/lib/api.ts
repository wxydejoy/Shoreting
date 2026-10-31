/** 控制台自己的 HTTP 接口。服务端在 dock_console/server.py，两边字段名要一起改。 */

export type WindowsProbe = {
  url: string;
  address?: string;
  online: boolean;
  label: string;
};

export type Service = {
  id: string;
  name: string;
  blurb: string;
  label: string;
  port: number;
  install: string;
  log_file: string;
  loaded: boolean;
  domain: string | null;
  state: string | null;
  pid: number | null;
  installed: boolean;
  health_ok: boolean;
  health_summary: string;
  health_error: string | null;
  log_tail: string;
  state_label: string;
  windows?: WindowsProbe;
};

export type FeatureKind =
  | "bool"
  | "int"
  | "number"
  | "time"
  | "text"
  | "device"
  | "scene"
  | "quiet";

export type FeatureOption = { value: string; label: string };

export type Feature = {
  key: string;
  group: string;
  label: string;
  kind: FeatureKind;
  value: unknown;
  hint?: string;
  unit?: string;
  low?: number | null;
  high?: number | null;
  step?: number | null;
  optional?: boolean;
  options?: FeatureOption[];
};

export type Group = { id: string; title: string; blurb?: string };

export type Catalog = {
  ok: boolean;
  message?: string;
  groups?: Group[];
  features?: Feature[];
  config_path?: string;
};

export type Result = { ok: boolean; message?: string; value?: unknown };

export type ServiceAction = "start" | "stop" | "restart";

export const HUB_ORIGIN = `http://${window.location.hostname || "127.0.0.1"}:17890`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(text.slice(0, 160) || `控制台回了 ${response.status}`);
  }
  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "message" in payload
        ? String((payload as { message?: unknown }).message ?? "")
        : "";
    throw new Error(message || `控制台回了 ${response.status}`);
  }
  return payload as T;
}

export async function fetchStatus(): Promise<Service[]> {
  const data = await request<{ services?: Service[] }>("/api/status");
  return data.services ?? [];
}

export async function fetchCatalog(): Promise<Catalog> {
  return request<Catalog>("/api/features");
}

export async function servicesAction(service: string, action: ServiceAction): Promise<Result> {
  return request<Result>(`/api/services/${service}/${action}`, { method: "POST" });
}

export async function allAction(action: ServiceAction): Promise<Result> {
  return request<Result>(`/api/all/${action}`, { method: "POST" });
}

export async function saveFeature(key: string, value: unknown): Promise<Result> {
  return request<Result>("/api/features", {
    method: "POST",
    body: JSON.stringify({ key, value }),
  });
}

/**
 * 手机上的视频背景库。控制台把请求转给手机自己起的背景服务（:17920），
 * 手机没开 / 不在同一个 Wi-Fi 时这些函数会抛一句人话。
 */
export type BackgroundItem = {
  id: string;
  name: string;
  kind: "image" | "video";
  bytes: number;
  modified?: number;
  owner?: string;
  /** 抽过帧的视频顺手带上；没抽过就没有 */
  duration?: number;
  width?: number;
  height?: number;
};

export type BackgroundList = {
  ok: boolean;
  current?: string | null;
  items?: BackgroundItem[];
  address?: string | null;
  message?: string;
};

export function fetchBackgrounds(): Promise<BackgroundList> {
  return request<BackgroundList>("/api/phone/backgrounds");
}

export function selectBackground(id: string): Promise<BackgroundList> {
  return request<BackgroundList>(`/api/phone/backgrounds/${encodeURIComponent(id)}/select`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function deleteBackground(id: string): Promise<BackgroundList> {
  return request<BackgroundList>(`/api/phone/backgrounds/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function clearBackground(): Promise<BackgroundList> {
  return request<BackgroundList>("/api/phone/backgrounds/clear", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/**
 * 手机主屏布局：网页编辑器靠它还原手机现状。模块坐标是 0..1 的画布比例。
 * 手机 APK 没更新到带 /api/layout 的版本时会得到一句「版本太旧」。
 */
export type PhoneLayoutRect = {
  x: number;
  y: number;
  w: number;
  h: number;
  visible: boolean;
  chrome: boolean;
};

export type PhoneLayoutSnapshot = {
  ok: boolean;
  canvas?: { w: number; h: number };
  slots?: { id: string; label: string }[];
  defaults?: Record<string, PhoneLayoutRect>;
  modules?: Record<string, PhoneLayoutRect>;
};

export async function fetchPhoneLayout(): Promise<PhoneLayoutSnapshot> {
  // 手机快照里的 defaults / modules 是包了一层的 DockLayout（{version, modules}），
  // 老版本 APK 发过平铺 map——两种都接住，编辑器一律拿平铺的 rect map。
  const raw = await request<Record<string, unknown>>("/api/phone/layout");
  const unwrap = (value: unknown): Record<string, PhoneLayoutRect> => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    const inner = (value as { modules?: unknown }).modules;
    if (inner && typeof inner === "object" && !Array.isArray(inner)) {
      return inner as Record<string, PhoneLayoutRect>;
    }
    return value as Record<string, PhoneLayoutRect>;
  };
  return {
    ...(raw as PhoneLayoutSnapshot),
    defaults: unwrap(raw.defaults),
    modules: unwrap(raw.modules),
  };
}

/** 上传走 multipart，不设 Content-Type（让浏览器自己写 boundary）。 */
export async function uploadBackground(file: File): Promise<BackgroundList> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch("/api/phone/backgrounds", { method: "POST", body: form });
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(text.slice(0, 160) || `控制台回了 ${response.status}`);
  }
  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "message" in payload
        ? String((payload as { message?: unknown }).message ?? "")
        : "";
    throw new Error(message || `上传失败（${response.status}）`);
  }
  return payload as BackgroundList;
}

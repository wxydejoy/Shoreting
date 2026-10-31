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

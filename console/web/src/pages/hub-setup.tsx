import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Copy,
  Cpu,
  KeyRound,
  Laptop,
  Loader2,
  MonitorCog,
  QrCode,
  RefreshCw,
  Save,
  Smartphone,
  Sparkles,
  Thermometer,
  Volume2,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader, Section } from "@/components/page";
import { HubNotice } from "@/pages/hub-notice";
import { useConsole } from "@/lib/console-store";
import { hubGet, hubPost, useHub } from "@/lib/hub";
import { cn } from "@/lib/utils";

type DeviceRaw = {
  id: string;
  name?: string;
  type?: string;
  icon?: string;
  mijia_name?: string;
  base_url?: string;
  on_brightness?: number;
  follow_pc?: boolean;
  run?: { program?: string; args?: string[]; cwd?: string };
};

type Scene = {
  id: string;
  name: string;
  on?: string[];
  off?: string[];
  brightness?: Record<string, number>;
  say?: string;
  phrases?: string[];
};

type MijiaDevice = { name: string; model: string; online?: boolean | null };

type NodeRaw = { host?: string; ip?: string; port?: number };

type SetupState = {
  config: {
    name: string;
    host: string;
    port: number;
    devices: DeviceRaw[];
    scenes?: Scene[];
    temperature?: { name?: string; mijia_name?: string } | null;
    pc?: { enabled?: boolean; sample_ms?: number; cpu_temp?: boolean; gpu?: boolean } | null;
    media?: { enabled?: boolean } | null;
    mini?: { enabled?: boolean; base_url?: string; pc_ms?: number; mijia_ms?: number } | null;
    probe?: { url?: string; stale_ms?: number; hub_url?: string; listen?: string } | null;
    nodes?: Record<string, NodeRaw>;
    companion?: { enabled?: boolean; tts?: { base_url?: string; deliver?: boolean } };
  };
  token_masked: string;
  secrets?: { probe?: boolean; mini?: boolean; tts?: boolean };
  lan_ips: string[];
  mijia: string;
  mijia_message?: string | null;
  mijia_devices: MijiaDevice[];
  mijia_devices_error?: string | null;
  mijia_login: { phase: string; qr_url?: string | null; message?: string | null };
  config_path: string;
  llm: { base_url: string; model: string; has_key: boolean; key_hint?: string };
  voice: { volume: number; speed: number; saved: boolean };
};

type LlmResult = { ok?: boolean; message?: string; models?: string[]; count?: number; sample?: string };

type NodeForm = { host: string; ip: string; port: string };

type Form = {
  name: string;
  host: string;
  port: string;
  nodes: { android: NodeForm; win: NodeForm; mac: NodeForm };
  probe: { url: string; token: string; hub_url: string; listen: string; stale_ms: string };
  pcOn: boolean;
  cpuTemp: boolean;
  gpu: boolean;
  sampleMs: string;
  mediaOn: boolean;
  miniOn: boolean;
  miniUrl: string;
  miniToken: string;
  miniPcMs: string;
  miniMijiaMs: string;
  ttsUrl: string;
  ttsToken: string;
  ttsDeliver: boolean;
  llmBase: string;
  llmModel: string;
  llmKey: string;
  volume: string;
  speed: string;
  tempName: string;
  tempMijia: string;
};

const NONE = "__none__";

const NODE_ROWS: { key: "android" | "win" | "mac"; label: string; hint: string; icon: typeof Laptop }[] = [
  { key: "android", label: "安卓 K20", hint: "手机自己", icon: Smartphone },
  { key: "win", label: "Windows", hint: "探针 :17892", icon: MonitorCog },
  { key: "mac", label: "Mac", hint: "Hub :17890", icon: Laptop },
];

const TABS = [
  { id: "android", label: "安卓 K20", icon: Smartphone },
  { id: "win", label: "Windows", icon: MonitorCog },
  { id: "mac", label: "Mac", icon: Laptop },
];

function nodeForm(node?: NodeRaw): NodeForm {
  return {
    host: node?.host ?? "",
    ip: node?.ip ?? "",
    port: node?.port ? String(node.port) : "",
  };
}

function buildForm(state: SetupState): Form {
  const nodes = state.config.nodes ?? {};
  return {
    name: state.config.name,
    host: state.config.host,
    port: String(state.config.port),
    nodes: {
      android: nodeForm(nodes.android),
      win: nodeForm(nodes.win),
      mac: nodeForm(nodes.mac),
    },
    probe: {
      url: state.config.probe?.url ?? "",
      token: "",
      hub_url: state.config.probe?.hub_url ?? "",
      listen: state.config.probe?.listen ?? "",
      stale_ms: String(state.config.probe?.stale_ms ?? 4000),
    },
    pcOn: Boolean(state.config.pc?.enabled),
    cpuTemp: Boolean(state.config.pc?.cpu_temp),
    gpu: Boolean(state.config.pc?.gpu),
    sampleMs: String(state.config.pc?.sample_ms ?? 1000),
    mediaOn: Boolean(state.config.media?.enabled),
    miniOn: Boolean(state.config.mini?.enabled),
    miniUrl: state.config.mini?.base_url ?? "",
    miniToken: "",
    miniPcMs: String(state.config.mini?.pc_ms ?? 1000),
    miniMijiaMs: String(state.config.mini?.mijia_ms ?? 15000),
    ttsUrl: state.config.companion?.tts?.base_url ?? "",
    ttsToken: "",
    ttsDeliver: Boolean(state.config.companion?.tts?.deliver),
    llmBase: state.llm.base_url,
    llmModel: state.llm.model,
    llmKey: "",
    volume: String(state.voice.volume),
    speed: String(state.voice.speed),
    tempName: state.config.temperature?.name ?? "",
    tempMijia: state.config.temperature?.mijia_name || NONE,
  };
}

function copyText(text: string, label: string) {
  if (!text) return;
  navigator.clipboard
    .writeText(text)
    .then(() => toast.success(`${label}已复制`))
    .catch(() => toast.error("复制失败，手动选一下吧"));
}

/** mini.lan:17891 这种「地址:端口」，从 URL 里抠掉协议。 */
function addressOf(url: string): string {
  return url.replace(/^https?:\/\//, "").replace(/\/+$/, "");
}

/** 手机上该填的 Mini 地址：Hub 和 Mini 同机时配置里是 127.0.0.1，手机抄这个连不上。 */
function phoneAddressOf(url: string, fallbackHost: string): string {
  const text = addressOf(url);
  if (!text) return "";
  const cut = text.lastIndexOf(":");
  const host = cut > 0 ? text.slice(0, cut) : text;
  const port = cut > 0 ? text.slice(cut) : "";
  if (host === "127.0.0.1" || host === "localhost" || host === "0.0.0.0") {
    return fallbackHost ? `${fallbackHost}${port}` : "";
  }
  return text;
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-[12px] text-muted-foreground">{label}</Label>
      {children}
      {hint ? <p className="text-[11px] leading-relaxed text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function CopyField({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Field label={label} hint={hint}>
      <div className="flex gap-1.5">
        <Input value={value || "—"} readOnly className="h-8 font-mono text-[12.5px]" />
        <Button
          size="icon-sm"
          variant="outline"
          aria-label={`复制${label}`}
          disabled={!value}
          onClick={() => copyText(value, label)}
        >
          <Copy />
        </Button>
      </div>
    </Field>
  );
}

function Row({
  title,
  description,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px]">{title}</div>
        {description ? (
          <div className="text-[11px] leading-relaxed text-muted-foreground">{description}</div>
        ) : null}
      </div>
      {children}
    </div>
  );
}

export function SetupPage({ initialTab }: { initialTab?: string }) {
  const { data, error, loading, reload } = useHub<SetupState>("/setup/api/state", 30_000);
  const { services } = useConsole();
  const probe = services.find((service) => service.id === "hub")?.windows;

  const [tab, setTab] = useState(
    initialTab && TABS.some((item) => item.id === initialTab) ? initialTab : "android",
  );

  // 从别的链接跳进来（#/hub/setup/win）时跟过去；自己点 tab 只改 hash 不重挂
  useEffect(() => {
    if (initialTab && TABS.some((item) => item.id === initialTab)) setTab(initialTab);
  }, [initialTab]);

  const changeTab = (next: string) => {
    setTab(next);
    const target = `#/hub/setup/${next}`;
    if (window.location.hash !== target) window.history.replaceState(null, "", target);
  };
  const [form, setForm] = useState<Form | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [mijiaFilter, setMijiaFilter] = useState("");
  const [login, setLogin] = useState<SetupState["mijia_login"] | null>(null);

  // 只在第一次拿到数据时铺表单：轮询每 30 秒回来一次，不能把正在输入的字洗掉
  useEffect(() => {
    if (data && !form) {
      setForm(buildForm(data));
      setLogin(data.mijia_login);
    }
  }, [data, form]);

  // 扫码中才轮询登录状态；phase 到 done / error 就停
  useEffect(() => {
    if (!login || (login.phase !== "qr" && login.phase !== "starting")) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await hubGet<SetupState["mijia_login"]>("/setup/api/mijia-login/status");
        setLogin(next);
        if (next.phase === "done") {
          toast.success("米家登录成功");
          reload();
        }
        if (next.phase === "error") toast.error(next.message || "米家登录失败");
      } catch {
        /* 轮询失败就等下一轮 */
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [login, reload]);

  const cfg = data?.config;

  const save = (payload: Record<string, unknown>, key: string, label = "已保存") =>
    (async () => {
      setSaving(key);
      try {
        const result = await hubPost<{ ok?: boolean; message?: string }>("/setup/api/save", payload);
        toast.success(result.message || label);
        reload();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(null);
      }
    })();

  const patch = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const patchNode = (key: keyof Form["nodes"], field: keyof NodeForm, value: string) =>
    setForm((prev) =>
      prev
        ? { ...prev, nodes: { ...prev.nodes, [key]: { ...prev.nodes[key], [field]: value } } }
        : prev,
    );

  const patchProbe = (field: keyof Form["probe"], value: string) =>
    setForm((prev) => (prev ? { ...prev, probe: { ...prev.probe, [field]: value } } : prev));

  const saveNodes = () => {
    if (!form) return;
    void save({ nodes: form.nodes }, "nodes", "三端地址已保存");
  };

  const saveProbe = () => {
    if (!form) return;
    void save(
      {
        probe: {
          url: form.probe.url,
          token: form.probe.token,
          hub_url: form.probe.hub_url,
          listen: form.probe.listen,
          stale_ms: form.probe.stale_ms,
        },
      },
      "probe",
      "Windows 探针已保存",
    );
  };

  const savePc = (over?: Partial<{ pcOn: boolean; cpuTemp: boolean; gpu: boolean; sampleMs: string }>) => {
    if (!form) return;
    const next = { ...form, ...over };
    void save(
      {
        pc: {
          enabled: next.pcOn,
          sample_ms: Number(next.sampleMs) || 1000,
          cpu_temp: next.cpuTemp,
          gpu: next.gpu,
        },
      },
      "pc",
      "电脑监控已保存",
    );
  };

  const saveMedia = (enabled: boolean) => {
    patch("mediaOn", enabled);
    void save({ media: { enabled } }, "media", enabled ? "媒体控制已开" : "媒体控制已关");
  };

  const saveMac = () => {
    if (!form) return;
    void save(
      { name: form.name.trim(), host: form.host.trim(), port: Number(form.port) || cfg?.port },
      "mac",
      "这台 Mac 的配置已保存",
    );
  };

  const saveMini = () => {
    if (!form) return;
    void save(
      {
        mini: {
          enabled: form.miniOn,
          base_url: form.miniUrl,
          token: form.miniToken,
          pc_ms: form.miniPcMs,
          mijia_ms: form.miniMijiaMs,
        },
      },
      "mini",
      "Mini 监控已保存",
    );
  };

  const saveCompanion = () => {
    if (!form) return;
    void save(
      {
        // 不提交 enabled：关掉 companion 时 Hub 会把 llm/tts 一起收起来，
        // 这一页只改朗读服务本身，总开关留在 hub.yaml
        companion: { tts: { base_url: form.ttsUrl, token: form.ttsToken, deliver: form.ttsDeliver } },
      },
      "tts",
      "朗读服务已保存",
    );
  };

  const saveTemperature = () => {
    if (!form || !cfg) return;
    if (!cfg.temperature) {
      toast.error("hub.yaml 里没有 temperature 段，先在 yaml 里加一段");
      return;
    }
    void save(
      {
        temperature: {
          ...cfg.temperature,
          name: form.tempName,
          mijia_name: form.tempMijia === NONE ? "" : form.tempMijia,
        },
      },
      "temperature",
      "温湿度已保存",
    );
  };

  const testLlm = async (kind: "models" | "test") => {
    if (!form) return;
    setSaving(kind);
    try {
      const result = await hubPost<LlmResult>(`/setup/api/llm-${kind}`, {
        base_url: form.llmBase.trim(),
        model: form.llmModel.trim(),
        api_key: form.llmKey.trim(),
      });
      if (kind === "models") {
        setModels(result.models ?? []);
        toast[result.ok ? "success" : "error"](
          result.ok ? `拉到 ${result.count ?? result.models?.length ?? 0} 个模型` : result.message || "没拉到",
        );
      } else {
        toast[result.ok ? "success" : "error"](
          result.ok ? `通了 · 它回了「${result.sample ?? ""}」` : result.message || "不通",
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  };

  const saveLlm = async () => {
    if (!form) return;
    setSaving("llm");
    try {
      const result = await hubPost<{ ok?: boolean; message?: string }>("/setup/api/save", {
        llmBaseUrl: form.llmBase.trim(),
        llmModel: form.llmModel.trim(),
        llmApiKey: form.llmKey.trim(),
      });
      patch("llmKey", "");
      toast.success(result.message || "大模型配置已保存");
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  };

  const saveVoice = async () => {
    if (!form) return;
    setSaving("voice");
    try {
      const result = await hubPost<{ ok?: boolean; message?: string }>("/setup/api/voice", {
        volume: Number(form.volume),
        speed: Number(form.speed),
      });
      toast.success(result.message || "声音改好了");
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  };

  const startLogin = async () => {
    setSaving("mijia");
    try {
      const next = await hubPost<SetupState["mijia_login"]>("/setup/api/mijia-login/start", {
        force: true,
      });
      setLogin(next);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  };

  const regenerateToken = async () => {
    setSaving("token");
    try {
      const result = await hubPost<{ token?: string }>("/setup/api/token", {});
      if (!result.token) throw new Error("Hub 没给新 Token");
      await hubPost("/setup/api/save", { token: result.token });
      toast.success("新 Token 已生效，安卓那边要重新填");
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  };

  const revealToken = async () => {
    try {
      const result = await hubPost<{ token?: string }>("/setup/api/token/reveal", {});
      copyText(result.token ?? "", "Token ");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  const mijiaDevices = useMemo(() => {
    const keyword = mijiaFilter.trim();
    const list = data?.mijia_devices ?? [];
    return keyword ? list.filter((item) => item.name.includes(keyword)) : list;
  }, [data?.mijia_devices, mijiaFilter]);

  if (error && !data) {
    return (
      <div className="space-y-5">
        <PageHeader title="三端配置" subtitle="安卓、Windows、Mac 的地址与开关。" />
        <HubNotice message={error} onRetry={reload} />
      </div>
    );
  }

  const busy = (key: string) => saving === key;

  // 安卓那边要填的地址：主机名优先，没填就退回这台机器的局域网 IP
  const hubAddress =
    form?.nodes.mac.host || form?.nodes.mac.ip || data?.lan_ips?.[0] || "127.0.0.1";
  const hubPort = form?.nodes.mac.port || String(cfg?.port ?? 17890);
  const miniAddress = phoneAddressOf(form?.miniUrl ?? "", hubAddress);
  // 探针地址和地址簿里的域名对不上时提一句（老配置里常是 IP 或 mDNS 名）
  const probeHost = addressOf(form?.probe.url ?? "").replace(/:\d+$/, "");
  const probeHostMismatch =
    Boolean(probeHost) &&
    Boolean(form?.nodes.win.host) &&
    probeHost !== form?.nodes.win.host &&
    probeHost !== form?.nodes.win.ip;
  const lanMismatch =
    Boolean(form?.nodes.mac.ip) && Boolean(data?.lan_ips?.length) &&
    !(data?.lan_ips ?? []).includes(form?.nodes.mac.ip ?? "");

  return (
    <div className="space-y-4 pb-6">
      <PageHeader
        title="三端配置"
        subtitle={
          <>
            安卓、Windows、Mac 三台机器都在这一页。改动写进{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11.5px]">
              {data?.config_path ?? "~/.config/dock-hub/hub.yaml"}
            </code>
            ，Hub 自己热加载。
          </>
        }
        actions={
          <Badge variant="outline" className="text-[11px] text-muted-foreground">
            {loading && !data ? "同步中…" : "已同步"}
          </Badge>
        }
      />

      <Card className="gap-0 py-0">
        <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
          <div>
            <span className="text-[12.5px] font-medium">三端地址</span>
            <span className="ml-2 text-[11px] text-muted-foreground">
              域名是固定的名字，IP 由路由器绑给它
            </span>
          </div>
          <Button size="sm" variant="outline" disabled={Boolean(saving) || !form} onClick={saveNodes}>
            {busy("nodes") ? <Loader2 className="animate-spin" /> : <Save />}
            保存
          </Button>
        </div>
        <div className="divide-y divide-border/60">
          {NODE_ROWS.map((row) => (
            <div key={row.key} className="flex flex-wrap items-end gap-2 px-4 py-3">
              <div className="flex w-[112px] shrink-0 items-center gap-1.5 pb-1.5">
                <row.icon className="size-3.5 text-muted-foreground" />
                <div className="leading-tight">
                  <div className="text-[12.5px]">{row.label}</div>
                  <div className="text-[10.5px] text-muted-foreground">{row.hint}</div>
                </div>
              </div>
              <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-[1.2fr_1fr_88px]">
                <Field label="域名（FQDN）">
                  <Input
                    value={form?.nodes[row.key].host ?? ""}
                    onChange={(event) => patchNode(row.key, "host", event.target.value)}
                    placeholder="mini.lan"
                    spellCheck={false}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
                <Field label="IP">
                  <Input
                    value={form?.nodes[row.key].ip ?? ""}
                    onChange={(event) => patchNode(row.key, "ip", event.target.value)}
                    placeholder="10.14.14.117"
                    spellCheck={false}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
                <Field label="端口">
                  <Input
                    value={form?.nodes[row.key].port ?? ""}
                    onChange={(event) =>
                      patchNode(row.key, "port", event.target.value.replace(/\D/g, ""))
                    }
                    inputMode="numeric"
                    placeholder="—"
                    className="h-8 font-mono text-[12.5px] tabular-nums"
                  />
                </Field>
              </div>
            </div>
          ))}
          <div className="bg-muted/35 px-4 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
            在路由器里把主机名绑到 IP（静态租约 / FQDN 重绑）。换网段只改路由器那一行，三台机器上的配置都不用动。
            {lanMismatch ? (
              <span className="ml-1 text-warn">
                这台机器现在的局域网地址是 {data?.lan_ips?.join("、")}，和上面的 IP 对不上，域名解析可能还没生效。
              </span>
            ) : null}
          </div>
        </div>
      </Card>

      <Tabs value={tab} onValueChange={changeTab}>
        <TabsList className="h-8">
          {TABS.map((item) => (
            <TabsTrigger key={item.id} value={item.id} className="gap-1.5 text-[12px]">
              <item.icon className="size-3.5" />
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {/* ------------------------------------------------------------ 安卓 */}
        <TabsContent value="android" className="space-y-4">
          <Card className="gap-0 py-0">
            <div className="border-b border-border/60 px-4 py-2.5 text-[12.5px] font-medium">
              手机上填这三项（岸亭 → 设置 → Hub）
            </div>
            <div className="space-y-4 p-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <CopyField
                  label="地址"
                  value={hubAddress}
                  hint="域名优先；改路由器就好，不用动手机"
                />
                <CopyField label="端口" value={hubPort} hint="改端口要重启 Hub 才生效" />
              </div>
              <Field label="Token" hint="保密。改完手机也要跟着改。">
                <div className="flex flex-wrap gap-1.5">
                  <Input
                    value={data?.token_masked ?? ""}
                    readOnly
                    className="h-8 min-w-[180px] flex-1 font-mono text-[12.5px]"
                  />
                  <Button size="sm" variant="outline" onClick={() => void revealToken()}>
                    <KeyRound />
                    复制
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={Boolean(saving)}
                    onClick={() => void regenerateToken()}
                  >
                    {busy("token") ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                    重新生成
                  </Button>
                </div>
              </Field>
              <CopyField
                label="Mini CPU（主屏第二行的机器）"
                value={form?.miniOn ? miniAddress : ""}
                hint={
                  form?.miniOn
                    ? "口令在 mini/install-launchd.sh 装完那行，不在这里改"
                    : "Mini 监控没开，去 Mac 那页打开"
                }
              />
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <Thermometer className="size-3.5 text-muted-foreground" />
                室内温湿度
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={Boolean(saving) || !form}
                onClick={saveTemperature}
              >
                {busy("temperature") ? <Loader2 className="animate-spin" /> : <Save />}
                保存
              </Button>
            </div>
            <div className="grid gap-4 p-4 sm:grid-cols-2">
              <Field label="显示名称（安卓上看到的）">
                <Input
                  value={form?.tempName ?? ""}
                  onChange={(event) => patch("tempName", event.target.value)}
                  placeholder="卧室"
                  className="h-8 text-[12.5px]"
                />
              </Field>
              <Field label="米家设备">
                <Select value={form?.tempMijia ?? NONE} onValueChange={(next) => patch("tempMijia", next)}>
                  <SelectTrigger size="sm" className="h-8 text-[12.5px]">
                    <SelectValue placeholder="不显示温度" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>不显示温度</SelectItem>
                    {(data?.mijia_devices ?? []).map((device) => (
                      <SelectItem key={device.name} value={device.name} className="text-[12.5px]">
                        {device.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          </Card>

          <Section title="主屏方块" description="顺序就是安卓上的顺序，设备本身在 hub.yaml 里。">
            <Card className="gap-0 py-0">
              <div className="divide-y divide-border/60">
                {(cfg?.devices ?? []).map((device) => (
                  <div key={device.id} className="flex items-center gap-2.5 px-4 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[12.5px]">{device.name || device.id}</div>
                      <div className="truncate font-mono text-[10.5px] text-muted-foreground">
                        {device.type}
                        {device.mijia_name ? ` · 米家：${device.mijia_name}` : ""}
                        {device.base_url ? ` · ${device.base_url}` : ""}
                        {device.run?.program ? ` · ${device.run.program}` : ""}
                      </div>
                    </div>
                    {device.follow_pc ? (
                      <Badge variant="outline" className="text-[10px] text-muted-foreground">
                        跟电脑
                      </Badge>
                    ) : null}
                    {device.type ? (
                      <Badge variant="secondary" className="text-[10px]">
                        {device.type}
                      </Badge>
                    ) : null}
                  </div>
                ))}
                {!cfg?.devices?.length ? (
                  <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">
                    还没有方块。米家设备可以在米家里勾「主屏」。
                  </div>
                ) : null}
              </div>
            </Card>
          </Section>

          {(cfg?.scenes?.length ?? 0) > 0 ? (
            <Section title="场景" description="一句话触发的组合。">
              <Card className="gap-0 py-0">
                <div className="divide-y divide-border/60">
                  {cfg?.scenes?.map((scene) => (
                    <div key={scene.id} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                      <span className="text-[12.5px] font-medium">{scene.name}</span>
                      {scene.phrases?.length ? (
                        <span className="text-[11px] text-muted-foreground">
                          说「{scene.phrases[0]}」
                        </span>
                      ) : null}
                      <span className="ml-auto text-[11px] text-muted-foreground">
                        {scene.on?.length ? `开 ${scene.on.join("、")}` : ""}
                        {scene.off?.length ? `关 ${scene.off.join("、")}` : ""}
                        {scene.say ? ` · 说「${scene.say}」` : ""}
                      </span>
                    </div>
                  ))}
                </div>
              </Card>
            </Section>
          ) : null}

          <div className="flex items-start gap-2.5 rounded-lg border bg-card px-4 py-3 text-[11.5px] leading-relaxed text-muted-foreground">
            <Smartphone className="mt-0.5 size-3.5 shrink-0" />
            <p>
              还有几项只存在手机上，控制台改不到：视频背景、方块样式与圆角、字号、插电亮屏、息屏时间、天气城市。
              它们在「岸亭 → 设置 → 外观 / 电源」里。
            </p>
          </div>
        </TabsContent>

        {/* --------------------------------------------------------- Windows */}
        <TabsContent value="win" className="space-y-4">
          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <MonitorCog className="size-3.5 text-muted-foreground" />
                Windows 探针
              </span>
              <div className="flex items-center gap-2">
                {probe ? (
                  <Badge
                    variant="outline"
                    className={cn(
                      "gap-1.5 rounded-full text-[10.5px]",
                      probe.online
                        ? "border-ok/25 bg-ok/10 text-ok"
                        : "border-warn/30 bg-warn/12 text-warn",
                    )}
                  >
                    <span className={cn("size-1.5 rounded-full", probe.online ? "bg-ok" : "bg-warn")} />
                    {probe.label}
                  </Badge>
                ) : null}
                <Button size="sm" variant="outline" disabled={Boolean(saving) || !form} onClick={saveProbe}>
                  {busy("probe") ? <Loader2 className="animate-spin" /> : <Save />}
                  保存
                </Button>
              </div>
            </div>
            <div className="space-y-4 p-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="探针地址"
                  hint={
                    probeHostMismatch
                      ? `现在填的是 ${probeHost}，和地址簿里的 ${form?.nodes.win.host} 不一致；点右边按钮换过来`
                      : "留空 = 不用探针，本机采样；端口默认 17892"
                  }
                >
                  <div className="flex gap-1.5">
                    <Input
                      value={form?.probe.url ?? ""}
                      onChange={(event) => patchProbe("url", event.target.value)}
                      placeholder="win.lan:17892"
                      spellCheck={false}
                      className="h-8 font-mono text-[12.5px]"
                    />
                    <Button
                      size="icon-sm"
                      variant="outline"
                      aria-label="用域名填探针地址"
                      onClick={() =>
                        patchProbe(
                          "url",
                          `${form?.nodes.win.host || form?.nodes.win.ip}:${form?.nodes.win.port || 17892}`,
                        )
                      }
                    >
                      <RefreshCw />
                    </Button>
                  </div>
                </Field>
                <Field
                  label="探针口令"
                  hint={data?.secrets?.probe ? "已配置。留空 = 保持现有的" : "没配口令，局域网谁都能开程序"}
                >
                  <Input
                    value={form?.probe.token ?? ""}
                    onChange={(event) => patchProbe("token", event.target.value)}
                    type="password"
                    autoComplete="new-password"
                    placeholder={data?.secrets?.probe ? "••••（不修改）" : "可留空"}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Windows 往哪台上报" hint="Windows 上 --probe 的 POST 目标">
                  <Input
                    value={form?.probe.hub_url ?? ""}
                    onChange={(event) => patchProbe("hub_url", event.target.value)}
                    placeholder="mini.lan:17890"
                    spellCheck={false}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
                <Field label="探针监听" hint="Windows 侧监听地址，默认 0.0.0.0:17892">
                  <Input
                    value={form?.probe.listen ?? ""}
                    onChange={(event) => patchProbe("listen", event.target.value)}
                    placeholder="0.0.0.0:17892"
                    spellCheck={false}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
              </div>
              <Field label="多久没上报算掉线（毫秒）" hint="默认 4000；Windows 睡眠唤醒慢可以调大">
                <Input
                  value={form?.probe.stale_ms ?? ""}
                  onChange={(event) => patchProbe("stale_ms", event.target.value.replace(/\D/g, ""))}
                  inputMode="numeric"
                  className="h-8 w-[140px] font-mono text-[12.5px] tabular-nums"
                />
              </Field>
              <div className="rounded-lg border border-dashed border-border px-3 py-2.5 text-[11.5px] leading-relaxed text-muted-foreground">
                Windows 上跑 <code className="font-mono text-foreground">uv run dock-hub --probe</code>
                ，并放行 TCP 17892 入站。填错的表现是安卓主屏第一行一直空着、点快捷方式没反应。
              </div>
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <Cpu className="size-3.5 text-muted-foreground" />
                电脑监控
              </span>
              <div className="flex items-center gap-2">
                <Input
                  value={form?.sampleMs ?? ""}
                  onChange={(event) => patch("sampleMs", event.target.value.replace(/\D/g, ""))}
                  inputMode="numeric"
                  aria-label="采样间隔"
                  className="h-7 w-[86px] font-mono text-[11.5px] tabular-nums"
                />
                <span className="text-[11px] text-muted-foreground">ms</span>
                <Button size="sm" variant="outline" disabled={Boolean(saving)} onClick={() => savePc()}>
                  {busy("pc") ? <Loader2 className="animate-spin" /> : <Save />}
                  保存
                </Button>
              </div>
            </div>
            <div className="divide-y divide-border/60">
              <Row title="在安卓显示 CPU / 内存" description="关掉则 snapshot.pc 为 null，主屏不显示电脑那行。">
                <Switch
                  checked={form?.pcOn ?? false}
                  onCheckedChange={(next) => {
                    patch("pcOn", next);
                    savePc({ pcOn: next });
                  }}
                />
              </Row>
              <Row
                title="读 CPU 温度"
                description="Windows 要装 PawnIO 并以管理员身份跑 Hub；读不到只是没数字。"
              >
                <Switch
                  checked={form?.cpuTemp ?? false}
                  disabled={!form?.pcOn}
                  onCheckedChange={(next) => {
                    patch("cpuTemp", next);
                    savePc({ cpuTemp: next });
                  }}
                />
              </Row>
              <Row title="显示 GPU" description="NVIDIA 显卡才有。">
                <Switch
                  checked={form?.gpu ?? false}
                  disabled={!form?.pcOn}
                  onCheckedChange={(next) => {
                    patch("gpu", next);
                    savePc({ gpu: next });
                  }}
                />
              </Row>
              <Row
                title="媒体控制"
                description="上一首 / 暂停 / 下一首走 Hub 所在机器的系统媒体会话；Hub 在 Mac 上建议关。"
              >
                <Switch
                  checked={form?.mediaOn ?? false}
                  onCheckedChange={(next) => saveMedia(next)}
                />
              </Row>
            </div>
          </Card>

          <Section title="启动项" description="点安卓主屏 logo 时在 Windows 上跑的东西。">
            <Card className="gap-0 py-0">
              <div className="divide-y divide-border/60">
                {(cfg?.devices ?? [])
                  .filter((device) => device.type === "action")
                  .map((device) => (
                    <div key={device.id} className="flex items-center gap-2.5 px-4 py-2.5">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[12.5px]">{device.name || device.id}</div>
                        <div className="truncate font-mono text-[10.5px] text-muted-foreground">
                          {device.run?.program || "没写 program"}
                        </div>
                      </div>
                      <Badge variant="outline" className="text-[10px] text-muted-foreground">
                        {device.icon || device.id}
                      </Badge>
                    </div>
                  ))}
                {!(cfg?.devices ?? []).some((device) => device.type === "action") ? (
                  <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">
                    还没有启动项。在 hub.yaml 的 devices 里加 type: action。
                  </div>
                ) : null}
              </div>
            </Card>
          </Section>
        </TabsContent>

        {/* ------------------------------------------------------------- Mac */}
        <TabsContent value="mac" className="space-y-4">
          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <Laptop className="size-3.5 text-muted-foreground" />
                这台 Mac（Hub 本机）
              </span>
              <Button size="sm" variant="outline" disabled={Boolean(saving) || !form} onClick={saveMac}>
                {busy("mac") ? <Loader2 className="animate-spin" /> : <Save />}
                保存
              </Button>
            </div>
            <div className="space-y-4 p-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Hub 的名字" hint="安卓连上后显示的名字">
                  <Input
                    value={form?.name ?? ""}
                    onChange={(event) => patch("name", event.target.value)}
                    placeholder="study"
                    className="h-8 text-[12.5px]"
                  />
                </Field>
                <Field label="监听地址" hint="0.0.0.0 = 局域网可达">
                  <Input
                    value={form?.host ?? ""}
                    onChange={(event) => patch("host", event.target.value)}
                    placeholder="0.0.0.0"
                    spellCheck={false}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
                <Field label="端口" hint="改完要退出 Hub 再启动">
                  <Input
                    value={form?.port ?? ""}
                    onChange={(event) => patch("port", event.target.value.replace(/\D/g, ""))}
                    inputMode="numeric"
                    className="h-8 font-mono text-[12.5px] tabular-nums"
                  />
                </Field>
              </div>
              <Field label="Token" hint="安卓那一页复制同一枚；重新生成后手机要重填。">
                <div className="flex flex-wrap gap-1.5">
                  <Input
                    value={data?.token_masked ?? ""}
                    readOnly
                    className="h-8 min-w-[180px] flex-1 font-mono text-[12.5px]"
                  />
                  <Button size="sm" variant="outline" onClick={() => void revealToken()}>
                    <KeyRound />
                    复制
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={Boolean(saving)}
                    onClick={() => void regenerateToken()}
                  >
                    {busy("token") ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                    重新生成
                  </Button>
                </div>
              </Field>
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <Sparkles className="size-3.5 text-muted-foreground" />
                伴侣大模型
              </span>
              <Badge
                variant="outline"
                className={cn(
                  "text-[10.5px]",
                  data?.llm.has_key ? "border-ok/40 text-ok" : "text-muted-foreground",
                )}
              >
                {data?.llm.has_key ? `Key 已配置 ${data.llm.key_hint ?? ""}` : "还没配 Key"}
              </Badge>
            </div>
            <div className="space-y-4 p-4">
              <Field label="API 地址（base_url）" hint="任意 OpenAI 兼容服务，带不带 /v1 以服务商要求为准。">
                <Input
                  value={form?.llmBase ?? ""}
                  onChange={(event) => patch("llmBase", event.target.value)}
                  placeholder="https://open.bigmodel.cn/api/paas/v4"
                  className="h-8 font-mono text-[12px]"
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="模型名">
                  <Input
                    value={form?.llmModel ?? ""}
                    onChange={(event) => patch("llmModel", event.target.value)}
                    placeholder="glm-4-flash"
                    className="h-8 font-mono text-[12px]"
                    list="hub-llm-models"
                  />
                  <datalist id="hub-llm-models">
                    {models.map((item) => (
                      <option key={item} value={item} />
                    ))}
                  </datalist>
                </Field>
                <Field label="API Key" hint="留空 = 保持现有的；不回显。">
                  <Input
                    value={form?.llmKey ?? ""}
                    onChange={(event) => patch("llmKey", event.target.value)}
                    type="password"
                    autoComplete="new-password"
                    placeholder="sk-…"
                    className="h-8 font-mono text-[12px]"
                  />
                </Field>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={Boolean(saving)}
                  onClick={() => void testLlm("models")}
                >
                  {busy("models") ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                  拉取模型列表
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={Boolean(saving)}
                  onClick={() => void testLlm("test")}
                >
                  {busy("test") ? <Loader2 className="animate-spin" /> : <Check />}
                  测试连接
                </Button>
                <Button size="sm" disabled={Boolean(saving)} onClick={() => void saveLlm()}>
                  {busy("llm") ? <Loader2 className="animate-spin" /> : <Save />}
                  保存
                </Button>
                {models.length ? (
                  <span className="text-[11px] text-muted-foreground">{models.length} 个可选模型</span>
                ) : null}
              </div>
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <Volume2 className="size-3.5 text-muted-foreground" />
                说话与朗读服务
              </span>
              <Button size="sm" variant="outline" disabled={Boolean(saving)} onClick={() => void saveVoice()}>
                {busy("voice") ? <Loader2 className="animate-spin" /> : <Volume2 />}
                改声音
              </Button>
            </div>
            <div className="space-y-4 p-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="音量（0–100）" hint="改完会立刻调系统输出音量。">
                  <Input
                    value={form?.volume ?? ""}
                    onChange={(event) => patch("volume", event.target.value.replace(/\D/g, ""))}
                    inputMode="numeric"
                    className="h-8 w-[120px] tabular-nums"
                  />
                </Field>
                <Field label="语速（0.5–2.0）">
                  <Input
                    value={form?.speed ?? ""}
                    onChange={(event) => patch("speed", event.target.value)}
                    inputMode="decimal"
                    className="h-8 w-[120px] tabular-nums"
                  />
                </Field>
              </div>
              <div className="grid gap-4 border-t border-border/60 pt-4 sm:grid-cols-2">
                <Field label="朗读服务地址（TTS）" hint="Hub 和 TTS 同机就填 127.0.0.1:18100">
                  <Input
                    value={form?.ttsUrl ?? ""}
                    onChange={(event) => patch("ttsUrl", event.target.value)}
                    placeholder="127.0.0.1:18100"
                    spellCheck={false}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
                <Field
                  label="朗读服务口令"
                  hint={data?.secrets?.tts ? "已配置。留空 = 保持现有的" : "没配口令；非回环监听时必须有"}
                >
                  <Input
                    value={form?.ttsToken ?? ""}
                    onChange={(event) => patch("ttsToken", event.target.value)}
                    type="password"
                    autoComplete="new-password"
                    placeholder={data?.secrets?.tts ? "••••（不修改）" : "可留空"}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border px-3 py-2.5">
                <div className="min-w-0">
                  <div className="text-[12.5px]">把 wav 也交给手机</div>
                  <div className="text-[11px] text-muted-foreground">
                    默认关：声音从这台机器的喇叭出，手机只拿字幕。
                  </div>
                </div>
                <Switch checked={form?.ttsDeliver ?? false} onCheckedChange={(next) => patch("ttsDeliver", next)} />
              </div>
              <div className="flex items-center gap-2">
                <Button size="sm" disabled={Boolean(saving)} onClick={saveCompanion}>
                  {busy("tts") ? <Loader2 className="animate-spin" /> : <Save />}
                  保存朗读设置
                </Button>
                <span className="text-[11px] text-muted-foreground">
                  伴侣总开关（<code className="font-mono">companion.enabled</code>）还在 hub.yaml 里，这一页不动它。
                </span>
              </div>
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <Cpu className="size-3.5 text-muted-foreground" />
                Mini 监控（主屏第二行 CPU）
              </span>
              <Button size="sm" variant="outline" disabled={Boolean(saving) || !form} onClick={saveMini}>
                {busy("mini") ? <Loader2 className="animate-spin" /> : <Save />}
                保存
              </Button>
            </div>
            <div className="divide-y divide-border/60">
              <Row title="让 Mini 上报" description="关掉则安卓主屏不显示 Mac 的 CPU。">
                <Switch checked={form?.miniOn ?? false} onCheckedChange={(next) => patch("miniOn", next)} />
              </Row>
              <div className="grid gap-4 px-4 py-3 sm:grid-cols-2">
                <Field label="地址" hint="Hub 和 Mini 同机就填 127.0.0.1:17891">
                  <div className="flex gap-1.5">
                    <Input
                      value={form?.miniUrl ?? ""}
                      onChange={(event) => patch("miniUrl", event.target.value)}
                      placeholder="127.0.0.1:17891"
                      spellCheck={false}
                      className="h-8 font-mono text-[12.5px]"
                    />
                    <Button
                      size="icon-sm"
                      variant="outline"
                      aria-label="用域名填 Mini 地址"
                      onClick={() => patch("miniUrl", `${form?.nodes.mac.host || "127.0.0.1"}:17891`)}
                    >
                      <RefreshCw />
                    </Button>
                  </div>
                </Field>
                <Field
                  label="口令"
                  hint={data?.secrets?.mini ? "已配置。留空 = 保持现有的" : "install-launchd.sh 装完会打印"}
                >
                  <Input
                    value={form?.miniToken ?? ""}
                    onChange={(event) => patch("miniToken", event.target.value)}
                    type="password"
                    autoComplete="new-password"
                    placeholder={data?.secrets?.mini ? "••••（不修改）" : "可留空"}
                    className="h-8 font-mono text-[12.5px]"
                  />
                </Field>
              </div>
              <div className="grid gap-4 px-4 py-3 sm:grid-cols-2">
                <Field label="电脑多久报一次（毫秒）">
                  <Input
                    value={form?.miniPcMs ?? ""}
                    onChange={(event) => patch("miniPcMs", event.target.value.replace(/\D/g, ""))}
                    inputMode="numeric"
                    className="h-8 w-[140px] font-mono text-[12.5px] tabular-nums"
                  />
                </Field>
                <Field label="米家多久报一次（毫秒）">
                  <Input
                    value={form?.miniMijiaMs ?? ""}
                    onChange={(event) => patch("miniMijiaMs", event.target.value.replace(/\D/g, ""))}
                    inputMode="numeric"
                    className="h-8 w-[140px] font-mono text-[12.5px] tabular-nums"
                  />
                </Field>
              </div>
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <QrCode className="size-3.5 text-muted-foreground" />
                米家账号
              </span>
              <Badge
                variant="outline"
                className={cn(
                  "text-[10.5px]",
                  data?.mijia === "ok" ? "border-ok/40 text-ok" : "border-warn/40 text-warn",
                )}
              >
                {data?.mijia === "ok"
                  ? `已登录 · ${data.mijia_devices.length} 台设备`
                  : (data?.mijia_message ?? "未登录")}
              </Badge>
            </div>
            <div className="space-y-4 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant="outline" disabled={Boolean(saving)} onClick={() => void startLogin()}>
                  {busy("mijia") ? <Loader2 className="animate-spin" /> : <QrCode />}
                  扫码登录米家
                </Button>
                <Button size="sm" variant="ghost" onClick={() => reload()}>
                  <RefreshCw />
                  刷新设备列表
                </Button>
                {login && login.phase !== "idle" ? (
                  <span className="text-[11.5px] text-muted-foreground">
                    {login.phase === "starting"
                      ? "正在获取二维码…"
                      : login.phase === "qr"
                        ? "二维码已出，用米家 App 扫"
                        : login.phase === "done"
                          ? "登录成功"
                          : (login.message ?? "登录失败")}
                  </span>
                ) : null}
              </div>

              {login?.phase === "qr" && login.qr_url ? (
                <div className="flex flex-col items-center gap-2 rounded-lg border border-border bg-muted/40 py-5">
                  <img src={login.qr_url} alt="米家登录二维码" className="size-[168px] rounded-md bg-white p-2" />
                  <span className="text-[11px] text-muted-foreground">用米家 App 扫一下</span>
                </div>
              ) : null}

              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Input
                    value={mijiaFilter}
                    onChange={(event) => setMijiaFilter(event.target.value)}
                    placeholder="搜设备名"
                    className="h-8 max-w-[220px] text-[12px]"
                  />
                  <span className="text-[11px] text-muted-foreground tabular-nums">
                    {mijiaDevices.length} / {data?.mijia_devices.length ?? 0}
                  </span>
                </div>
                <div className="scroll-thin max-h-[300px] divide-y divide-border/60 overflow-y-auto rounded-lg border">
                  {mijiaDevices.map((device) => (
                    <div key={`${device.name}-${device.model}`} className="flex items-center gap-2 px-3 py-2">
                      <span className="min-w-0 flex-1 truncate text-[12.5px]">{device.name || "未命名"}</span>
                      <span className="hidden max-w-[180px] shrink-0 truncate font-mono text-[10.5px] text-muted-foreground sm:block">
                        {device.model}
                      </span>
                      <span
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          device.online === false ? "bg-muted-foreground/40" : "bg-ok",
                        )}
                      />
                    </div>
                  ))}
                  {!mijiaDevices.length ? (
                    <div className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                      {data?.mijia_devices_error || "还没有设备。先扫码登录。"}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="border-b border-border/60 px-4 py-2.5 text-[12.5px] font-medium">这台机器</div>
            <div className="space-y-1.5 px-4 py-3 text-[11.5px] text-muted-foreground">
              <div>
                配置文件：<code className="font-mono">{data?.config_path}</code>
              </div>
              <div>
                监听：<code className="font-mono">{form?.host}:{form?.port}</code> · 局域网地址{" "}
                <code className="font-mono">{data?.lan_ips?.join("、") || "—"}</code>
              </div>
              <div>
                域名：<code className="font-mono">{form?.nodes.mac.host || "—"}</code>
                {form?.nodes.mac.ip ? ` → ${form.nodes.mac.ip}` : ""}
              </div>
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

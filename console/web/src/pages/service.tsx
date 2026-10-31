import { useState } from "react";
import { Check, ChevronRight, Copy, RefreshCw, ServerCrash } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, MetaRow, PageHeader } from "@/components/page";
import { ServiceActions } from "@/components/service-actions";
import { StateBadge } from "@/components/service-state";
import { useConsole } from "@/lib/console-store";
import { hrefOf, navigate, type Route, type ServiceTab } from "@/lib/route";
import { cn } from "@/lib/utils";

const TABS: { id: ServiceTab; label: string }[] = [
  { id: "status", label: "状态" },
  { id: "logs", label: "日志" },
  { id: "api", label: "接口" },
];

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11.5px]">{children}</code>
  );
}

function CopyButton({ text, label = "复制" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1600);
        });
      }}
    >
      {copied ? <Check /> : <Copy />}
      {copied ? "已复制" : label}
    </Button>
  );
}

const ENDPOINTS: { method: string; path: string; note: string }[] = [
  { method: "GET", path: "/api/status", note: "三个服务的状态、pid、健康检查和日志尾巴" },
  { method: "POST", path: "/api/services/{id}/{start|stop|restart}", note: "单个服务，id 是 hub / mini / tts" },
  { method: "POST", path: "/api/all/{start|stop|restart}", note: "三个一起，顺序按依赖排" },
  { method: "POST", path: "/api/config/windows", note: 'body {"address": "10.83.22.31"}，会顺手重启 Hub' },
  { method: "GET", path: "/api/features", note: "功能开关清单，控制台只是转发给 Hub" },
  { method: "POST", path: "/api/features", note: 'body {"key": "proactive.enabled", "value": true}' },
];

export function ServicePage({ id, tab }: { id: string; tab: ServiceTab }) {
  const { services, refresh, updatedAt } = useConsole();
  const service = services.find((item) => item.id === id);

  if (!service) {
    return (
      <div className="space-y-5">
        <PageHeader title="没有这个服务" subtitle={`控制台里没有 id 为 ${id} 的服务。`} />
        <EmptyState
          icon={<ServerCrash className="size-6" />}
          title="只剩三个"
          description="Hub、Mini 监控、守岸人语音。左边点一个就行。"
          action={
            <Button size="sm" variant="outline" onClick={() => navigate({ kind: "overview" })}>
              回概览
            </Button>
          }
        />
      </div>
    );
  }

  const go = (next: ServiceTab) => {
    const route: Route = { kind: "service", id, tab: next };
    navigate(route);
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title={service.name}
        subtitle={service.blurb}
        actions={
          <>
            <StateBadge label={service.state_label} className="mr-1 h-8 px-3" />
            <ServiceActions service={service} compact />
          </>
        }
      />

      <Tabs value={tab} onValueChange={(value) => go(value as ServiceTab)}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <TabsList>
            {TABS.map((item) => (
              <TabsTrigger key={item.id} value={item.id} className="text-[12.5px]">
                {item.label}
              </TabsTrigger>
            ))}
          </TabsList>
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {updatedAt
              ? `状态每 2.5 秒刷新 · ${new Date(updatedAt).toLocaleTimeString("zh-CN", { hour12: false })}`
              : "正在读取…"}
          </span>
        </div>

        {tab === "status" ? (
          <div className="grid gap-4 pt-4 lg:grid-cols-5">
            <Card className="gap-0 py-0 lg:col-span-3">
              <CardContent className="px-4 py-2">
                <dl>
                  <MetaRow label="LaunchAgent">
                    <Code>{service.label}</Code>
                  </MetaRow>
                  <MetaRow label="域">{service.domain ?? "还没装载"}</MetaRow>
                  <MetaRow label="端口">
                    <Code>{service.port}</Code>
                  </MetaRow>
                  <MetaRow label="PID">{service.pid ?? "—"}</MetaRow>
                  <MetaRow label="进程状态">{service.state ?? "—"}</MetaRow>
                  <MetaRow label="登录项">
                    {service.installed ? "已安装" : "没装，跑一次下面的脚本"}
                  </MetaRow>
                  <MetaRow label="健康检查">
                    <span className="font-mono text-[11.5px] text-muted-foreground">
                      GET http://127.0.0.1:{service.port}/health
                    </span>
                  </MetaRow>
                </dl>
              </CardContent>
            </Card>

            <div className="space-y-4 lg:col-span-2">
              <Card className="gap-0 py-0">
                <CardContent className="space-y-2 px-4 py-4">
                  <div className="text-[12px] font-medium">Hub 的回复</div>
                  <p
                    className={cn(
                      "text-[12.5px] leading-relaxed break-words",
                      service.health_ok ? "text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {service.health_summary || "健康检查没有返回内容。"}
                  </p>
                  {!service.health_ok && service.health_error ? (
                    <p className="rounded-md bg-destructive/8 px-2.5 py-2 font-mono text-[11px] break-words text-destructive">
                      {service.health_error}
                    </p>
                  ) : null}
                  {!service.installed ? (
                    <div className="rounded-md bg-warn/10 px-2.5 py-2 text-[11.5px] text-warn">
                      登录项还没装：在仓库里跑 <Code>./{service.install}</Code>
                    </div>
                  ) : null}
                  <Separator className="my-1" />
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={() => void refresh()}>
                      <RefreshCw />
                      刷新
                    </Button>
                    <CopyButton text={`http://127.0.0.1:${service.port}`} label="复制地址" />
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        ) : null}

        {tab === "logs" ? (
          <Card className="mt-4 gap-0 py-0">
            <div className="flex items-center justify-between gap-2 border-b border-border/70 px-4 py-2.5">
              <div className="text-[11.5px] text-muted-foreground">
                末尾 24 行 · <span className="font-mono">{service.log_file}</span>
              </div>
              <div className="flex gap-1.5">
                <CopyButton text={service.log_tail} />
                <Button size="sm" variant="outline" onClick={() => void refresh()}>
                  <RefreshCw />
                  刷新
                </Button>
              </div>
            </div>
            <pre className="scroll-thin max-h-[56vh] min-h-[240px] overflow-auto px-4 py-3 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
              {service.log_tail || "还没有日志。服务起来一次之后，这里会有东西。"}
            </pre>
          </Card>
        ) : null}

        {tab === "api" ? (
          <Card className="mt-4 gap-0 py-0">
            <div className="border-b border-border/70 px-4 py-3 text-[11.5px] text-muted-foreground">
              控制台自己的接口，都在 <Code>{`http://127.0.0.1:${window.location.port || 17880}`}</Code>{" "}
              上，没有鉴权，只监听本机。
            </div>
            <div className="divide-y divide-border/70">
              {ENDPOINTS.map((endpoint) => (
                <div
                  key={endpoint.path}
                  className="flex flex-col gap-1 px-4 py-2.5 sm:flex-row sm:items-center sm:gap-3"
                >
                  <span
                    className={cn(
                      "w-14 shrink-0 rounded px-1.5 py-0.5 text-center font-mono text-[10.5px]",
                      endpoint.method === "GET"
                        ? "bg-muted text-muted-foreground"
                        : "bg-primary/10 text-primary",
                    )}
                  >
                    {endpoint.method}
                  </span>
                  <code className="font-mono text-[12px]">{endpoint.path}</code>
                  <span className="text-[11.5px] text-muted-foreground sm:ml-auto sm:text-right">
                    {endpoint.note}
                  </span>
                </div>
              ))}
            </div>
            <div className="flex items-center gap-1.5 px-4 py-3 text-[11.5px] text-muted-foreground">
              要脚本化就用这些，别去抓页面。
              <ChevronRight className="size-3" />
              <a
                className="underline decoration-dotted underline-offset-2 hover:text-foreground"
                href={hrefOf({ kind: "about" })}
              >
                关于里还有端口表
              </a>
            </div>
          </Card>
        ) : null}
      </Tabs>
    </div>
  );
}

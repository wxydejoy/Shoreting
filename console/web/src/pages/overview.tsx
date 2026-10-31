import {
  ChevronRight,
  Cpu,
  Network,
  Server,
  Terminal,
  Volume2,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader, Section } from "@/components/page";
import { PowerActions, ServiceActions } from "@/components/service-actions";
import { ServiceDot, StateBadge } from "@/components/service-state";
import { type Service } from "@/lib/api";
import { useConsole } from "@/lib/console-store";
import { HUB_PAGES, hrefOf } from "@/lib/route";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  hub: Server,
  mini: Cpu,
  tts: Volume2,
};

const HUB_ICONS: Record<string, LucideIcon> = {
  setup: Network,
  chats: Terminal,
  read: Volume2,
  week: Server,
  diary: Cpu,
};

function Stat({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string | number;
  mono?: boolean;
  tone?: "ok" | "warn";
}) {
  return (
    <div className="rounded-lg bg-muted/50 px-2 py-1.5">
      <div className="text-[10.5px] tracking-wide text-muted-foreground uppercase">{label}</div>
      <div
        className={cn(
          "mt-0.5 truncate text-[12.5px] font-medium",
          mono && "font-mono text-[12px]",
          tone === "ok" && "text-ok",
          tone === "warn" && "text-warn",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function ServiceTile({ service }: { service: Service }) {
  const Icon = ICONS[service.id] ?? Server;
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="px-4 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Icon className="size-3.5" />
            </span>
            <CardTitle className="truncate text-[14px]">{service.name}</CardTitle>
          </div>
          <StateBadge label={service.state_label} />
        </div>
        <CardDescription className="text-[12px] leading-snug">{service.blurb}</CardDescription>
      </CardHeader>

      <CardContent className="px-4 pt-3">
        <div className="grid grid-cols-3 gap-2">
          <Stat label="端口" value={service.port} mono />
          <Stat label="进程" value={service.pid ?? "—"} mono />
          <Stat
            label="健康"
            value={service.health_ok ? "正常" : "没应答"}
            tone={service.health_ok ? "ok" : "warn"}
          />
        </div>
        <p className="mt-2 h-4 truncate text-[11.5px] text-muted-foreground">
          {service.health_summary || service.health_error || service.label}
        </p>
      </CardContent>

      <CardFooter className="flex-wrap gap-1.5 px-4 pb-4">
        <ServiceActions service={service} compact />
        <Button asChild size="sm" variant="ghost" className="ml-auto text-muted-foreground">
          <a href={hrefOf({ kind: "service", id: service.id, tab: "status" })}>
            详情
            <ChevronRight />
          </a>
        </Button>
      </CardFooter>
    </Card>
  );
}

function WindowsCard({ service }: { service?: Service }) {
  const probe = service?.windows;

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="px-4 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex size-7 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Network className="size-3.5" />
            </span>
            <CardTitle className="text-[14px]">Windows 探针</CardTitle>
          </div>
          {probe ? (
            <Badge
              variant="outline"
              className={cn(
                "gap-1.5 rounded-full",
                probe.online
                  ? "border-ok/25 bg-ok/10 text-ok"
                  : "border-warn/30 bg-warn/12 text-warn",
              )}
            >
              <span className={cn("size-1.5 rounded-full", probe.online ? "bg-ok" : "bg-warn")} />
              {probe.label}
            </Badge>
          ) : null}
        </div>
        <CardDescription className="text-[12px] leading-snug">
          Hub 用这个地址开 Windows 上的程序、看它在不在线。
        </CardDescription>
      </CardHeader>
      <CardContent className="px-4 pt-3">
        <div className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5">
          <span className="min-w-0 flex-1 truncate font-mono text-[12px]">
            {probe?.address || "还没填"}
          </span>
          <Badge variant="outline" className="shrink-0 text-[10px] text-muted-foreground">
            {probe?.online ? "在线" : "没响应"}
          </Badge>
        </div>
      </CardContent>
      <CardFooter className="px-4 pb-4">
        <Button asChild size="sm" variant="ghost" className="text-muted-foreground">
          <a href={hrefOf({ kind: "hub", page: "setup", tab: "win" })}>
            去三端配置里改
            <ChevronRight />
          </a>
        </Button>
      </CardFooter>
    </Card>
  );
}

export function OverviewPage() {
  const { services, error } = useConsole();
  const hub = services.find((service) => service.id === "hub");

  return (
    <div className="space-y-6">
      <PageHeader
        title="概览"
        subtitle="这台 Mac 上的三个常驻服务。关掉只是这次登录不再运行，下次登录仍会开机启动。"
        actions={<PowerActions />}
      />

      {error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/8 px-3.5 py-2.5 text-[12.5px] text-destructive">
          控制台自己出了点问题：{error}
        </div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {services.map((service) => (
          <ServiceTile key={service.id} service={service} />
        ))}
        {services.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-[12.5px] text-muted-foreground md:col-span-2 xl:col-span-3">
            还没读到服务列表，稍等一下或者点右上角刷新。
          </div>
        ) : null}
      </div>

      <div className="grid gap-5 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Section
            title="陪伴"
            description="聊天、朗读、周报与日记——都在这一层导航里，不用另开标签。"
          >
            <div className="overflow-hidden rounded-xl border bg-card">
              {HUB_PAGES.filter((page) => page.id !== "setup").map((page, index) => {
                const Icon = HUB_ICONS[page.id] ?? Terminal;
                return (
                  <a
                    key={page.id}
                    href={hrefOf({ kind: "hub", page: page.id })}
                    className={cn(
                      "flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/60",
                      index > 0 && "border-t border-border/70",
                    )}
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                      <Icon className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium">{page.title}</span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {page.desc}
                      </span>
                    </span>
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground/60" />
                  </a>
                );
              })}
            </div>
          </Section>
        </div>

        <div className="space-y-4 lg:col-span-2">
          <WindowsCard service={hub} />

          <Card className="gap-0 py-0">
            <CardHeader className="px-4 pt-4">
              <div className="flex items-center justify-between">
                <CardTitle className="text-[14px]">Hub 最近日志</CardTitle>
                <Button
                  asChild
                  size="xs"
                  variant="ghost"
                  className="text-muted-foreground"
                >
                  <a href={hrefOf({ kind: "service", id: "hub", tab: "logs" })}>
                    全部
                    <ChevronRight />
                  </a>
                </Button>
              </div>
            </CardHeader>
            <CardContent className="px-4 pt-3 pb-4">
              <pre className="scroll-thin h-[136px] overflow-auto rounded-lg bg-muted/60 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
                {hub?.log_tail?.split("\n").slice(-8).join("\n") || "还没有日志。"}
              </pre>
            </CardContent>
          </Card>

          <div className="flex items-center gap-2 rounded-lg border bg-card px-3.5 py-2.5 text-[11.5px] text-muted-foreground">
            <ServiceDot service={hub ?? ({ state_label: "已关闭" } as Service)} />
            关掉页面不会关掉任何服务，控制台只是个遥控器。
          </div>
        </div>
      </div>
    </div>
  );
}

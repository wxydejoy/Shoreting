import { useTheme } from "next-themes";
import { BookOpen, ExternalLink, Monitor, Moon, ShieldAlert, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { MetaRow, PageHeader, Section } from "@/components/page";
import { useConsole } from "@/lib/console-store";
import { cn } from "@/lib/utils";

const PORTS: { port: number; name: string; note: string }[] = [
  { port: 17880, name: "控制台", note: "就是这一页，只监听 127.0.0.1" },
  { port: 17890, name: "Hub", note: "手机连它；带 Bearer token" },
  { port: 17891, name: "Mini 监控", note: "Mac Mini 的 CPU / 内存 / GPU" },
  { port: 17892, name: "Windows 探针", note: "在 Windows 那台上，不是 Hub" },
  { port: 18100, name: "守岸人 TTS", note: "Mini 的嘴，--play 才出声" },
  { port: 17920, name: "背景页", note: "浏览区里传图片 / 视频背景" },
];

const DOCS: { title: string; path: string }[] = [
  { title: "接法说明", path: "docs/companion.md" },
  { title: "局域网协议", path: "docs/lan-protocol.md" },
  { title: "OpenAPI", path: "docs/openapi.yaml" },
  { title: "Hub 说明", path: "hub/README.md" },
  { title: "控制台说明", path: "console/README.md" },
];

function themeLabel(theme: string | undefined, resolved: string | undefined) {
  if (!theme) return "跟随系统";
  if (theme === "system") return `跟随系统（现在${resolved === "dark" ? "暗" : "亮"}）`;
  return theme === "dark" ? "暗色" : "亮色";
}

export function AboutPage() {
  const { services } = useConsole();
  const { theme, setTheme, resolvedTheme } = useTheme();

  const options = [
    { value: "light", label: "亮色", icon: Sun },
    { value: "dark", label: "暗色", icon: Moon },
    { value: "system", label: "跟随系统", icon: Monitor },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="关于与帮助"
        subtitle="这个页面只管这台 Mac 上的服务。手机、Hub 的配置都在别处，下面有地图。"
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="gap-0 py-0">
          <CardContent className="px-4 py-2">
            <dl>
              <MetaRow label="控制台">岸亭控制台 · 纯标准库 Python + 预构建的前端</MetaRow>
              <MetaRow label="监听">http://127.0.0.1:17880</MetaRow>
              <MetaRow label="鉴权">没有。别暴露到局域网</MetaRow>
              <MetaRow label="服务端代码">console/dock_console/</MetaRow>
              <MetaRow label="前端源码">console/web/（构建产物在 assets）</MetaRow>
              <MetaRow label="仓库">/Users/weiekko/dock</MetaRow>
            </dl>
          </CardContent>
        </Card>

        <Card className="gap-0 py-0">
          <CardContent className="space-y-3 px-4 py-4">
            <div className="text-[13px] font-medium">外观</div>
            <div className="flex flex-wrap gap-1.5">
              {options.map((option) => (
                <Button
                  key={option.value}
                  size="sm"
                  variant={theme === option.value ? "default" : "outline"}
                  onClick={() => setTheme(option.value)}
                >
                  <option.icon />
                  {option.label}
                </Button>
              ))}
            </div>
            <p className="text-[11.5px] text-muted-foreground">现在是{themeLabel(theme, resolvedTheme)}。</p>
            <Separator />
            <div className="flex items-start gap-2 text-[11.5px] leading-relaxed text-muted-foreground">
              <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-warn" />
              <p>
                控制台能启停 launchd 里的服务，等于本机的一个开关面板。它只绑 127.0.0.1，
                不要把 17880 转发出去。
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Section title="端口" description="记不住的话看这里。手机只该认识 17890。">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {PORTS.map((item) => (
            <div key={item.port} className="rounded-lg border bg-card px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[13px] font-medium">{item.port}</span>
                <span className="text-[12.5px]">{item.name}</span>
              </div>
              <div className="mt-0.5 text-[11px] text-muted-foreground">{item.note}</div>
            </div>
          ))}
        </div>
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="三个常驻服务">
          <Card className="gap-0 divide-y divide-border/70 py-0">
            {services.map((service) => (
              <div key={service.id} className="flex items-center gap-3 px-4 py-2.5">
                <span
                  className={cn(
                    "size-1.5 shrink-0 rounded-full",
                    service.state_label === "运行中" ? "bg-ok" : "bg-idle",
                  )}
                />
                <span className="w-20 shrink-0 text-[12.5px] font-medium">{service.name}</span>
                <span className="truncate font-mono text-[11px] text-muted-foreground">
                  {service.label}
                </span>
                <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                  :{service.port}
                </span>
              </div>
            ))}
          </Card>
        </Section>

        <Section title="文档" description="改接口之前先改文档——两边的契约都在这些文件里。">
          <Card className="gap-0 divide-y divide-border/70 py-0">
            {DOCS.map((doc) => (
              <div key={doc.path} className="flex items-center gap-2 px-4 py-2.5">
                <BookOpen className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="text-[12.5px]">{doc.title}</span>
                <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                  {doc.path}
                </span>
              </div>
            ))}
          </Card>
        </Section>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-4 py-3 text-[11.5px] text-muted-foreground">
        <span>改完控制台的前端要重新构建：</span>
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
          cd console/web && npm run build
        </code>
        <Button
          asChild
          size="xs"
          variant="ghost"
          className="ml-auto text-muted-foreground"
        >
          <a href="https://ui.shadcn.com/docs/components/button" target="_blank" rel="noreferrer">
            shadcn/ui
            <ExternalLink />
          </a>
        </Button>
      </div>
    </div>
  );
}

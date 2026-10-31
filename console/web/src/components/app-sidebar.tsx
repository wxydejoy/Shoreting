import { useState } from "react";
import {
  Activity,
  BookOpen,
  CalendarDays,
  ChevronRight,
  Clock,
  Cpu,
  History,
  Info,
  LayoutDashboard,
  Lightbulb,
  MessageCircle,
  MessagesSquare,
  Network,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Smile,
  Volume2,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ServiceDot } from "@/components/service-state";
import { useConsole } from "@/lib/console-store";
import { cn } from "@/lib/utils";
import { HUB_PAGES, hrefOf, navigate, type Route } from "@/lib/route";

const SERVICE_ICONS: Record<string, LucideIcon> = {
  hub: Server,
  mini: Cpu,
  tts: Volume2,
};

// 功能开关的六组各给一个图标，不然六个滑块排在一起看不出是不同东西。
const FEATURE_ICONS: Record<string, LucideIcon> = {
  talk: MessageCircle,
  routine: Clock,
  home: Lightbulb,
  memory: History,
  face: Smile,
  safety: ShieldCheck,
};

const HUB_ICONS: Record<string, LucideIcon> = {
  setup: Wrench,
  chats: MessagesSquare,
  read: BookOpen,
  journal: CalendarDays,
};

type Item = {
  key: string;
  label: string;
  to: Route;
  icon: LucideIcon;
  aside?: React.ReactNode;
};

type Section = {
  id: string;
  label: string;
  items: Item[];
  hint?: string;
};

function isActive(route: Route, item: Route): boolean {
  if (route.kind !== item.kind) return false;
  if (route.kind === "service" && item.kind === "service") return route.id === item.id;
  if (route.kind === "features" && item.kind === "features") {
    return route.group === item.group;
  }
  if (route.kind === "hub" && item.kind === "hub") return route.page === item.page;
  return true;
}

export function AppSidebar({ route }: { route: Route }) {
  const { services, catalog, catalogError } = useConsole();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem("dock-console:nav") ?? "{}") as Record<
        string,
        boolean
      >;
    } catch {
      return {};
    }
  });

  const toggle = (id: string) => {
    setCollapsed((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      localStorage.setItem("dock-console:nav", JSON.stringify(next));
      return next;
    });
  };

  const groups = catalog?.groups ?? [];
  const running = services.filter((service) => service.state_label === "运行中").length;
  const setupPage = HUB_PAGES.find((page) => page.id === "setup");

  const sections: Section[] = [
    {
      id: "overview",
      label: "总览",
      items: [{ key: "overview", label: "概览", to: { kind: "overview" }, icon: LayoutDashboard }],
    },
    {
      id: "config",
      label: "配置",
      items: setupPage
        ? [
            {
              key: "hub-setup",
              label: setupPage.title,
              to: { kind: "hub", page: "setup" } as Route,
              icon: Wrench,
            },
          ]
        : [],
    },
    {
      id: "services",
      label: "服务",
      items: services.map((service) => ({
        key: `svc-${service.id}`,
        label: service.name,
        to: { kind: "service", id: service.id, tab: "status" } as Route,
        icon: SERVICE_ICONS[service.id] ?? Activity,
        aside: <ServiceDot service={service} />,
      })),
    },
    {
      id: "features",
      label: "功能开关",
      hint: groups.length ? undefined : (catalogError ?? "Hub 没在跑"),
      items: groups.map((group) => ({
        key: `feat-${group.id}`,
        label: group.title,
        to: { kind: "features", group: group.id } as Route,
        icon: FEATURE_ICONS[group.id] ?? SlidersHorizontal,
      })),
    },
    {
      id: "hub",
      label: "陪伴",
      items: HUB_PAGES.filter((page) => page.id !== "setup").map((page) => ({
        key: `hub-${page.id}`,
        label: page.title,
        to: { kind: "hub", page: page.id } as Route,
        icon: HUB_ICONS[page.id] ?? Network,
      })),
    },
    {
      id: "system",
      label: "系统",
      items: [{ key: "about", label: "关于与帮助", to: { kind: "about" }, icon: Info }],
    },
  ];

  return (
    <div className="flex h-full flex-col bg-sidebar">
      <div className="flex h-14 items-center gap-2.5 border-b border-sidebar-border px-4">
        <span className="relative flex size-7 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <span className="text-[13px] font-semibold">岸</span>
        </span>
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[13px] font-semibold tracking-tight">岸亭控制台</div>
          <div className="truncate text-[11px] text-muted-foreground">
            {services.length ? `${running}/${services.length} 个服务在跑` : "正在连接…"}
          </div>
        </div>
      </div>

      <nav className="scroll-thin flex-1 overflow-y-auto px-2 py-3">
        {sections.map((section) => {
          const open = !collapsed[section.id];
          const single = section.items.length <= 1 && section.id === "overview";
          return (
            <div key={section.id} className="mb-1">
              {single ? null : (
                <button
                  type="button"
                  onClick={() => toggle(section.id)}
                  className="group flex h-7 w-full items-center gap-1 rounded-md px-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase transition-colors hover:text-foreground"
                >
                  <ChevronRight
                    className={cn(
                      "size-3 transition-transform",
                      open ? "rotate-90" : "rotate-0",
                    )}
                  />
                  <span className="flex-1 text-left">{section.label}</span>
                  {section.hint ? (
                    <span className="text-[10px] tracking-normal normal-case opacity-70">
                      {section.hint}
                    </span>
                  ) : null}
                </button>
              )}

              {open || single ? (
                <ul className="space-y-0.5">
                  {section.items.map((item) => {
                    const active = isActive(route, item.to);
                    return (
                      <li key={item.key}>
                        <a
                          href={hrefOf(item.to)}
                          className={cn(
                            "flex h-8 items-center gap-2 rounded-md px-2 text-[13px] transition-colors",
                            active
                              ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                              : "text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                          )}
                        >
                          <item.icon
                            className={cn(
                              "size-3.5 shrink-0",
                              active ? "text-primary" : "text-muted-foreground",
                            )}
                          />
                          <span className="flex-1 truncate">{item.label}</span>
                          {item.aside}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          );
        })}
      </nav>

      <div className="border-t border-sidebar-border px-3 py-2.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => navigate({ kind: "about" })}
              className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[11px] text-muted-foreground transition-colors hover:text-foreground"
            >
              <span className="size-1.5 rounded-full bg-ok" />
              只监听 127.0.0.1 · 无鉴权
            </button>
          </TooltipTrigger>
          <TooltipContent side="top">不要暴露到局域网。点开看端口和文档。</TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}

import { useState } from "react";
import { motion } from "motion/react";
import {
  BookOpen,
  CalendarDays,
  ChevronRight,
  House,
  LayoutGrid,
  MessagesSquare,
  Server,
  SlidersHorizontal,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useConsole } from "@/lib/console-store";
import { cn } from "@/lib/utils";
import { hrefOf, navigate, type Route } from "@/lib/route";

type Item = {
  key: string;
  label: string;
  to: Route;
  icon: LucideIcon;
};

type Section = {
  id: string;
  label: string;
  items: Item[];
};

function isActive(route: Route, item: Route): boolean {
  if (item.kind === "services") return route.kind === "services" || route.kind === "service";
  if (item.kind === "features") return route.kind === "features";
  if (route.kind !== item.kind) return false;
  if (route.kind === "hub" && item.kind === "hub") return route.page === item.page;
  return true;
}

export function AppSidebar({ route }: { route: Route }) {
  const { services } = useConsole();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem("dock-console:nav") ?? "{}") as Record<string, boolean>;
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

  const running = services.filter((service) => service.state_label === "运行中").length;

  const sections: Section[] = [
    {
      id: "today",
      label: "今天",
      items: [{ key: "today", label: "今天", to: { kind: "overview" }, icon: House }],
    },
    {
      id: "days",
      label: "日子",
      items: [
        { key: "chats", label: "聊天", to: { kind: "hub", page: "chats" }, icon: MessagesSquare },
        { key: "read", label: "朗读", to: { kind: "hub", page: "read" }, icon: BookOpen },
        { key: "journal", label: "周报", to: { kind: "hub", page: "journal" }, icon: CalendarDays },
      ],
    },
    {
      id: "machine",
      label: "机器",
      items: [
        { key: "services", label: "服务", to: { kind: "services" }, icon: Server },
        { key: "setup", label: "配置", to: { kind: "hub", page: "setup" }, icon: Wrench },
        { key: "features", label: "功能开关", to: { kind: "features", group: "" }, icon: SlidersHorizontal },
        { key: "layout", label: "主屏布局", to: { kind: "hub", page: "layout" }, icon: LayoutGrid },
      ],
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
          const bare = section.id === "today";
          return (
            <div key={section.id} className="mb-1">
              {bare ? null : (
                <button
                  type="button"
                  onClick={() => toggle(section.id)}
                  className="group flex h-7 w-full items-center gap-1 rounded-md px-2 text-[11px] font-medium tracking-wide text-muted-foreground transition-colors hover:text-foreground"
                >
                  <ChevronRight
                    className={cn("size-3 transition-transform", open ? "rotate-90" : "rotate-0")}
                  />
                  <span className="flex-1 text-left">{section.label}</span>
                </button>
              )}

              {open || bare ? (
                <ul className="space-y-0.5">
                  {section.items.map((item) => {
                    const active = isActive(route, item.to);
                    return (
                      <li key={item.key}>
                        <a
                          href={hrefOf(item.to)}
                          className={cn(
                            "relative flex h-8 items-center gap-2 rounded-md px-2 text-[13px]",
                            active
                              ? "font-medium text-sidebar-accent-foreground"
                              : "text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                          )}
                        >
                          {active ? (
                            <motion.span
                              layoutId="dock-nav"
                              className="absolute inset-0 rounded-md bg-sidebar-accent"
                              transition={{ type: "spring", bounce: 0.16, duration: 0.38 }}
                            />
                          ) : null}
                          <item.icon
                            className={cn(
                              "relative size-3.5 shrink-0",
                              active ? "text-primary" : "text-muted-foreground",
                            )}
                          />
                          <span className="relative flex-1 truncate">{item.label}</span>
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

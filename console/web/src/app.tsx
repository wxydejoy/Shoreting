import { useEffect, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { ChevronRight, Menu, Monitor, Moon, RefreshCw, Sun, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { AppSidebar } from "@/components/app-sidebar";
import { EmptyState, PageHeader } from "@/components/page";
import { OverviewPage } from "@/pages/overview";
import { ServicePage } from "@/pages/service";
import { FeaturesPage } from "@/pages/features";
import { SetupPage } from "@/pages/hub-setup";
import { ChatsPage } from "@/pages/hub-chats";
import { ReadPage } from "@/pages/hub-read";
import { JournalPage } from "@/pages/hub-journal";
import { AboutPage } from "@/pages/about";
import { useConsole } from "@/lib/console-store";
import { HUB_PAGES, pathOf, type Route, useRoute } from "@/lib/route";
import { cn } from "@/lib/utils";

function crumbs(
  route: Route,
  services: { id: string; name: string }[],
  groups: { id: string; title: string }[],
): string[] {
  switch (route.kind) {
    case "service":
      return ["服务", services.find((item) => item.id === route.id)?.name ?? route.id];
    case "features": {
      const group = groups.find((item) => item.id === route.group) ?? groups[0];
      return group ? ["功能开关", group.title] : ["功能开关"];
    }
    case "hub":
      return ["陪伴", HUB_PAGES.find((page) => page.id === route.page)?.title ?? route.page];
    case "about":
      return ["系统", "关于与帮助"];
    default:
      return ["概览"];
  }
}

function UnknownHubPage({ page }: { page: string }) {
  return (
    <div className="space-y-5">
      <PageHeader title="没有这一页" subtitle={`控制台里没有 ${page} 这个页面。`} />
      <EmptyState
        icon={<TriangleAlert className="size-6" />}
        title="换一个入口"
        description={HUB_PAGES.map((item) => item.title).join(" / ")}
      />
    </div>
  );
}

function ThemeToggle() {
  const { theme, setTheme, resolvedTheme } = useTheme();
  const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Icon = theme === "system" ? Monitor : resolvedTheme === "dark" ? Moon : Sun;
  const label = theme === "system" ? "跟随系统" : theme === "dark" ? "暗色" : "亮色";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          size="icon-sm"
          variant="ghost"
          className="text-muted-foreground"
          onClick={() => setTheme(next)}
          aria-label="切换外观"
        >
          <Icon />
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">外观：{label}，点一下换下一个</TooltipContent>
    </Tooltip>
  );
}

function MobileNav({ route }: { route: Route }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="icon-sm" variant="ghost" className="lg:hidden" aria-label="导航">
          <Menu />
        </Button>
      </DialogTrigger>
      <DialogContent className="left-0 h-dvh max-w-[260px] translate-x-0 translate-y-0 gap-0 rounded-none border-y-0 border-l-0 p-0 sm:max-w-[260px]">
        <DialogTitle className="sr-only">导航</DialogTitle>
        <div className="h-full" onClick={() => setOpen(false)}>
          <AppSidebar route={route} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function App() {
  const route = useRoute();
  const { updatedAt, refresh, error, services, catalog } = useConsole();
  const trail = crumbs(route, services, catalog?.groups ?? []);
  const mainRef = useRef<HTMLElement>(null);
  const where = pathOf(route);

  // 换页面就回到顶部：不然从长列表跳到短页面会停在半空
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [where]);

  return (
    <div className="flex h-dvh overflow-hidden">
      <aside className="hidden w-[248px] shrink-0 border-r border-sidebar-border lg:block">
        <AppSidebar route={route} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-background/85 px-4 backdrop-blur lg:px-5">
          <div className="flex min-w-0 items-center gap-1 text-[12px] text-muted-foreground">
            <span className="hidden shrink-0 sm:inline">岸亭控制台</span>
            {trail.map((crumb, index) => (
              <span key={crumb} className="flex min-w-0 items-center gap-1">
                <ChevronRight className={cn("size-3 shrink-0", index === 0 && "hidden sm:block")} />
                <span
                  className={cn(
                    "truncate",
                    index === trail.length - 1 && "font-medium text-foreground",
                  )}
                >
                  {crumb}
                </span>
              </span>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-1.5">
            <span className="mr-1 hidden text-[11px] text-muted-foreground tabular-nums md:inline">
              {updatedAt
                ? `更新于 ${new Date(updatedAt).toLocaleTimeString("zh-CN", { hour12: false })}`
                : "正在读取…"}
            </span>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="text-muted-foreground"
                  onClick={() => void refresh()}
                  aria-label="刷新"
                >
                  <RefreshCw />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">立刻拉一次状态</TooltipContent>
            </Tooltip>
            <ThemeToggle />
            <MobileNav route={route} />
          </div>
        </header>

        {error ? (
          <div className="border-b border-destructive/25 bg-destructive/8 px-5 py-2 text-[12px] text-destructive">
            连不上控制台自己的接口：{error}
          </div>
        ) : null}

        <main
          ref={mainRef}
          className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6"
        >
          {route.kind === "overview" ? <OverviewPage /> : null}
          {route.kind === "service" ? <ServicePage id={route.id} tab={route.tab} /> : null}
          {route.kind === "features" ? <FeaturesPage group={route.group} /> : null}
          {route.kind === "hub" && route.page === "setup" ? <SetupPage initialTab={route.tab} /> : null}
          {route.kind === "hub" && route.page === "chats" ? <ChatsPage /> : null}
          {route.kind === "hub" && route.page === "read" ? <ReadPage /> : null}
          {route.kind === "hub" && route.page === "journal" ? <JournalPage /> : null}
          {route.kind === "hub" && !HUB_PAGES.some((page) => page.id === route.page) ? (
            <UnknownHubPage page={route.page} />
          ) : null}
          {route.kind === "about" ? <AboutPage /> : null}
        </main>
      </div>
    </div>
  );
}

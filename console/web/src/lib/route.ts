import { useEffect, useState } from "react";

export type ServiceTab = "status" | "logs" | "api";

export type Route =
  | { kind: "overview" }
  | { kind: "services" }
  | { kind: "service"; id: string; tab: ServiceTab }
  | { kind: "features"; group: string }
  | { kind: "hub"; page: string; tab?: string }
  | { kind: "about" };

export type HubPage = {
  id: string;
  path: string;
  title: string;
  desc: string;
};

/** 陪伴相关的几页。都是控制台自己的 React 页面，数据走 /api/hub 转发。 */
export const HUB_PAGES: HubPage[] = [
  { id: "setup", path: "/setup", title: "配置", desc: "安卓 / Windows / Mac 的地址、口令与开关" },
  { id: "layout", path: "/layout", title: "主屏布局", desc: "手机主屏上模块的位置和大小" },
  { id: "chats", path: "/chats", title: "聊天", desc: "和她说过的每一段，按天翻" },
  { id: "read", path: "/read", title: "朗读", desc: "丢一段文字，让 Mini 念出来" },
  { id: "journal", path: "/journal", title: "周报", desc: "这一周的账，和她自己记的那些天" },
];

/** 老的 #/hub/week、#/hub/diary 合并进 journal，别让收藏的链接 404。 */
const HUB_ALIAS: Record<string, string> = { week: "journal", diary: "journal" };

export function hubPage(id: string): HubPage | undefined {
  return HUB_PAGES.find((page) => page.id === id);
}

const SERVICE_TABS: ServiceTab[] = ["status", "logs", "api"];

export function parseRoute(raw: string): Route {
  const parts = raw.replace(/^#/, "").split("/").filter(Boolean);
  const [head, second, third] = parts;

  if (!head) return { kind: "overview" };
  if (head === "services" && second) {
    const tab = SERVICE_TABS.find((item) => item === third) ?? "status";
    return { kind: "service", id: second, tab };
  }
  if (head === "services") return { kind: "services" };
  if (head === "features") return { kind: "features", group: second ?? "" };
  if (head === "hub" && second === "setup" && third === "layout") {
    return { kind: "hub", page: "layout" };
  }
  if (head === "hub" && second) {
    return { kind: "hub", page: HUB_ALIAS[second] ?? second, tab: third };
  }
  // 老的 #/windows：并进三端配置的 Windows 页，收藏的链接不 404
  if (head === "windows") return { kind: "hub", page: "setup", tab: "win" };
  if (head === "about") return { kind: "about" };
  return { kind: "overview" };
}

export function pathOf(route: Route): string {
  switch (route.kind) {
    case "services":
      return "/services";
    case "service":
      return `/services/${route.id}/${route.tab}`;
    case "features":
      return route.group ? `/features/${route.group}` : "/features";
    case "hub":
      return route.tab ? `/hub/${route.page}/${route.tab}` : `/hub/${route.page}`;
    case "about":
      return "/about";
    default:
      return "/";
  }
}

export function hrefOf(route: Route): string {
  return `#${pathOf(route)}`;
}

export function navigate(route: Route, { replace = false } = {}) {
  const target = hrefOf(route);
  if (window.location.hash === target) return;
  if (replace) {
    window.history.replaceState(null, "", target);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    return;
  }
  window.location.hash = target;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const sync = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  return route;
}

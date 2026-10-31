import { motion } from "motion/react";

import { WeekChart } from "@/components/week-chart";
import { PageHeader } from "@/components/page";
import { ServiceDot } from "@/components/service-state";
import { useConsole } from "@/lib/console-store";
import { useHub } from "@/lib/hub";
import { hrefOf } from "@/lib/route";
import { cn } from "@/lib/utils";

type Chat = { ts: string; reply: string; user: string; speaker?: string | null };

type Story = {
  playing: boolean;
  book_id?: string | null;
  chapter_title?: string | null;
  sentence?: string | null;
};

type Day = { date: string; desk_sec: number; chats: number };

type Week = { summary?: { days: Day[] }; line?: string };

function clock(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

function deskText(sec: number): string {
  const minutes = Math.round((sec || 0) / 60);
  if (minutes <= 0) return "0 分钟";
  if (minutes < 60) return `${minutes} 分钟`;
  const hour = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hour} 小时 ${rest} 分` : `${hour} 小时`;
}

export function TodayPage() {
  const { services } = useConsole();
  const chats = useHub<{ items: Chat[] }>("/v1/companion/chats?limit=12", 8000);
  const story = useHub<Story>("/v1/companion/story", 8000);
  const week = useHub<Week>("/v1/companion/week", 60_000);

  const last = chats.data?.items.at(-1);
  const days = week.data?.summary?.days ?? [];
  const todayKey = new Date().toISOString().slice(0, 10);
  const today = days.find((day) => day.date === todayKey);
  const book = (story.data?.book_id ?? "").replace(/\.txt$/i, "");
  const reading = story.data?.playing
    ? story.data.sentence || story.data.chapter_title || book || "正在念"
    : "停着";

  return (
    <div className="space-y-6">
      <PageHeader title="今天" subtitle="她刚才说的，书停在哪，今天在不在桌上。" />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {services.map((service) => (
          <a
            key={service.id}
            href={hrefOf({ kind: "service", id: service.id, tab: "status" })}
            className="inline-flex items-center gap-2 text-[12.5px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <ServiceDot service={service} />
            {service.name}
          </a>
        ))}
        {!services.length ? (
          <span className="text-[12.5px] text-muted-foreground">正在读服务…</span>
        ) : null}
      </div>

      <motion.a
        href={hrefOf({ kind: "hub", page: "chats" })}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28, ease: "easeOut" }}
        className="block rounded-2xl border bg-card px-5 py-5 transition-colors hover:bg-accent/40"
      >
        <div className="text-[11px] tracking-wide text-muted-foreground">她刚才说</div>
        <p className="mt-2 text-[20px] leading-snug font-medium tracking-tight">
          {last?.reply || (chats.loading ? "在听…" : "今天还没说话。")}
        </p>
        {last ? (
          <p className="mt-3 text-[12px] text-muted-foreground">
            {clock(last.ts)}
            {last.user ? ` · 你说「${last.user}」` : ""}
          </p>
        ) : null}
      </motion.a>

      <div className="grid gap-3 sm:grid-cols-2">
        <a
          href={hrefOf({ kind: "hub", page: "read" })}
          className="rounded-xl border bg-card px-4 py-3.5 transition-colors hover:bg-accent/40"
        >
          <div className="text-[11px] text-muted-foreground">朗读</div>
          <div className={cn("mt-1 text-[15px] leading-snug", story.data?.playing && "text-primary")}>
            {reading}
          </div>
          {book && story.data?.playing ? (
            <div className="mt-1 truncate text-[11.5px] text-muted-foreground">{book}</div>
          ) : null}
        </a>
        <a
          href={hrefOf({ kind: "hub", page: "journal" })}
          className="rounded-xl border bg-card px-4 py-3.5 transition-colors hover:bg-accent/40"
        >
          <div className="text-[11px] text-muted-foreground">今天在桌</div>
          <div className="mt-1 text-[15px]">{deskText(today?.desk_sec ?? 0)}</div>
          <div className="mt-1 text-[11.5px] text-muted-foreground">
            {today?.chats ? `说了 ${today.chats} 句` : "还没开口"}
          </div>
        </a>
      </div>

      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <h2 className="text-[13px] font-semibold">这一周</h2>
          <a
            href={hrefOf({ kind: "hub", page: "journal" })}
            className="text-[12px] text-muted-foreground hover:text-foreground"
          >
            周报
          </a>
        </div>
        <div className="rounded-xl border bg-card px-3 pt-2 pb-1">
          <WeekChart days={days} height={168} />
        </div>
      </div>
    </div>
  );
}

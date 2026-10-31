import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, MessageSquare, Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState, PageHeader } from "@/components/page";
import { HubNotice } from "@/pages/hub-notice";
import { useHub } from "@/lib/hub";
import { cn } from "@/lib/utils";

type Skill = {
  id?: string;
  name?: string;
  on?: boolean;
  ok?: boolean;
  brightness?: number;
  route?: string;
};

type Chat = {
  ts: string;
  turn: string;
  user: string;
  reply: string;
  ms?: number;
  skills?: Skill[];
  error?: string | null;
  speaker?: string | null;
};

type ChatsData = { items: Chat[]; days?: string[] };

const LIVE = "live";

function dayLabel(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", weekday: "short" });
}

function timeLabel(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function dayChoice(day: string): string {
  const date = new Date(`${day}T00:00:00`);
  if (Number.isNaN(date.getTime())) return day;
  return `${day} · ${date.toLocaleDateString("zh-CN", { weekday: "short" })}`;
}

function skillText(skill: Skill): string {
  const name = skill.name || skill.id;
  if (!name) return skill.route || "";
  const action =
    skill.on === false ? "关" : /wegame|douyin|steam|cursor/i.test(skill.id ?? "") ? "启动" : "开";
  const failed = skill.ok === false ? " · 失败" : "";
  return `${action}${name}${failed}`;
}

function Bubble({ side, who, text, chat }: { side: "you" | "her"; who: string; text: string; chat: Chat }) {
  const skills = chat.skills ?? [];
  return (
    <div className={cn("flex w-full", side === "you" ? "justify-end" : "justify-start")}>
      <article
        className={cn(
          "max-w-[min(640px,84%)] rounded-xl border px-3.5 py-2.5 text-[13px] leading-relaxed",
          side === "you"
            ? "border-primary/20 bg-primary/8"
            : "border-border/70 bg-card shadow-[0_1px_2px_rgba(0,0,0,0.03)]",
        )}
      >
        <div className="mb-1 text-[10.5px] font-medium tracking-wide text-muted-foreground">{who}</div>
        <div className="whitespace-pre-wrap">{text || "（无原文）"}</div>
        {skills.length ? (
          <div className="mt-2 flex flex-wrap gap-1">
            {skills.map((skill, index) => (
              <span
                key={`${skill.id ?? index}-${index}`}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[10.5px]",
                  skill.ok === false
                    ? "border-destructive/30 bg-destructive/8 text-destructive"
                    : "border-border bg-muted/60 text-muted-foreground",
                )}
              >
                {skillText(skill)}
              </span>
            ))}
          </div>
        ) : null}
        {chat.error ? (
          <div className="mt-2 rounded-md bg-destructive/8 px-2 py-1 text-[11.5px] text-destructive">
            {chat.error}
          </div>
        ) : null}
        <div className="mt-1.5 text-[10.5px] text-muted-foreground tabular-nums">
          {timeLabel(chat.ts)}
          {chat.ms ? ` · ${chat.ms}ms` : ""}
        </div>
      </article>
    </div>
  );
}

export function ChatsPage() {
  const [raw, setRaw] = useState("");
  const [query, setQuery] = useState("");
  const [day, setDay] = useState<string>(LIVE);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(raw.trim()), 260);
    return () => window.clearTimeout(timer);
  }, [raw]);

  const path = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", "400");
    params.set("with_days", "1");
    if (query) params.set("q", query);
    else if (day !== LIVE) params.set("day", day);
    return `/v1/companion/chats?${params.toString()}`;
  }, [query, day]);

  // 只有「今晚」在实时动；翻旧账和搜索都是静止的过去
  const live = !query && day === LIVE;
  const { data, error, loading, reload } = useHub<ChatsData>(path, live ? 4000 : 0);

  const days = data?.days ?? [];
  const items = data?.items ?? [];
  const endRef = useRef<HTMLDivElement>(null);
  const count = items.length;

  // 跟着新话走：首次进来和来了新的一句都滚到底。翻旧账/搜索不动用户的滚动位置。
  useEffect(() => {
    if (!live || !count) return;
    endRef.current?.scrollIntoView({ block: "end" });
  }, [count, live]);

  const step = (delta: number) => {
    if (!days.length) return;
    if (day === LIVE) {
      if (delta < 0) setDay(days[days.length - 1]);
      return;
    }
    const index = days.indexOf(day);
    if (index < 0) return;
    const next = index + delta;
    if (next >= days.length) setDay(LIVE);
    else if (next >= 0) setDay(days[next]);
  };

  if (error && !data) {
    return (
      <div className="space-y-5">
        <PageHeader title="聊天记录" subtitle="你和她说的每一段，按天翻。" />
        <HubNotice message={error} onRetry={reload} />
      </div>
    );
  }

  let lastDay = "";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 pb-2">
      <PageHeader
        title="聊天记录"
        subtitle="你对她说的每一句，和她回的每一句。只存在这台电脑上。"
        actions={
          <span className="text-[11.5px] text-muted-foreground tabular-nums">
            {loading && !items.length ? "读着呢…" : `${items.length} 段 · ${days.length} 天有记录`}
          </span>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={raw}
            onChange={(event) => setRaw(event.target.value)}
            placeholder="搜一句话、一个设备名"
            className="h-8 pl-8 text-[12.5px]"
          />
        </div>

        <div className="flex items-center gap-1">
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="前一天"
            disabled={!days.length || (day !== LIVE && days.indexOf(day) <= 0)}
            onClick={() => step(-1)}
          >
            <ChevronLeft />
          </Button>
          <Select value={day} onValueChange={setDay}>
            <SelectTrigger size="sm" className="h-8 min-w-[168px] text-[12.5px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={LIVE} className="text-[12.5px]">
                今晚
              </SelectItem>
              {days.map((item) => (
                <SelectItem key={item} value={item} className="text-[12.5px]">
                  {dayChoice(item)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="后一天"
            disabled={day === LIVE}
            onClick={() => step(1)}
          >
            <ChevronRight />
          </Button>
        </div>
      </div>

      {!items.length ? (
        <EmptyState
          icon={<MessageSquare className="size-6" />}
          title={loading ? "读着呢…" : query ? "没搜到" : "这天没说话"}
          description={
            query
              ? `「${query}」在记录里没有出现过。换个词试试。`
              : "对脸说一句之后，原文会出现在这里。"
          }
        />
      ) : (
        <div className="scroll-thin min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          {items.map((chat) => {
            const label = dayLabel(chat.ts);
            const newDay = label !== lastDay;
            lastDay = label;
            return (
              <div key={`${chat.turn}-${chat.ts}`} className="space-y-2">
                {newDay ? (
                  <div className="flex items-center gap-2 pt-2">
                    <span className="text-[11px] font-medium text-muted-foreground">{label}</span>
                    <span className="h-px flex-1 bg-border" />
                  </div>
                ) : null}
                <Bubble side="you" who="你" text={chat.user} chat={chat} />
                <Bubble side="her" who={chat.speaker || "守岸人"} text={chat.reply} chat={chat} />
              </div>
            );
          })}
          <div ref={endRef} />
        </div>
      )}
    </div>
  );
}

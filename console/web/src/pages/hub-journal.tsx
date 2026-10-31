import { useState } from "react";
import {
  CalendarDays,
  Clock3,
  MessageSquare,
  NotebookPen,
  Power,
  Timer,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, PageHeader, Section } from "@/components/page";
import { HubNotice } from "@/pages/hub-notice";
import { hubDelete, useHub } from "@/lib/hub";
import { cn } from "@/lib/utils";

type Day = {
  date: string;
  weekday: number;
  desk_sec: number;
  read_sec: number;
  chats: number;
  boots: number;
  last_at: string | null;
};

type WeekData = {
  summary: {
    days: Day[];
    total: { desk_sec: number; read_sec: number; chats: number; boots: number };
    delta: { desk_sec: number; read_sec: number; chats: number; boots: number };
    latest_at: string | null;
  };
  line: string;
};

type Entry = { date: string; text: string; source?: string };

type DiaryData = { entries: Entry[]; enabled: boolean; at?: string };

const WEEKDAY = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const DIARY_WEEKDAY = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const SOURCE: Record<string, string> = {
  llm: "她自己写的",
  log: "大脑不在，只记了句事实",
};

const TABS = [
  { id: "week", label: "这一周" },
  { id: "diary", label: "日记" },
] as const;

type JournalTab = (typeof TABS)[number]["id"];

export function duration(sec: number): string {
  const minutes = Math.round((sec || 0) / 60);
  if (minutes <= 0) return "0 分钟";
  if (minutes < 60) return `${minutes} 分钟`;
  const hour = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hour} 小时 ${rest} 分` : `${hour} 小时`;
}

function deltaText(sec: number): string {
  const minutes = Math.round(sec / 60);
  if (!minutes) return "和上周持平";
  return `${minutes > 0 ? "比上周多" : "比上周少"} ${duration(Math.abs(sec))}`;
}

function weekdayOf(day: string): string {
  const date = new Date(`${day}T00:00:00`);
  return Number.isNaN(date.getTime()) ? "" : (DIARY_WEEKDAY[date.getDay()] ?? "");
}

function Metric({
  icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  tone?: "up" | "down";
}) {
  return (
    <Card className="gap-0 py-0">
      <div className="flex items-center gap-1.5 px-4 pt-3.5 text-[11.5px] text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="px-4 pt-1 pb-1 text-[24px] leading-tight font-semibold tabular-nums">
        {value}
      </div>
      <div
        className={cn(
          "px-4 pb-3.5 text-[11px]",
          tone === "up" ? "text-ok" : "text-muted-foreground",
        )}
      >
        {hint || "\u00a0"}
      </div>
    </Card>
  );
}

function WeekView() {
  const { data, error, loading, reload } = useHub<WeekData>("/v1/companion/week", 60_000);

  if (error) {
    return <HubNotice message={error} onRetry={reload} />;
  }

  const summary = data?.summary;
  const total = summary?.total;
  const delta = summary?.delta;
  const days = summary?.days ?? [];
  const peak = Math.max(...days.map((day) => day.desk_sec), 1);
  const today = new Date().toISOString().slice(0, 10);

  return (
    <div className="space-y-5">
      {data?.line ? (
        <Card className="gap-0 border-primary/25 bg-primary/5 py-0">
          <div className="px-5 py-4 text-[14px] leading-relaxed">「{data.line}」</div>
        </Card>
      ) : (
        <Card className="gap-0 py-0">
          <div className="px-5 py-4 text-[13px] text-muted-foreground">
            {loading ? "读着呢…" : "这一周还没有可说的。开会儿机、聊几句，周日晚上她就有得讲了。"}
          </div>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Metric
          icon={<Clock3 className="size-3.5" />}
          label="在桌时长"
          value={duration(total?.desk_sec ?? 0)}
          hint={delta ? deltaText(delta.desk_sec) : undefined}
          tone={delta && delta.desk_sec > 0 ? "up" : undefined}
        />
        <Metric
          icon={<Timer className="size-3.5" />}
          label="朗读时长"
          value={duration(total?.read_sec ?? 0)}
          hint="她念给你听的时间"
        />
        <Metric
          icon={<CalendarDays className="size-3.5" />}
          label="最晚一次"
          value={summary?.latest_at || "—"}
          hint="这周最晚还在桌前的时刻"
        />
        <Metric
          icon={<MessageSquare className="size-3.5" />}
          label="说了多少句"
          value={`${total?.chats ?? 0} 句`}
          hint="你对她说的"
        />
        <Metric
          icon={<Power className="size-3.5" />}
          label="开了几次机"
          value={`${total?.boots ?? 0} 次`}
          hint="Mini 看见的开机"
        />
      </div>

      <Section title="这一周" description="灰条是在桌时长，实心是今天。">
        <Card className="gap-0 py-0">
          <div className="divide-y divide-border/70">
            {days.map((day) => {
              const width = Math.max(
                day.desk_sec > 0 ? 6 : 0,
                Math.round((day.desk_sec / peak) * 100),
              );
              const isToday = day.date === today;
              const idle = !day.desk_sec && !day.chats && !day.read_sec && !day.boots;
              return (
                <div key={day.date} className="flex items-center gap-3 px-4 py-2.5">
                  <div className="w-[74px] shrink-0 text-[12px] tabular-nums">{day.date.slice(5)}</div>
                  <div
                    className={cn(
                      "w-[46px] shrink-0 text-[11.5px]",
                      isToday ? "font-medium text-primary" : "text-muted-foreground",
                    )}
                  >
                    {WEEKDAY[day.weekday] ?? ""}
                  </div>
                  <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn("h-full rounded-full", isToday ? "bg-primary" : "bg-primary/35")}
                      style={{ width: `${width}%` }}
                    />
                  </div>
                  <div className="w-[92px] shrink-0 text-right text-[11.5px] text-muted-foreground tabular-nums">
                    {idle ? "没开机" : duration(day.desk_sec)}
                  </div>
                  <div className="hidden w-[86px] shrink-0 text-right text-[11.5px] text-muted-foreground tabular-nums sm:block">
                    {day.chats ? `${day.chats} 句` : "—"}
                  </div>
                  <div className="hidden w-[52px] shrink-0 text-right text-[11.5px] text-muted-foreground tabular-nums md:block">
                    {day.last_at ?? "—"}
                  </div>
                </div>
              );
            })}
            {!days.length ? (
              <div className="px-4 py-10 text-center text-[12.5px] text-muted-foreground">
                {loading ? "读着呢…" : "还没有台账。"}
              </div>
            ) : null}
          </div>
        </Card>
      </Section>

      <p className="text-[11.5px] leading-relaxed text-muted-foreground">
        她会在周日晚 20:00 把这句念出来（主动播报开着的时候）。数字都在
        <code className="mx-1 rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
          ~/.config/dock-hub/hub.yaml
        </code>
        所在的机器上，不上传。
      </p>
    </div>
  );
}

function DiaryView() {
  const { data, error, loading, reload } = useHub<DiaryData>("/v1/companion/diary?limit=200", 120_000);
  const [pending, setPending] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const remove = async (day: string) => {
    setDeleting(true);
    try {
      await hubDelete(`/v1/companion/diary/${day}`);
      toast.success(`${day} 这一天删掉了`);
      setPending(null);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  };

  if (error && !data) {
    return <HubNotice message={error} onRetry={reload} />;
  }

  const entries = data?.entries ?? [];
  const off = data?.enabled === false;

  return (
    <div className="space-y-4">
      {entries.length ? (
        <p className="text-[11.5px] leading-relaxed text-muted-foreground">
          {off
            ? "日记关着（hub.yaml 里 diary: false）。哪天想让她写了，把那行删掉就行。"
            : `她每天 ${data?.at || "21:30"} 写一段今天。写的都是她记得的事，只留在你自己的 Hub 上。`}
        </p>
      ) : null}

      {!entries.length ? (
        <EmptyState
          icon={<NotebookPen className="size-6" />}
          title={loading ? "读着呢…" : "还没有日记"}
          description={
            off
              ? "把 hub.yaml 里的 diary: false 去掉，她就会接着写。"
              : `到点（${data?.at || "21:30"}）她就会写今天的头一段。`
          }
        />
      ) : (
        <div className="space-y-3">
          {entries.map((entry) => (
            <Card key={entry.date} className="gap-0 py-0">
              <div className="flex items-baseline justify-between gap-3 border-b border-border/60 px-5 py-3">
                <div className="flex items-baseline gap-2">
                  <span className="text-[14px] font-semibold tabular-nums">{entry.date}</span>
                  <span className="text-[11.5px] text-muted-foreground">{weekdayOf(entry.date)}</span>
                </div>
                <span className="text-[11px] text-muted-foreground">
                  {SOURCE[entry.source ?? ""] ?? ""}
                </span>
              </div>
              <p className="px-5 py-4 text-[13.5px] leading-7 whitespace-pre-wrap">{entry.text}</p>
              <div className="flex justify-end border-t border-border/60 px-4 py-2">
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-muted-foreground hover:text-destructive"
                  onClick={() => setPending(entry.date)}
                >
                  <Trash2 />
                  删掉这一天
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog
        open={Boolean(pending)}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
      >
        <DialogContent className="max-w-[400px]">
          <DialogHeader>
            <DialogTitle>删掉 {pending} 这一天的日记？</DialogTitle>
            <DialogDescription>删了就没有了，聊天记录不受影响。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)}>
              留着
            </Button>
            <Button
              variant="destructive"
              disabled={deleting}
              onClick={() => (pending ? void remove(pending) : undefined)}
            >
              删掉
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function JournalPage() {
  // 老链接 #/hub/diary 还认得，直接落到日记那一格
  const [tab, setTab] = useState<JournalTab>(() =>
    window.location.hash.includes("/diary") ? "diary" : "week",
  );

  return (
    <div className="space-y-4 pb-6">
      <PageHeader
        title="周报与日记"
        subtitle={
          tab === "week"
            ? "最近 7 天（含今天）。在桌时长来自 Mini 的心跳，朗读来自她念过的字。"
            : "她每天傍晚写一段今天。写的都是她记得的事，只留在你自己的 Hub 上。"
        }
      />

      <Tabs value={tab} onValueChange={(value) => setTab(value as JournalTab)}>
        <TabsList className="h-8">
          {TABS.map((item) => (
            <TabsTrigger key={item.id} value={item.id} className="text-[12.5px]">
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {tab === "week" ? <WeekView /> : <DiaryView />}
    </div>
  );
}

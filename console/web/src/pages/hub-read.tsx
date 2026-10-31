import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronDown,
  Download,
  FileText,
  ListMusic,
  Pause,
  Play,
  RotateCw,
  ScrollText,
  Square,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, PageHeader } from "@/components/page";
import { HubNotice } from "@/pages/hub-notice";
import { hubFile, hubGet, hubPost, hubUpload, useHub } from "@/lib/hub";
import { cn } from "@/lib/utils";

type Job = {
  id: string;
  mode: string;
  status: string;
  preview?: string;
  chars?: number;
  chunks?: number;
  done?: number;
  duration_sec?: number;
  estimate_sec?: number;
  error?: string;
  filename?: string;
  audio_id?: string;
};

type Book = { id: string; title: string; bytes: number; mtime: number };

type BooksData = { items: Book[]; current_id?: string; timer_min?: number };

type ChapterData = {
  index: number;
  title: string;
  text: string;
  start: number;
  end: number;
};

type Story = {
  playing: boolean;
  book_id?: string | null;
  offset?: number;
  chapter?: number | null;
  chapter_title?: string | null;
  chars?: number;
  chapters?: number;
  timer_min?: number;
  timer_remaining_sec?: number | null;
  stopped_by?: string | null;
  error?: string | null;
  /** 正在念的那一句原文，和它在全书里的起点。Hub 每句更新一次。 */
  sentence?: string | null;
  sentence_start?: number | null;
};

type Chapter = { index: number; title: string; volume?: string; start: number; end: number };

type BookDetail = {
  id: string;
  title: string;
  chars: number;
  bookmark?: number;
  chapters: Chapter[];
};

const LIMIT = 8000;
const TABS = [
  { id: "shelf", label: "书架" },
  { id: "synth", label: "语音合成" },
] as const;

type ReadTab = (typeof TABS)[number]["id"];

function fmtSec(sec?: number | null): string {
  if (!sec || sec <= 0) return "0 秒";
  const total = Math.round(sec);
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
  const hour = Math.floor(minutes / 60);
  return `${hour} 小时 ${minutes % 60} 分`;
}

function fmtCount(chars?: number): string {
  const total = Math.round(Number(chars) || 0);
  if (total >= 10000) return `约 ${(total / 10000).toFixed(1)} 万字`;
  if (total > 0) return `约 ${total} 字`;
  return "空文件";
}

/** 书架接口只给字节数；utf-8 下一个汉字约三字节，粗估厚薄够用了。 */
function fmtSize(bytes?: number): string {
  return fmtCount((Number(bytes) || 0) / 3);
}

function fmtDate(mtime?: number): string {
  if (!mtime) return "—";
  const ms = mtime > 1e12 ? mtime : mtime * 1000;
  return new Date(ms).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function bare(id?: string | null): string {
  return (id ?? "").replace(/\.txt$/i, "");
}

function statusText(job: Job): string {
  if (job.mode === "play") {
    if (job.status === "queued") return "等待播放";
    if (job.status === "running") return "正在送到书桌";
    if (job.status === "done") return "已送到书桌";
  }
  const table: Record<string, string> = {
    queued: "排队中",
    running: "合成中",
    done: "已完成",
    cancelled: "已取消",
    error: "失败",
  };
  return table[job.status] ?? job.status;
}

function statusTone(job: Job): "ok" | "warn" | "idle" | "bad" {
  if (job.status === "done") return "ok";
  if (job.status === "running" || job.status === "queued") return "warn";
  if (job.status === "error") return "bad";
  return "idle";
}

function progress(job: Job): number {
  if (job.status === "done") return 100;
  const total = Number(job.chunks) || 0;
  if (!total) return 0;
  return Math.max(0, Math.min(100, Math.round(((Number(job.done) || 0) / total) * 100)));
}

export function ReadPage() {
  const jobs = useHub<{ items: Job[] }>("/v1/companion/read/jobs", 2000);
  const books = useHub<BooksData>("/v1/companion/books", 15_000);
  const story = useHub<Story>("/v1/companion/story", 2000);

  const [tab, setTab] = useState<ReadTab>("shelf");
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [book, setBook] = useState<string>("");
  const [detail, setDetail] = useState<BookDetail | null>(null);
  const [tocOpen, setTocOpen] = useState(false);
  const [tocFilter, setTocFilter] = useState("");
  const [bodyOpen, setBodyOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const bookRef = useRef<HTMLInputElement>(null);

  const list = jobs.data?.items ?? [];
  const shelf = books.data?.items ?? [];
  const current = book || books.data?.current_id || shelf[0]?.id || "";
  const live = story.data;
  const onThisBook = Boolean(live?.book_id) && bare(live?.book_id) === bare(current);
  const playing = Boolean(live?.playing) && onThisBook;

  // 在念哪一章：正在念就用 story 的；没在念就按书签落到那一章
  const readingChapter = onThisBook && live?.chapter != null ? live.chapter : null;
  const chapterIndex = useMemo(() => {
    const list = detail?.chapters ?? [];
    if (readingChapter != null) return readingChapter;
    if (!list.length) return null;
    const at = detail?.bookmark ?? 0;
    const found = list.find((item) => at >= item.start && at < item.end);
    return (found ?? list[0]).index;
  }, [readingChapter, detail]);

  const chapter = useHub<ChapterData>(
    current && chapterIndex != null
      ? `/v1/companion/books/${encodeURIComponent(current)}/chapter/${chapterIndex}`
      : null,
  );

  // 当前句在章节正文里的位置：先用 offset 估，再在附近精确找一遍这句话
  const span = useMemo(() => {
    const text = chapter.data?.text ?? "";
    if (!text) return null;
    const start = chapter.data?.start ?? 0;
    const sentence = onThisBook ? (live?.sentence ?? "") : "";
    const raw = (onThisBook ? (live?.sentence_start ?? live?.offset) : detail?.bookmark) ?? 0;
    let rel = Math.max(0, Math.min(text.length, raw - start));
    if (sentence) {
      const near = text.indexOf(sentence, Math.max(0, rel - 40));
      if (near >= 0) rel = near;
    }
    return { text, rel, size: sentence.length };
  }, [chapter.data, onThisBook, live?.sentence, live?.sentence_start, live?.offset, detail?.bookmark]);

  // 一章可能几十万字，只切当前句前后各四千字，免得把 DOM 撑爆
  const piece = useMemo(() => {
    if (!span) return null;
    const from = Math.max(0, span.rel - 4000);
    const to = Math.min(span.text.length, span.rel + span.size + 4000);
    return {
      before: span.text.slice(from, span.rel),
      hit: span.text.slice(span.rel, span.rel + span.size),
      after: span.text.slice(span.rel + span.size, to),
      head: from > 0,
      tail: to < span.text.length,
    };
  }, [span]);

  const bodyRef = useRef<HTMLDivElement>(null);
  const markRef = useRef<HTMLSpanElement>(null);

  // 跟着念的那句走：只在自己滚动的那块区域里居中，不动整页
  useEffect(() => {
    if (!bodyOpen) return;
    const box = bodyRef.current;
    const mark = markRef.current;
    if (!box || !mark) return;
    box.scrollTop = Math.max(0, mark.offsetTop - box.clientHeight / 2 + 24);
  }, [bodyOpen, live?.sentence_start, chapter.data?.index]);

  useEffect(() => {
    if (!current) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    hubGet<BookDetail>(`/v1/companion/books/${encodeURIComponent(current)}`)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [current]);

  const chapters = detail?.chapters ?? [];
  const filtered = useMemo(() => {
    const keyword = tocFilter.trim();
    if (!keyword) return chapters.slice(0, 300);
    return chapters.filter((item) => item.title.includes(keyword)).slice(0, 300);
  }, [chapters, tocFilter]);

  const act = async (key: string, label: string, task: () => Promise<unknown>) => {
    setPending(key);
    try {
      await task();
      if (label) toast.success(label);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
      jobs.reload();
      story.reload();
      books.reload();
    }
  };

  const submit = (mode: "play" | "file") =>
    act(`submit:${mode}`, mode === "play" ? "已加入播放队列" : "已加入合成队列", async () => {
      const body = text.trim();
      if (!body) throw new Error("先贴一段文字");
      if (body.length > LIMIT) throw new Error(`一次最多 ${LIMIT} 字`);
      await hubPost("/v1/companion/read", { text: body, mode });
      setText("");
    });

  const loadFile = (file: File | null | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const raw = String(reader.result ?? "");
      setText(raw.slice(0, LIMIT));
      toast.success(raw.length > LIMIT ? `${file.name} · 已截到八千字` : `${file.name} · 已读入`);
    };
    reader.readAsText(file, "utf-8");
  };

  const uploadBook = async (file: File | null | undefined) => {
    if (!file) return;
    if (!/\.txt$/i.test(file.name)) {
      toast.error("只收 txt 文件");
      return;
    }
    if (file.size > 32 * 1024 * 1024) {
      toast.error("文件超过 32MB，Hub 收不下");
      return;
    }
    setPending("upload");
    try {
      const saved = await hubUpload<{ id: string; bytes: number }>("/v1/companion/books", file);
      toast.success(`《${saved.id.replace(/\.txt$/i, "")}》放进书架了`);
      setBook(saved.id);
      setDetail(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
      books.reload();
    }
  };

  if (jobs.error && !jobs.data) {
    return (
      <div className="space-y-5">
        <PageHeader title="朗读" subtitle="书架、合成、播放都在这儿。" />
        <HubNotice message={jobs.error} onRetry={jobs.reload} />
      </div>
    );
  }

  const chapterTitle = onThisBook ? (live?.chapter_title ?? "") : "";
  const percent = onThisBook
    ? live?.chars && live.offset != null
      ? Math.min(100, Math.round((live.offset / live.chars) * 100))
      : 0
    : detail?.chars && detail.bookmark
      ? Math.min(100, Math.round((detail.bookmark / detail.chars) * 100))
      : 0;

  const openBook = (id: string) => {
    if (id === current) return;
    setBook(id);
    setDetail(null);
  };

  return (
    <div className="space-y-4 pb-6">
      <input
        ref={bookRef}
        type="file"
        accept=".txt,text/plain"
        className="hidden"
        onChange={(event) => {
          void uploadBook(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <PageHeader
        title="朗读"
        subtitle="先挑一本书接着念；临时一段字去「语音合成」，合成完可以直接送到书桌。"
        actions={
          <Button
            size="sm"
            variant="outline"
            disabled={pending === "stop"}
            onClick={() => act("stop", "已经停下", () => hubPost("/v1/companion/stop", {}))}
          >
            <Square />
            停一下
          </Button>
        }
      />

      <Tabs value={tab} onValueChange={(value) => setTab(value as ReadTab)}>
        <TabsList className="h-8">
          {TABS.map((item) => (
            <TabsTrigger key={item.id} value={item.id} className="text-[12.5px]">
              {item.label}
              {item.id === "shelf" && shelf.length ? (
                <span className="ml-1 text-[10.5px] text-muted-foreground tabular-nums">
                  {shelf.length}
                </span>
              ) : null}
              {item.id === "synth" && list.length ? (
                <span className="ml-1 text-[10.5px] text-muted-foreground tabular-nums">
                  {list.length}
                </span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {tab === "shelf" ? (
        <>
          <Card className="gap-0 overflow-hidden py-0">
            {!shelf.length ? (
              <div className="p-4">
                <EmptyState
                  icon={<BookOpen className="size-6" />}
                  title="书架是空的"
                  description="丢一个 txt 进来就行，中文编码它自己认。也可以直接放到 Hub 的书架目录。"
                  action={
                    <Button
                      size="sm"
                      disabled={pending === "upload"}
                      onClick={() => bookRef.current?.click()}
                    >
                      <Upload />
                      导入 txt
                    </Button>
                  }
                />
              </div>
            ) : (
              <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-start sm:gap-5">
                <div className="flex size-14 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-[20px] font-semibold text-primary">
                  {(detail?.title || current).slice(0, 1)}
                </div>

                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-[15px] font-semibold tracking-tight">
                      {detail?.title || current.replace(/\.txt$/i, "") || "挑一本书"}
                    </h2>
                    {playing ? (
                      <Badge variant="outline" className="border-warn/45 text-[10.5px] text-warn">
                        正在念
                      </Badge>
                    ) : null}
                  </div>

                  <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-[11.5px] text-muted-foreground tabular-nums">
                    {chapterTitle ? (
                      <span className="max-w-full truncate text-[12px] text-foreground/85">
                        {chapterTitle}
                      </span>
                    ) : null}
                    {onThisBook && live?.chapter != null && live.chapters ? (
                      <span>
                        第 {live.chapter + 1} / {live.chapters} 章
                      </span>
                    ) : detail ? (
                      <span>共 {detail.chapters.length} 章</span>
                    ) : (
                      <span>正在读书架…</span>
                    )}
                    <span className="ml-auto">{percent}%</span>
                  </div>

                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary transition-[width]"
                      style={{ width: `${percent}%` }}
                    />
                  </div>

                  {onThisBook && live?.sentence ? (
                    <div className="rounded-md border-l-2 border-primary bg-muted/50 px-3 py-2 text-[13px] leading-relaxed">
                      <span className="mr-1.5 align-middle text-[10.5px] tracking-wide text-muted-foreground">
                        正在念
                      </span>
                      {live.sentence}
                    </div>
                  ) : null}

                  <div className="flex flex-wrap items-center gap-2 pt-0.5">
                    <span className="text-[11px] text-muted-foreground">定时停</span>
                    <Select
                      value={String(live?.timer_min ?? books.data?.timer_min ?? 30)}
                      onValueChange={(value) =>
                        act(`timer:${value}`, value === "0" ? "定时已关" : `定时 ${value} 分钟`, () =>
                          hubPost("/v1/companion/story/timer", { min: Number(value) }),
                        )
                      }
                    >
                      <SelectTrigger size="sm" className="h-7 w-[92px] text-[11.5px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="0">不定时</SelectItem>
                        <SelectItem value="15">15 分钟</SelectItem>
                        <SelectItem value="30">30 分钟</SelectItem>
                        <SelectItem value="60">60 分钟</SelectItem>
                        <SelectItem value="120">120 分钟</SelectItem>
                      </SelectContent>
                    </Select>
                    {live?.timer_remaining_sec != null && onThisBook ? (
                      <span className="text-[11px] text-muted-foreground tabular-nums">
                        还剩 {Math.ceil(live.timer_remaining_sec / 60)} 分
                      </span>
                    ) : null}
                    {live?.stopped_by === "timer" ? (
                      <span className="text-[11px] text-muted-foreground">到时间了，已停下。</span>
                    ) : null}
                    {live?.stopped_by === "end" ? (
                      <span className="text-[11px] text-muted-foreground">这本书读完了。</span>
                    ) : null}
                    {onThisBook && live?.error ? (
                      <span className="text-[11px] text-destructive">{live.error}</span>
                    ) : null}
                  </div>
                </div>

                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={pending === "upload"}
                    onClick={() => bookRef.current?.click()}
                  >
                    <Upload />
                    导入 txt
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!current || Boolean(pending)}
                    onClick={() => setTocOpen(true)}
                  >
                    <ListMusic />
                    目录
                  </Button>
                  {playing ? (
                    <Button
                      size="sm"
                      disabled={Boolean(pending)}
                      onClick={() => act("pause", "已暂停", () => hubPost("/v1/companion/stop", {}))}
                    >
                      <Pause />
                      暂停
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      disabled={!current || Boolean(pending)}
                      onClick={() =>
                        act("play", "", () =>
                          hubPost(`/v1/companion/books/${encodeURIComponent(current)}/play`, {}),
                        )
                      }
                    >
                      <Play />
                      接着念
                    </Button>
                  )}
                </div>
              </div>
            )}
          </Card>

          {chapterIndex != null ? (
            <Card className="gap-0 py-0">
              <button
                type="button"
                onClick={() => setBodyOpen((open) => !open)}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-muted/40"
              >
                <ScrollText className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="shrink-0 text-[12.5px] font-medium">正文</span>
                <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground">
                  {chapter.data?.title ||
                    chapters.find((item) => item.index === chapterIndex)?.title ||
                    `第 ${chapterIndex + 1} 章`}
                </span>
                <span className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground tabular-nums">
                  {chapter.data ? `${chapter.data.text.length} 字` : ""}
                  <ChevronDown
                    className={cn(
                      "size-3.5 transition-transform",
                      bodyOpen ? "rotate-180" : "rotate-0",
                    )}
                  />
                </span>
              </button>

              {bodyOpen ? (
                <div
                  ref={bodyRef}
                  className="scroll-thin relative max-h-[42vh] min-h-[160px] overflow-y-auto border-t border-border/60 px-5 py-4 text-[13.5px] leading-7 whitespace-pre-wrap"
                >
                  {chapter.error ? (
                    <span className="text-destructive">{chapter.error}</span>
                  ) : !piece ? (
                    <span className="text-muted-foreground">
                      {chapter.loading ? "正在翻这一章…" : "这一章没有正文。"}
                    </span>
                  ) : (
                    <>
                      {piece.head ? <span className="text-muted-foreground">……</span> : null}
                      {piece.before}
                      {piece.hit ? (
                        <span
                          ref={markRef}
                          className="rounded-sm bg-warn/25 px-0.5 font-medium text-foreground"
                        >
                          {piece.hit}
                        </span>
                      ) : (
                        // 没在念的时候也放个空标记，展开正文能落到书签那一句
                        <span ref={markRef} />
                      )}
                      {piece.after}
                      {piece.tail ? <span className="text-muted-foreground">……</span> : null}
                    </>
                  )}
                </div>
              ) : null}
            </Card>
          ) : null}

          {shelf.length ? (
            <div className="space-y-2">
              <div className="flex items-baseline justify-between px-0.5">
                <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  全部藏书
                </span>
                <span className="text-[11px] text-muted-foreground tabular-nums">
                  {shelf.length} 本 · 点一下换书
                </span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {shelf.map((item) => {
                  const active = item.id === current;
                  const reading = Boolean(live?.book_id) && bare(live?.book_id) === bare(item.id);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => openBook(item.id)}
                      className={cn(
                        "flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors",
                        active
                          ? "border-primary/45 bg-primary/5"
                          : "border-border/70 hover:bg-muted/60",
                      )}
                    >
                      <BookOpen
                        className={cn(
                          "mt-0.5 size-4 shrink-0",
                          active ? "text-primary" : "text-muted-foreground",
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-[12.5px] font-medium">{item.title}</span>
                          {reading ? (
                            <span className="size-1.5 shrink-0 rounded-full bg-warn" />
                          ) : null}
                        </div>
                        <div className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
                          {fmtSize(item.bytes)} · {fmtDate(item.mtime)}
                        </div>
                      </div>
                      {active ? <Check className="mt-0.5 size-3.5 shrink-0 text-primary" /> : null}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between gap-2 border-b border-border/60 px-4 py-2.5">
              <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <FileText className="size-3.5 text-muted-foreground" />
                念一段
              </div>
              <div className="flex items-center gap-1.5">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".txt,.md,text/plain"
                  className="hidden"
                  onChange={(event) => {
                    loadFile(event.target.files?.[0]);
                    event.target.value = "";
                  }}
                />
                <Button size="sm" variant="ghost" onClick={() => fileRef.current?.click()}>
                  <Upload />
                  读文件
                </Button>
              </div>
            </div>

            <div className="space-y-3 p-4">
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value.slice(0, LIMIT))}
                placeholder="贴一段文字进来。她会念出来，也可以只合成一个 wav 存下来。"
                className="scroll-thin h-[210px] w-full resize-none rounded-lg border border-input bg-transparent px-3 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/25"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[11.5px] text-muted-foreground tabular-nums">
                  {text.length} / {LIMIT} 字 · 约 {fmtSec(text.length / 5.2)}
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!text.trim() || Boolean(pending)}
                    onClick={() => void submit("file")}
                  >
                    <Download />
                    只生成 wav
                  </Button>
                  <Button
                    size="sm"
                    disabled={!text.trim() || Boolean(pending)}
                    onClick={() => void submit("play")}
                  >
                    <Play />
                    在书桌念
                  </Button>
                </div>
              </div>
            </div>
          </Card>

          <Card className="gap-0 py-0">
            <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
              <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
                <ListMusic className="size-3.5 text-muted-foreground" />
                队列
              </div>
              <span className="text-[11px] text-muted-foreground tabular-nums">{list.length} 条</span>
            </div>

            <div className="scroll-thin max-h-[288px] divide-y divide-border/60 overflow-y-auto">
              {!list.length ? (
                <div className="px-4 py-12 text-center text-[12px] text-muted-foreground">
                  还没有任务。念一段之后会出现在这里。
                </div>
              ) : (
                list.map((job) => {
                  const tone = statusTone(job);
                  return (
                    <div key={job.id} className="space-y-1.5 px-4 py-2.5">
                      <div className="flex items-start gap-2">
                        <div className="min-w-0 flex-1 truncate text-[12.5px]">
                          {job.preview || job.filename || job.id}
                        </div>
                        <Badge
                          variant={tone === "bad" ? "destructive" : "outline"}
                          className={cn(
                            "shrink-0 text-[10.5px]",
                            tone === "ok" && "border-ok/40 text-ok",
                            tone === "warn" && "border-warn/40 text-warn",
                          )}
                        >
                          {statusText(job)}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-2 text-[10.5px] text-muted-foreground tabular-nums">
                        <span>{job.mode === "play" ? "播放" : "文件"}</span>
                        {job.chars ? <span>{job.chars} 字</span> : null}
                        {job.chunks ? (
                          <span>
                            {job.done ?? 0} / {job.chunks} 段
                          </span>
                        ) : null}
                        {job.estimate_sec ? <span>约 {fmtSec(job.estimate_sec)}</span> : null}
                      </div>
                      {job.status === "running" ? (
                        <div className="h-1 overflow-hidden rounded-full bg-muted">
                          <div className="h-full bg-primary" style={{ width: `${progress(job)}%` }} />
                        </div>
                      ) : null}
                      {job.error ? (
                        <div className="text-[10.5px] text-destructive">{job.error}</div>
                      ) : null}
                      <div className="flex flex-wrap items-center gap-1">
                        {job.status === "queued" || job.status === "running" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-[11px]"
                            onClick={() =>
                              act(`cancel:${job.id}`, "已取消", () =>
                                hubPost(`/v1/companion/read/jobs/${encodeURIComponent(job.id)}/cancel`, {}),
                              )
                            }
                          >
                            取消
                          </Button>
                        ) : null}
                        {job.status === "done" && job.audio_id ? (
                          <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" asChild>
                            <a
                              href={hubFile(
                                `/v1/companion/audio/${encodeURIComponent(job.audio_id)}?download=1`,
                              )}
                            >
                              下载
                            </a>
                          </Button>
                        ) : null}
                        {job.status !== "queued" && job.status !== "running" ? (
                          <>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-6 px-2 text-[11px]"
                              onClick={() =>
                                act(`replay:${job.id}`, "正在再送到书桌", () =>
                                  hubPost(`/v1/companion/read/jobs/${encodeURIComponent(job.id)}/replay`, {}),
                                )
                              }
                            >
                              <RotateCw />
                              再播
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-6 px-2 text-[11px] text-muted-foreground hover:text-destructive"
                              onClick={() =>
                                act(`delete:${job.id}`, "已从列表拿掉", () =>
                                  hubPost(`/v1/companion/read/jobs/${encodeURIComponent(job.id)}/delete`, {}),
                                )
                              }
                            >
                              <Trash2 />
                              删除
                            </Button>
                          </>
                        ) : null}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </Card>
        </div>
      )}

      <Dialog open={tocOpen} onOpenChange={setTocOpen}>
        <DialogContent className="max-w-[520px]">
          <DialogHeader>
            <DialogTitle>目录</DialogTitle>
            <DialogDescription>
              {detail ? `${detail.title} · 共 ${detail.chapters.length} 章` : "正在读书架…"}
            </DialogDescription>
          </DialogHeader>
          <Input
            value={tocFilter}
            onChange={(event) => setTocFilter(event.target.value)}
            placeholder="搜章节名"
            className="h-8 text-[12.5px]"
          />
          <div className="scroll-thin max-h-[52vh] divide-y divide-border/60 overflow-y-auto rounded-lg border">
            {filtered.map((chapter) => (
              <button
                key={chapter.index}
                type="button"
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12.5px] transition-colors hover:bg-muted/70"
                onClick={() => {
                  setTocOpen(false);
                  act(`jump:${chapter.index}`, `跳到 ${chapter.title || `第 ${chapter.index + 1} 章`}`, () =>
                    hubPost(`/v1/companion/books/${encodeURIComponent(current)}/play`, {
                      offset: chapter.start,
                    }),
                  );
                }}
              >
                <span className="w-10 shrink-0 text-[11px] text-muted-foreground tabular-nums">
                  {chapter.index + 1}
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {chapter.volume ? `${chapter.volume} · ` : ""}
                  {chapter.title || `第 ${chapter.index + 1} 章`}
                </span>
              </button>
            ))}
            {!filtered.length ? (
              <div className="px-3 py-8 text-center text-[12px] text-muted-foreground">
                {chapters.length ? "没搜到这一章。" : "还在读书架…"}
              </div>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

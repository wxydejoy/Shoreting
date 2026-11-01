import { useState } from "react";
import { Brain, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader } from "@/components/page";
import { HubNotice } from "@/pages/hub-notice";
import { hubDelete, useHub } from "@/lib/hub";

type MemoryEntry = {
  key: string;
  value: string;
  source?: string;
  learned_at?: string;
};

type MemoryData = { items: MemoryEntry[]; limit: number };

function sourceLabel(source?: string): string {
  if (source === "diary") return "她记下的";
  if (source === "teach") return "你教的";
  return "聊天里记的";
}

function sourceClass(source?: string): string {
  if (source === "diary") return "border-primary/25 bg-primary/8 text-primary";
  if (source === "teach") return "border-amber-500/30 bg-amber-500/8 text-amber-600 dark:text-amber-400";
  return "border-border bg-muted/60 text-muted-foreground";
}

function Entry({ item, onDeleted }: { item: MemoryEntry; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);
  const [failed, setFailed] = useState("");

  const remove = async () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    setFailed("");
    try {
      await hubDelete(`/v1/companion/memory/${encodeURIComponent(item.key)}`);
      onDeleted();
    } catch (error) {
      setFailed(error instanceof Error ? error.message : "没删掉");
      setBusy(false);
      setArmed(false);
    }
  };

  return (
    <article className="rounded-xl border border-border/70 bg-card px-3.5 py-3 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-medium">{item.key}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10.5px] ${sourceClass(item.source)}`}>
              {sourceLabel(item.source)}
            </span>
          </div>
          <p className="mt-1 text-[13px] leading-relaxed text-foreground/90">{item.value}</p>
          {item.learned_at ? (
            <div className="mt-1.5 text-[10.5px] text-muted-foreground tabular-nums">
              {item.learned_at} 记下的
            </div>
          ) : null}
          {failed ? <div className="mt-1.5 text-[11.5px] text-destructive">{failed}</div> : null}
        </div>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={`忘掉「${item.key}」`}
          disabled={busy}
          onClick={remove}
          className={armed ? "text-destructive hover:text-destructive" : "text-muted-foreground"}
        >
          <Trash2 />
        </Button>
      </div>
    </article>
  );
}

export function MemoryPage() {
  const { data, error, loading, reload } = useHub<MemoryData>("/v1/companion/memory", 0);
  const items = data?.items ?? [];
  const limit = data?.limit ?? 0;

  if (error && !data) {
    return (
      <div className="space-y-5">
        <PageHeader title="记忆" subtitle="她记得关于你的事。" />
        <HubNotice message={error} onRetry={reload} />
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-2">
      <PageHeader
        title="记忆"
        subtitle="她记得关于你的事，和每晚日记里攒下的对你的印象。对话时会自然用上。"
        actions={
          <span className="text-[11.5px] text-muted-foreground tabular-nums">
            {loading && !items.length ? "读着呢…" : `${items.length}${limit ? ` / ${limit}` : ""} 条`}
          </span>
        }
      />

      {!items.length ? (
        <EmptyState
          icon={<Brain className="size-6" />}
          title={loading ? "读着呢…" : "脑子里还空着"}
          description="聊天里说「记住……」她会记一条；每晚写完日记，也会更新一条对你的印象。"
        />
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <Entry key={item.key} item={item} onDeleted={reload} />
          ))}
        </div>
      )}
    </div>
  );
}

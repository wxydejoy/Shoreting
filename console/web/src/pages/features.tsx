import { useEffect, useMemo, useState } from "react";
import { Loader2, Play, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { EmptyState, PageHeader } from "@/components/page";
import { saveFeature, servicesAction, type Feature } from "@/lib/api";
import { useConsole } from "@/lib/console-store";
import { navigate } from "@/lib/route";
import { cn } from "@/lib/utils";

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function asQuiet(value: unknown): [string, string] {
  if (Array.isArray(value) && value.length >= 2) return [String(value[0] ?? ""), String(value[1] ?? "")];
  return ["", ""];
}

function FeatureRow({ feature }: { feature: Feature }) {
  const [saving, setSaving] = useState(false);
  const [value, setValue] = useState<unknown>(feature.value);
  const [draft, setDraft] = useState<string>(asText(feature.value));
  const [quiet, setQuiet] = useState<[string, string]>(asQuiet(feature.value));

  useEffect(() => {
    setValue(feature.value);
    setDraft(asText(feature.value));
    setQuiet(asQuiet(feature.value));
  }, [feature.key, feature.value]);

  const commit = async (next: unknown) => {
    setSaving(true);
    try {
      const result = await saveFeature(feature.key, next);
      if (result.ok) {
        setValue(result.value ?? next);
        setDraft(asText(result.value ?? next));
        toast.success(result.message || "已生效");
      } else {
        toast.error(result.message || "没成功");
        setValue(feature.value);
        setDraft(asText(feature.value));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
      setValue(feature.value);
      setDraft(asText(feature.value));
    } finally {
      setSaving(false);
    }
  };

  const disabled = saving;
  const numberStep = feature.step ?? 1;

  let control: React.ReactNode = null;

  if (feature.kind === "bool") {
    control = (
      <Switch
        checked={Boolean(value)}
        disabled={disabled}
        onCheckedChange={(checked) => {
          setValue(checked);
          void commit(checked);
        }}
      />
    );
  } else if (feature.kind === "int" || feature.kind === "number") {
    control = (
      <div className="flex items-center gap-1.5">
        <Input
          type="number"
          value={draft}
          disabled={disabled}
          min={feature.low ?? undefined}
          max={feature.high ?? undefined}
          step={numberStep}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => {
            if (draft !== asText(value)) void commit(draft);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              event.currentTarget.blur();
            }
          }}
          className="h-8 w-24 text-right font-mono text-[12.5px] tabular-nums"
        />
        {feature.unit ? (
          <span className="w-6 text-[11.5px] text-muted-foreground">{feature.unit}</span>
        ) : null}
      </div>
    );
  } else if (feature.kind === "time") {
    control = (
      <div className="flex items-center gap-1.5">
        <Input
          type="time"
          value={draft}
          disabled={disabled}
          onChange={(event) => {
            setDraft(event.target.value);
            void commit(event.target.value);
          }}
          className="h-8 w-[132px] font-mono text-[12.5px]"
        />
        {!draft && feature.optional ? (
          <span className="text-[11.5px] text-muted-foreground">关着</span>
        ) : null}
      </div>
    );
  } else if (feature.kind === "quiet") {
    control = (
      <div className="flex items-center gap-1.5">
        <Input
          type="time"
          value={quiet[0]}
          disabled={disabled}
          onChange={(event) => {
            const next: [string, string] = [event.target.value, quiet[1]];
            setQuiet(next);
            void commit(next);
          }}
          className="h-8 w-[120px] font-mono text-[12.5px]"
        />
        <span className="text-[11.5px] text-muted-foreground">到</span>
        <Input
          type="time"
          value={quiet[1]}
          disabled={disabled}
          onChange={(event) => {
            const next: [string, string] = [quiet[0], event.target.value];
            setQuiet(next);
            void commit(next);
          }}
          className="h-8 w-[120px] font-mono text-[12.5px]"
        />
      </div>
    );
  } else if (feature.kind === "device" || feature.kind === "scene") {
    const options = feature.options ?? [];
    // Radix 的 Item 不认空字符串，用哨兵值来回换一下，空值才显示得出来
    const NONE = "__none__";
    const current = asText(value) || NONE;
    control = (
      <Select
        value={current}
        disabled={disabled}
        onValueChange={(next) => {
          const actual = next === NONE ? "" : next;
          setValue(actual);
          void commit(actual);
        }}
      >
        <SelectTrigger size="sm" className="h-8 min-w-[170px] text-[12.5px]">
          <SelectValue placeholder="挑一个" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem
              key={option.value || NONE}
              value={option.value || NONE}
              className="text-[12.5px]"
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  } else {
    control = (
      <Input
        value={draft}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (draft !== asText(value)) void commit(draft);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            event.currentTarget.blur();
          }
        }}
        className="h-8 w-[240px] text-[12.5px]"
      />
    );
  }

  return (
    <div
      className={cn(
        "flex flex-col gap-2 px-4 py-3 transition-opacity sm:flex-row sm:items-center sm:gap-6",
        saving && "opacity-60",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-[13px] font-medium">
          {feature.label}
          {saving ? <Loader2 className="size-3 animate-spin text-muted-foreground" /> : null}
        </div>
        {feature.hint ? (
          <p className="mt-0.5 text-[11.5px] leading-relaxed text-muted-foreground">
            {feature.hint}
          </p>
        ) : null}
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

export function FeaturesPage({ group }: { group: string }) {
  const { catalog, catalogError, loadCatalog, run, busy } = useConsole();
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const groups = catalog?.groups ?? [];
  const features = catalog?.features ?? [];
  const activeGroup = groups.find((item) => item.id === group) ?? groups[0];
  const hubDown = !catalog?.ok;

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle) {
      return features.filter((feature) =>
        `${feature.label} ${feature.hint ?? ""} ${feature.key}`
          .toLowerCase()
          .includes(needle),
      );
    }
    if (!activeGroup) return [];
    return features.filter((feature) => feature.group === activeGroup.id);
  }, [features, query, activeGroup]);

  const enabledCount = features.filter((feature) => feature.kind === "bool" && feature.value).length;
  const boolCount = features.filter((feature) => feature.kind === "bool").length;
  const groupBools = visible.filter((feature) => feature.kind === "bool");
  const groupOn = groupBools.filter((feature) => feature.value).length;

  const grouped = useMemo(() => {
    if (!query.trim()) return null;
    const map = new Map<string, Feature[]>();
    for (const feature of visible) {
      const list = map.get(feature.group) ?? [];
      list.push(feature);
      map.set(feature.group, list);
    }
    return groups
      .map((item) => ({ group: item, rows: map.get(item.id) ?? [] }))
      .filter((entry) => entry.rows.length > 0);
  }, [visible, query, groups]);

  const refreshCatalog = async () => {
    setRefreshing(true);
    await loadCatalog();
    setRefreshing(false);
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="功能开关"
        subtitle={
          <>
            这些段落以前只能手写 <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11.5px]">hub.yaml</code>
            。改一下立刻生效，不用重启 Hub，注释和顺序都留着。
          </>
        }
        actions={
          <>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜开关…"
                className="h-8 w-[190px] pl-8 text-[12.5px]"
              />
            </div>
            <Button size="sm" variant="outline" onClick={() => void refreshCatalog()} disabled={refreshing}>
              <RefreshCw className={refreshing ? "animate-spin" : undefined} />
              重新读
            </Button>
          </>
        }
      />

      {hubDown ? (
        <Card className="gap-0 border-warn/35 bg-warn/8 py-0">
          <div className="flex flex-wrap items-center gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="text-[12.5px] font-medium text-warn">Hub 没在跑，开关读不到</div>
              <div className="mt-0.5 text-[11.5px] text-muted-foreground">
                {catalogError || catalog?.message || "先把它启动起来，这一页才有东西可改。"}
              </div>
            </div>
            <Button
              size="sm"
              onClick={() => run("hub:start", () => servicesAction("hub", "start"))}
              disabled={Boolean(busy)}
            >
              <Play />
              启动 Hub
            </Button>
          </div>
        </Card>
      ) : null}

      {!hubDown ? (
        <>
          <div className="flex flex-wrap gap-1.5">
            {groups.map((item) => {
              const on = !query.trim() && item.id === activeGroup?.id;
              return (
                <Button
                  key={item.id}
                  size="sm"
                  variant={on ? "secondary" : "ghost"}
                  onClick={() => {
                    setQuery("");
                    navigate({ kind: "features", group: item.id });
                  }}
                >
                  {item.title}
                </Button>
              );
            })}
          </div>

          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <p className="min-w-0 flex-1 text-[11.5px] leading-relaxed text-muted-foreground">
              {activeGroup?.blurb}
            </p>
            <span className="shrink-0 text-[11.5px] text-muted-foreground tabular-nums">
              {query.trim()
                ? `${visible.length} 项匹配`
                : `${visible.length} 项 · ${groupOn}/${groupBools.length} 个开关开着`}
            </span>
          </div>

          {query.trim() ? (
            <div className="space-y-5">
              {grouped?.map((entry) => (
                <div key={entry.group.id}>
                  <div className="mb-1.5 px-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                    {entry.group.title}
                  </div>
                  <Card className="gap-0 divide-y divide-border/70 py-0">
                    {entry.rows.map((feature) => (
                      <FeatureRow key={feature.key} feature={feature} />
                    ))}
                  </Card>
                </div>
              ))}
              {grouped?.length === 0 ? (
                <EmptyState
                  icon={<Search className="size-6" />}
                  title="没搜到"
                  description={`没有哪个开关的名字或说明里有「${query.trim()}」。`}
                />
              ) : null}
            </div>
          ) : (
            <>
              <Card className="gap-0 divide-y divide-border/70 py-0">
                {visible.map((feature) => (
                  <FeatureRow key={feature.key} feature={feature} />
                ))}
              </Card>

              {visible.length === 0 ? (
                <EmptyState
                  icon={<SlidersHorizontal className="size-6" />}
                  title="这组还没有开关"
                  description="Hub 那边没给这一组返回任何项。换一组看看。"
                />
              ) : null}
            </>
          )}

          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            一共 {features.length} 项，其中 {enabledCount}/{boolCount} 个开关开着。改的是{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
              {catalog?.config_path || "hub.yaml"}
            </code>
            ，Hub 立刻热加载。
          </p>
        </>
      ) : (
        <EmptyState
          icon={<SlidersHorizontal className="size-6" />}
          title="开关清单在这里"
          description="Hub 一起来，这一页就会出现六组、四十多项。"
          action={
            <Button
              size="sm"
              variant="outline"
              onClick={() => void loadCatalog()}
            >
              <RefreshCw />
              再读一次
            </Button>
          }
        />
      )}
    </div>
  );
}

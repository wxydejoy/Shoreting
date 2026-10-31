import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Loader2, RotateCcw, Scan, Save } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { hubPost } from "@/lib/hub";
import {
  fetchPhoneLayout,
  type PhoneLayoutRect,
  type PhoneLayoutSnapshot,
} from "@/lib/api";

/**
 * 主屏布局编辑器：一块按手机画布比例的板子，模块卡片直接拖。
 *
 * 坐标全程用 0..1 的比例（和手机端 DockLayout 一致），显示时乘画布尺寸。
 * 手机通过 /api/phone/layout 报现状（画布、槽位、默认布局、已存的覆盖）；
 * 保存时只提交和默认值不一样的槽位——没动过的槽位继续跟手机端的默认走，
 * 这样手机端将来调默认布局时，网页这边存过的旧值不会把新默认压住。
 */

const EPS = 1e-4;
const MIN_SIZE = 0.08;

type Draft = Record<string, PhoneLayoutRect>;

function sameRect(a: PhoneLayoutRect | undefined, b: PhoneLayoutRect | undefined): boolean {
  if (!a || !b) return false;
  return (
    Math.abs(a.x - b.x) < EPS &&
    Math.abs(a.y - b.y) < EPS &&
    Math.abs(a.w - b.w) < EPS &&
    Math.abs(a.h - b.h) < EPS &&
    a.visible === b.visible &&
    a.chrome === b.chrome
  );
}

/** 存的占位矩形（w/h≈0）显示成默认位置——显隐标记还在。 */
function viewRect(id: string, draft: Draft, defaults: Draft): PhoneLayoutRect {
  const d = draft[id];
  const def = defaults[id];
  if (!d) return def ?? { x: 0, y: 0, w: 0.2, h: 0.15, visible: true, chrome: true };
  if (d.w < 0.05 && def) return { ...def, visible: d.visible, chrome: d.chrome };
  return d;
}

export function PhoneLayoutEditor() {
  const [snap, setSnap] = useState<PhoneLayoutSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<
    | null
    | {
        id: string;
        mode: "move" | "resize";
        px: number;
        py: number;
        base: PhoneLayoutRect;
      }
  >(null);

  const load = useCallback(async (quiet = false) => {
    setLoading(true);
    try {
      const next = await fetchPhoneLayout();
      setSnap(next);
      const defaults = next.defaults ?? {};
      // 草稿 = 手机现在的样子（modules 是合并过的）；缺的槽位补默认值
      const merged: Draft = {};
      for (const slot of next.slots ?? []) {
        merged[slot.id] = next.modules?.[slot.id] ?? defaults[slot.id] ?? {
          x: 0.1, y: 0.1, w: 0.2, h: 0.15, visible: true, chrome: true,
        };
      }
      setDraft(merged);
      setSelected(null);
      setError(null);
      if (!quiet) toast.success("已读取手机布局");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      if (!quiet) toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
  }, [load]);

  const defaults = snap?.defaults ?? {};

  const beginDrag = (id: string, mode: "move" | "resize") => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    const base = { ...viewRect(id, draft, defaults) };
    if (base.w < MIN_SIZE) base.w = MIN_SIZE;
    if (base.h < MIN_SIZE) base.h = MIN_SIZE;
    drag.current = { id, mode, px: e.clientX, py: e.clientY, base };
    setSelected(id);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    const box = canvasRef.current?.getBoundingClientRect();
    if (!d || !box) return;
    const dx = (e.clientX - d.px) / box.width;
    const dy = (e.clientY - d.py) / box.height;
    setDraft((prev) => {
      const base = d.base;
      if (d.mode === "move") {
        return {
          ...prev,
          [d.id]: {
            ...base,
            x: Math.min(Math.max(base.x + dx, 0), 1 - base.w),
            y: Math.min(Math.max(base.y + dy, 0), 1 - base.h),
          },
        };
      }
      return {
        ...prev,
        [d.id]: {
          ...base,
          w: Math.min(Math.max(base.w + dx, MIN_SIZE), 1 - base.x),
          h: Math.min(Math.max(base.h + dy, MIN_SIZE), 1 - base.y),
        },
      };
    });
  };

  const endDrag = () => {
    drag.current = null;
  };

  const toggle = (key: "visible" | "chrome") => {
    if (!selected) return;
    setDraft((prev) => {
      const current = viewRect(selected, prev, defaults);
      return { ...prev, [selected]: { ...current, [key]: !current[key] } };
    });
  };

  const resetDraft = () => {
    setDraft({ ...defaults });
    setSelected(null);
  };

  const save = async () => {
    if (!snap) return;
    setSaving(true);
    try {
      const overrides: Draft = {};
      for (const [id, rect] of Object.entries(draft)) {
        if (sameRect(rect, defaults[id])) continue; // 没动过的槽位不存
        const def = defaults[id];
        const posSame =
          def &&
          Math.abs(rect.x - def.x) < EPS &&
          Math.abs(rect.y - def.y) < EPS &&
          Math.abs(rect.w - def.w) < EPS &&
          Math.abs(rect.h - def.h) < EPS;
        // 位置和默认一样、只是显隐变了 → 存占位矩形，手机端好认
        overrides[id] = posSame
          ? { x: 0, y: 0, w: 0, h: 0, visible: rect.visible, chrome: rect.chrome }
          : { ...rect };
      }
      const result = await hubPost<{ ok?: boolean; message?: string }>("/setup/api/phone", {
        phone: { layout: { modules: overrides } },
      });
      toast.success(result.message || "布局已保存，手机上几秒内会变");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const selectedRect = selected ? viewRect(selected, draft, defaults) : null;
  const labelOf = (id: string) => snap?.slots?.find((s) => s.id === id)?.label ?? id;
  const canvasW = snap?.canvas?.w ?? 2230;
  const canvasH = snap?.canvas?.h ?? 1025;

  return (
    <Card className="gap-0 py-0">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 px-4 py-2.5">
        <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
          <Scan className="size-3.5 text-muted-foreground" />
          主屏画布（{canvasW}×{canvasH}，和手机同比例）
        </span>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" disabled={loading} onClick={() => void load()}>
            {loading ? <Loader2 className="animate-spin" /> : <RotateCcw />}
            重读
          </Button>
          <Button size="sm" variant="outline" disabled={saving || !snap} onClick={resetDraft}>
            恢复默认
          </Button>
          <Button size="sm" variant="outline" disabled={saving || !snap} onClick={() => void save()}>
            {saving ? <Loader2 className="animate-spin" /> : <Save />}
            保存
          </Button>
        </div>
      </div>

      {error ? (
        <div className="px-4 py-6 text-[12px] leading-relaxed text-muted-foreground">
          读不到手机布局：{error}
          <br />
          读取和保存都要手机上的岸亭在新版本（带布局接口的 APK）；先把手机更新了再回来。
        </div>
      ) : snap ? (
        <div className="space-y-3 p-4">
          <div
            className="relative mx-auto w-full overflow-hidden rounded-xl border bg-muted/30 select-none"
            style={{
              aspectRatio: `${canvasW} / ${canvasH}`,
              maxWidth: `calc(76vh * ${canvasW / canvasH})`,
            }}
            ref={canvasRef}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
          >
            {(snap.slots ?? []).map((slot) => {
              const rect = viewRect(slot.id, draft, defaults);
              const active = selected === slot.id;
              return (
                <div
                  key={slot.id}
                  onPointerDown={beginDrag(slot.id, "move")}
                  className={[
                    "absolute flex items-center justify-center overflow-hidden rounded-lg border leading-tight",
                    rect.visible
                      ? "border-foreground/25 bg-background/80 text-foreground/80"
                      : "border-dashed border-foreground/20 bg-transparent text-muted-foreground/60",
                    active ? "border-primary ring-1.5 ring-primary/60 z-10" : "",
                  ].join(" ")}
                  style={{
                    left: `${rect.x * 100}%`,
                    top: `${rect.y * 100}%`,
                    width: `${rect.w * 100}%`,
                    height: `${rect.h * 100}%`,
                    opacity: drag.current?.id === slot.id ? 0.75 : 1,
                  }}
                >
                  <span className="px-1.5 text-center text-[12px]">
                    {slot.label}
                    {!rect.visible ? " · 隐" : !rect.chrome ? " · 裸" : ""}
                  </span>
                  {active ? (
                    <span
                      onPointerDown={beginDrag(slot.id, "resize")}
                      className="absolute -right-0 -bottom-0 size-4 cursor-nwse-resize rounded-tl-lg bg-primary/70"
                    />
                  ) : null}
                </div>
              );
            })}
          </div>

          <div className="min-w-0 space-y-2.5">
            <div className="flex flex-wrap gap-1.5">
              {(snap.slots ?? []).map((slot) => {
                const rect = viewRect(slot.id, draft, defaults);
                return (
                  <button
                    key={slot.id}
                    type="button"
                    onClick={() => setSelected(slot.id === selected ? null : slot.id)}
                    className={[
                      "rounded-full border px-2.5 py-1 text-[11.5px] transition-colors",
                      slot.id === selected
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border/60 text-muted-foreground hover:bg-muted",
                      !rect.visible ? "opacity-55" : "",
                    ].join(" ")}
                  >
                    {slot.label}
                    {!rect.visible ? "（隐）" : ""}
                  </button>
                );
              })}
            </div>
            {selected && selectedRect ? (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-[12px]">
                <span className="font-medium">{labelOf(selected)}</span>
                <Badge variant="outline" className="text-[10.5px]">
                  {Math.round(selectedRect.w * 100)}×{Math.round(selectedRect.h * 100)}%
                </Badge>
                <Button size="sm" variant={selectedRect.visible ? "outline" : "ghost"} onClick={() => toggle("visible")}>
                  {selectedRect.visible ? <Check /> : null}
                  {selectedRect.visible ? "显示中" : "已隐藏"}
                </Button>
                <Button size="sm" variant={selectedRect.chrome ? "outline" : "ghost"} onClick={() => toggle("chrome")}>
                  {selectedRect.chrome ? <Check /> : null}
                  边框
                </Button>
                <span className="text-[11px] text-muted-foreground">拖卡片移动，拖右下角缩放</span>
              </div>
            ) : (
              <p className="text-[11.5px] leading-relaxed text-muted-foreground">
                点一个磁贴选中，拖动移动、拖右下角缩放；「边框」关掉就是手机上那种没有底卡的裸样式。
                保存只提交动过的磁贴，其余继续跟手机端的默认布局走。
              </p>
            )}
          </div>
        </div>
      ) : (
        <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">正在读手机……</div>
      )}
    </Card>
  );
}

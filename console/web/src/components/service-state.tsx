import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { Service } from "@/lib/api";

const TONE: Record<string, { text: string; bg: string; dot: string }> = {
  ok: {
    text: "text-ok",
    bg: "bg-ok/10 border-ok/25",
    dot: "bg-ok",
  },
  warn: {
    text: "text-warn",
    bg: "bg-warn/12 border-warn/30",
    dot: "bg-warn",
  },
  idle: {
    text: "text-muted-foreground",
    bg: "bg-muted border-border",
    dot: "bg-idle",
  },
};

export function toneOfLabel(label: string): "ok" | "warn" | "idle" {
  if (label === "运行中") return "ok";
  if (label === "进程在，端口未就绪" || label === "未安装") return "warn";
  return "idle";
}

export function StateBadge({
  label,
  tone,
  className,
}: {
  label: string;
  tone?: "ok" | "warn" | "idle";
  className?: string;
}) {
  const colors = TONE[tone ?? toneOfLabel(label)];
  return (
    <Badge
      variant="outline"
      className={cn("gap-1.5 rounded-full font-medium", colors.bg, colors.text, className)}
    >
      <span className={cn("size-1.5 rounded-full", colors.dot)} />
      {label}
    </Badge>
  );
}

export function ServiceDot({ service, className }: { service: Service; className?: string }) {
  const colors = TONE[toneOfLabel(service.state_label)];
  return (
    <span
      title={service.state_label}
      className={cn("size-1.5 shrink-0 rounded-full", colors.dot, className)}
    />
  );
}

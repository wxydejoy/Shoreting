import { ChevronRight } from "lucide-react";

import { PageHeader } from "@/components/page";
import { PowerActions } from "@/components/service-actions";
import { ServiceDot, StateBadge } from "@/components/service-state";
import { useConsole } from "@/lib/console-store";
import { hrefOf } from "@/lib/route";

export function ServicesPage() {
  const { services } = useConsole();

  return (
    <div className="space-y-5">
      <PageHeader
        title="服务"
        subtitle="这台 Mac 上的三个常驻进程。点进去看日志和端口。"
        actions={<PowerActions />}
      />

      <div className="overflow-hidden rounded-xl border bg-card">
        {services.map((service, index) => (
          <a
            key={service.id}
            href={hrefOf({ kind: "service", id: service.id, tab: "status" })}
            className={
              index > 0
                ? "flex items-center gap-3 border-t border-border/70 px-4 py-3 transition-colors hover:bg-accent/50"
                : "flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/50"
            }
          >
            <ServiceDot service={service} />
            <span className="min-w-0 flex-1">
              <span className="block text-[13.5px] font-medium">{service.name}</span>
              <span className="block truncate text-[12px] text-muted-foreground">{service.blurb}</span>
            </span>
            <StateBadge label={service.state_label} />
            <span className="hidden font-mono text-[11px] text-muted-foreground sm:inline">
              :{service.port}
            </span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground/70" />
          </a>
        ))}
        {!services.length ? (
          <div className="px-4 py-10 text-center text-[12.5px] text-muted-foreground">
            还没读到服务列表。
          </div>
        ) : null}
      </div>

      <p className="text-[12.5px] text-muted-foreground">
        Windows 不在这台机器上跑。
        <a
          href={hrefOf({ kind: "hub", page: "setup", tab: "win" })}
          className="ml-1 underline decoration-dotted underline-offset-2 hover:text-foreground"
        >
          探针在配置里
        </a>
        。
      </p>
    </div>
  );
}

import { Play, RefreshCw, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/page";
import { servicesAction } from "@/lib/api";
import { useConsole } from "@/lib/console-store";

/** Hub 的数据拉不下来时统一出这个：说清为什么，顺手给个启动按钮。 */
export function HubNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  const { services, run, busy } = useConsole();
  const hub = services.find((service) => service.id === "hub");
  const down = !hub?.health_ok;

  return (
    <EmptyState
      icon={<TriangleAlert className="size-6" />}
      title={down ? "Hub 没在跑" : "这一页没读出来"}
      description={message}
      action={
        <div className="flex items-center gap-2">
          {down ? (
            <Button
              size="sm"
              disabled={Boolean(busy)}
              onClick={() => run("hub:start", () => servicesAction("hub", "start"))}
            >
              <Play />
              启动 Hub
            </Button>
          ) : null}
          {onRetry ? (
            <Button size="sm" variant="outline" onClick={onRetry}>
              <RefreshCw />
              再试一次
            </Button>
          ) : null}
        </div>
      }
    />
  );
}

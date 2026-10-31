import { useState } from "react";
import { Loader2, Play, RotateCw, Square, Power } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { allAction, servicesAction, type Service } from "@/lib/api";
import { useConsole } from "@/lib/console-store";

export function ServiceActions({
  service,
  compact = false,
}: {
  service: Service;
  compact?: boolean;
}) {
  const { run, busy } = useConsole();
  const disabled = Boolean(busy);
  const size = compact ? "sm" : "default";
  const key = (action: string) => `${service.id}:${action}`;
  const running = service.state_label === "运行中";

  return (
    <>
      <Button
        size={size}
        variant={running ? "outline" : "default"}
        disabled={disabled || running}
        onClick={() => run(key("start"), () => servicesAction(service.id, "start"))}
      >
        {busy === key("start") ? <Loader2 className="animate-spin" /> : <Play />}
        启动
      </Button>
      <Button
        size={size}
        variant={running ? "default" : "outline"}
        disabled={disabled}
        onClick={() => run(key("restart"), () => servicesAction(service.id, "restart"))}
      >
        {busy === key("restart") ? <Loader2 className="animate-spin" /> : <RotateCw />}
        重启
      </Button>
      <Button
        size={size}
        variant="outline"
        disabled={disabled}
        className="text-muted-foreground hover:border-destructive/40 hover:bg-destructive/8 hover:text-destructive"
        onClick={() => run(key("stop"), () => servicesAction(service.id, "stop"))}
      >
        {busy === key("stop") ? <Loader2 className="animate-spin" /> : <Square />}
        关闭
      </Button>
    </>
  );
}

export function PowerActions() {
  const { run, busy } = useConsole();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const disabled = Boolean(busy);

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => run("all:start", () => allAction("start"))}
      >
        {busy === "all:start" ? <Loader2 className="animate-spin" /> : <Play />}
        全部启动
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => run("all:restart", () => allAction("restart"))}
      >
        {busy === "all:restart" ? <Loader2 className="animate-spin" /> : <RotateCw />}
        全部重启
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={disabled}
        className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
        onClick={() => setConfirmOpen(true)}
      >
        <Power />
        全部关闭
      </Button>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>把三个服务都关掉？</DialogTitle>
            <DialogDescription>
              手机会立刻失去连接，守岸人也不再出声。这次登录里它们不会自己起来，下次登录仍会开机启动。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setConfirmOpen(false)}>
              算了
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => {
                setConfirmOpen(false);
                void run("all:stop", () => allAction("stop"));
              }}
            >
              全部关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

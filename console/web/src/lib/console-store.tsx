import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { toast } from "sonner";

import {
  fetchCatalog,
  fetchStatus,
  type Catalog,
  type Result,
  type Service,
} from "@/lib/api";

type ConsoleState = {
  services: Service[];
  updatedAt: number | null;
  error: string | null;
  busy: string | null;
  refresh: () => Promise<void>;
  run: (key: string, task: () => Promise<Result>) => Promise<Result | null>;
  catalog: Catalog | null;
  catalogError: string | null;
  loadCatalog: () => Promise<Catalog | null>;
};

const ConsoleContext = createContext<ConsoleState | null>(null);

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [services, setServices] = useState<Service[]>([]);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const busyRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const rows = await fetchStatus();
      setServices(rows);
      setUpdatedAt(Date.now());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadCatalog = useCallback(async () => {
    try {
      const data = await fetchCatalog();
      setCatalog(data);
      setCatalogError(data.ok ? null : (data.message ?? "Hub 没有返回开关清单"));
      return data;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCatalogError(message);
      return null;
    }
  }, []);

  const run = useCallback(
    async (key: string, task: () => Promise<Result>) => {
      busyRef.current = true;
      setBusy(key);
      try {
        const result = await task();
        if (result.ok) toast.success(result.message || "完成");
        else toast.error(result.message || "没成功");
        return result;
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
        return null;
      } finally {
        busyRef.current = false;
        setBusy(null);
        void refresh();
      }
    },
    [refresh],
  );

  useEffect(() => {
    void refresh();
    void loadCatalog();
    const timer = window.setInterval(() => {
      if (document.hidden || busyRef.current) return;
      void refresh();
    }, 2500);
    const onVisible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh, loadCatalog]);

  const value = useMemo<ConsoleState>(
    () => ({
      services,
      updatedAt,
      error,
      busy,
      refresh,
      run,
      catalog,
      catalogError,
      loadCatalog,
    }),
    [services, updatedAt, error, busy, refresh, run, catalog, catalogError, loadCatalog],
  );

  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}

export function useConsole(): ConsoleState {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole 必须在 ConsoleProvider 里用");
  return value;
}

export function useService(id: string): Service | undefined {
  const { services } = useConsole();
  return services.find((service) => service.id === id);
}

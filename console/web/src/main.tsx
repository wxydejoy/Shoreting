import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "next-themes";

import "./index.css";
import App from "@/app";
import { ConsoleProvider } from "@/lib/console-store";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

// 老书签是 /features（菜单栏里也是），把它接到同一套导航上。
if (
  (window.location.pathname === "/features" || window.location.pathname === "/features/") &&
  !window.location.hash
) {
  window.history.replaceState(null, "", "/#/features");
}

const root = document.getElementById("root");
if (!root) throw new Error("找不到 #root");

createRoot(root).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <TooltipProvider delayDuration={280}>
        <ConsoleProvider>
          <App />
          <Toaster position="bottom-right" offset={16} />
        </ConsoleProvider>
      </TooltipProvider>
    </ThemeProvider>
  </StrictMode>,
);

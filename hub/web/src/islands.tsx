import { useEffect, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { BounceSidebar } from "@/components/ui/bounce-sidebar";
import { DurationPicker, type DurationValue } from "@/components/ui/duration-picker";
import { GooeyNav } from "@/components/ui/gooey-nav";
import { PasswordInput } from "@/components/ui/password-input";
import { VoiceNote } from "@/components/ui/voice-note";

type Chapter = { index: number; title?: string; start: number };
type Story = { playing?: boolean } | null;

declare global {
  interface Window {
    showView?: (name: string) => void;
    storyPlay?: (offset: number | null) => void;
    stopAll?: () => void;
    jumpChapter?: (start: number, index: number) => void;
    shoreChapters?: () => Chapter[];
    shoreActiveChapter?: () => number;
    shoreStory?: () => Story;
  }
}

const VIEWS = ["read", "chats", "setup"] as const;
const ALLOWED = [0, 15, 30, 45, 60];

function viewName() {
  const view = document.documentElement.dataset.view;
  return VIEWS.includes(view as (typeof VIEWS)[number]) ? (view as (typeof VIEWS)[number]) : "read";
}

function go(name: string) {
  const href = name === "chats" ? "/chats" : name === "setup" ? "/setup" : "/read";
  history.pushState(null, "", href);
  window.showView?.(name);
}

function minutesOf(value: DurationValue) {
  if (value.hours >= 1) return 60;
  const total = value.hours * 60 + value.minutes;
  return ALLOWED.reduce((best, item) => (Math.abs(item - total) < Math.abs(best - total) ? item : best));
}

function durationOf(minutes: number): DurationValue {
  const snapped = minutesOf({ hours: 0, minutes });
  return { hours: snapped >= 60 ? 1 : 0, minutes: snapped >= 60 ? 0 : snapped };
}

function usePulse() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((n) => n + 1);
    document.addEventListener("shore-show", bump);
    document.addEventListener("shore-story", bump);
    document.addEventListener("shore-shelf", bump);
    document.addEventListener("shore-timer", bump);
    document.getElementById("tocQuery")?.addEventListener("input", bump);
    return () => {
      document.removeEventListener("shore-show", bump);
      document.removeEventListener("shore-story", bump);
      document.removeEventListener("shore-shelf", bump);
      document.removeEventListener("shore-timer", bump);
      document.getElementById("tocQuery")?.removeEventListener("input", bump);
    };
  }, []);
  return tick;
}

function SideNav() {
  usePulse();
  const index = VIEWS.indexOf(viewName()) + 1;
  return (
    <>
      <BounceSidebar
        items={[{ label: "应用", heading: true }, "朗读", "对话", "配置"]}
        value={index}
        onChange={(next) => {
          const name = VIEWS[next - 1];
          if (name) go(name);
        }}
      />
    </>
  );
}

function TopNav() {
  usePulse();
  return (
    <GooeyNav
      size="xs"
      items={["朗读", "对话", "配置"]}
      value={Math.max(0, VIEWS.indexOf(viewName()))}
      onChange={(index) => {
        const name = VIEWS[index];
        if (name) go(name);
      }}
    />
  );
}

function Timer() {
  usePulse();
  const select = document.getElementById("timer") as HTMLSelectElement | null;
  const minutes = Number(select?.value || 30);
  return (
    <DurationPicker
      key={minutes}
      defaultValue={durationOf(minutes)}
      maxHours={1}
      maxMinutes={59}
      hoursLabel="时"
      minutesLabel="分"
      onConfirm={(next) => {
        if (!select) return;
        select.value = String(minutesOf(next));
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }}
    />
  );
}

function Voice() {
  usePulse();
  const playing = !!window.shoreStory?.()?.playing;
  const current = Number((document.getElementById("voiceSpeed") as HTMLInputElement | null)?.value || 1);
  const speeds = [0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3, 1.4, 1.5];
  const ordered = [current, ...speeds.filter((item) => item !== current)];
  return (
    <VoiceNote
      playing={playing}
      duration={8 * 60 * 60}
      speeds={ordered}
      seekable={false}
      onPlayingChange={(next) => {
        if (next === playing) return;
        if (next) window.storyPlay?.(null);
        else window.stopAll?.();
      }}
      onSpeedChange={(speed) => {
        const input = document.getElementById("voiceSpeed") as HTMLInputElement | null;
        if (!input) return;
        input.value = String(speed);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }}
    />
  );
}

function Gate() {
  return <PasswordInput id="gateToken" strength={false} placeholder="Token" autoComplete="off" className="w-full max-w-none" />;
}

function mount(id: string, node: ReactNode, roots: Root[]) {
  const el = document.getElementById(id);
  if (!el) return;
  const root = createRoot(el);
  root.render(node);
  roots.push(root);
}

export function mountIslands() {
  const roots: Root[] = [];
  mount("rareSide", <SideNav />, roots);
  mount("rareGooey", <TopNav />, roots);
  mount("rareTimer", <Timer />, roots);
  mount("rareVoice", <Voice />, roots);
  mount("rareGate", <Gate />, roots);
}

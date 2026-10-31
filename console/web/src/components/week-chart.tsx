import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis } from "recharts";

type Day = {
  date: string;
  desk_sec: number;
  chats: number;
};

type Row = {
  label: string;
  minutes: number;
  chats: number;
  today: boolean;
};

function rowsOf(days: Day[]): Row[] {
  const today = new Date().toISOString().slice(0, 10);
  return days.map((day) => ({
    label: day.date.slice(5),
    minutes: Math.round((day.desk_sec || 0) / 60),
    chats: day.chats || 0,
    today: day.date === today,
  }));
}

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="rounded-lg border bg-popover px-2.5 py-1.5 text-[12px] shadow-sm">
      <div className="font-medium">{row.label}</div>
      <div className="text-muted-foreground">
        {row.minutes > 0 ? `在桌 ${row.minutes} 分钟 · ${row.chats} 句` : `${row.chats} 句`}
      </div>
    </div>
  );
}

/** 一周在桌分钟。整周都是 0 的时候改画当天说了几句，避免一根柱子都看不见。 */
export function WeekChart({ days, height = 192 }: { days: Day[]; height?: number }) {
  const rows = rowsOf(days);
  const byDesk = rows.some((row) => row.minutes > 0);
  const empty = rows.every((row) => row.minutes === 0 && row.chats === 0);

  if (!rows.length || empty) {
    return (
      <div
        className="flex items-center justify-center text-[12.5px] text-muted-foreground"
        style={{ height }}
      >
        这一周还没有在桌记录。
      </div>
    );
  }

  return (
    <div>
      <div className="px-3 pt-3 text-[12px] text-muted-foreground">
        {byDesk ? "在桌，按分钟。今天那根是主色。" : "这周没量到在桌时长，柱子是说了几句。"}
      </div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 4, left: 4, bottom: 0 }}>
            <XAxis
              dataKey="label"
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            />
            <Tooltip content={<Tip />} cursor={{ fill: "var(--muted)", opacity: 0.35 }} />
            <Bar
              dataKey={byDesk ? "minutes" : "chats"}
              radius={[5, 5, 0, 0]}
              maxBarSize={36}
              isAnimationActive={false}
            >
              {rows.map((row) => (
                <Cell
                  key={row.label}
                  fill={
                    row.today ? "var(--primary)" : "color-mix(in oklch, var(--primary) 32%, transparent)"
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

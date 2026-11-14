import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr, num } from "../lib/format";

/** One colour per customer segment, used everywhere the segment appears. */
export const SEG_COLOR: Record<string, string> = {
  Champions: "#2f6f4f", Loyal: "#4f7cac", Promising: "#7c9cbf", New: "#8b7bb8",
  "Needs attention": "#c99a3b", "At risk": "#c2553f", Hibernating: "#9a958c",
};

export const RECENCY_BUCKETS = [
  { key: "0-3", label: "0–3 days", min: 0, max: 3 },
  { key: "4-7", label: "4–7 days", min: 4, max: 7 },
  { key: "8-14", label: "1–2 weeks", min: 8, max: 14 },
  { key: "15-30", label: "2–4 weeks", min: 15, max: 30 },
  { key: "31+", label: "1 month+", min: 31, max: Infinity },
];
export const bucketOf = (days: number) => RECENCY_BUCKETS.find((b) => days >= b.min && days <= b.max)?.key ?? "31+";

type SegRow = { segment: string; customers: number; revenue_share_pct: number; description?: string };

/** Donut: share of sales by segment. Click a slice or a legend row to filter. */
export function SegmentDonut({ rows, total, active, onPick }: {
  rows: SegRow[]; total: number; active: string; onPick: (s: string) => void;
}) {
  const data = rows.filter((r) => r.customers).sort((a, b) => b.revenue_share_pct - a.revenue_share_pct);
  return (
    <div className="grid items-center gap-4 sm:grid-cols-[180px_1fr]">
      <div className="relative h-[180px]">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="revenue_share_pct" nameKey="segment" innerRadius={58} outerRadius={84}
              paddingAngle={2} stroke="none" onClick={(d: any) => onPick(d.segment === active ? "" : d.segment)}
              className="cursor-pointer outline-none">
              {data.map((d) => (
                <Cell key={d.segment} fill={SEG_COLOR[d.segment] ?? "#999"}
                  opacity={!active || active === d.segment ? 1 : 0.25} />
              ))}
            </Pie>
            <Tooltip formatter={(v: any, _n, p: any) => [`${num(v)}% of sales · ${p.payload.customers} customers`, p.payload.segment]} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-semibold tabular-nums text-ink">{active ? data.find((d) => d.segment === active)?.customers : total}</span>
          <span className="text-[11px] text-muted">{active || "customers"}</span>
        </div>
      </div>
      <ul className="space-y-0.5">
        {data.map((d) => (
          <li key={d.segment}>
            <button onClick={() => onPick(d.segment === active ? "" : d.segment)} title={d.description}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm transition hover:bg-surface-2 ${active === d.segment ? "bg-surface-2" : ""} ${active && active !== d.segment ? "opacity-50" : ""}`}>
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: SEG_COLOR[d.segment] }} />
              <span className="flex-1 text-ink">{d.segment}</span>
              <span className="tabular-nums text-muted">{d.customers}</span>
              <span className="w-14 text-right font-medium tabular-nums text-ink">{num(d.revenue_share_pct)}%</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Bars: how many customers last came in each time window. Click a bar to filter. */
export function RecencyBars({ customers, color, active, onPick }: {
  customers: { recency_days: number; monthly_value_rs: number }[]; color: string; active: string;
  onPick: (b: string) => void;
}) {
  const data = RECENCY_BUCKETS.map((b) => {
    const inB = customers.filter((c) => bucketOf(c.recency_days) === b.key);
    return { ...b, customers: inB.length, spend: inB.reduce((s, c) => s + (c.monthly_value_rs || 0), 0) };
  });
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 18, right: 4, left: -18, bottom: 0 }}>
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: "rgb(var(--muted))" }} axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "rgb(var(--muted))" }} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "rgb(var(--surface-2))" }}
          formatter={(v: any, _n, p: any) => [`${v} customers · ${inr(p.payload.spend)}/month`, "Last came"]} />
        <Bar dataKey="customers" radius={[6, 6, 0, 0]} label={{ position: "top", fontSize: 11, fill: "rgb(var(--muted))" }}
          onClick={(d: any) => onPick(d.key === active ? "" : d.key)} className="cursor-pointer">
          {data.map((d) => (
            <Cell key={d.key} fill={color} opacity={!active || active === d.key ? (d.min > 14 ? 0.55 : 1) : 0.2} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

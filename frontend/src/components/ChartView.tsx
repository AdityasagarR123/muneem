import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ReferenceArea,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import { inr, num, shortDate } from "../lib/format";

export const COLORS = ["#2f6f4f", "#4f7cac", "#c99a3b", "#c2553f", "#8b7bb8", "#3f8f8a", "#b5677d", "#7a8f3c"];

export type ChartSpec = {
  kind: "line" | "area" | "bar" | "stacked_bar" | "pie" | "funnel" | "waterfall" | "cohort_heatmap" | "scatter";
  x: string;
  unit?: string;
  series: { key: string; label: string }[];
  data: Record<string, any>[];
  annotations?: { name: string; start: string; end: string }[];
  start?: { label: string; value: number };
  end?: { label: string; value: number };
  color?: string;
  row?: string;
  col?: string;
  value?: string;
  png_url?: string;
};

const isDate = (v: any) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v);

export default function ChartView({ spec, height = 260 }: { spec: ChartSpec; height?: number }) {
  if (!spec?.data?.length) return <p className="text-sm text-muted">No data to chart.</p>;
  const money = spec.unit === "₹";
  const fmtVal = (v: any, key?: string) =>
    typeof v !== "number" ? v : key?.endsWith("_pct") || spec.unit === "%" ? `${num(v)}%` : money ? inr(v) : num(v);
  const tickX = (v: any) => (isDate(v) ? shortDate(v) : String(v).length > 14 ? String(v).slice(0, 13) + "…" : v);
  const series = spec.series?.some((s) => s.key.endsWith("_rs"))
    ? spec.series.filter((s) => !s.key.endsWith("_pct"))
    : spec.series ?? [];

  if (spec.kind === "cohort_heatmap") return <Heatmap spec={spec} />;
  if (spec.kind === "funnel") return <Funnel spec={spec} fmt={fmtVal} />;

  if (spec.kind === "pie") {
    const key = series[0].key;
    return (
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie data={spec.data} dataKey={key} nameKey={spec.x} innerRadius="45%" outerRadius="75%"
            label={(e) => e[spec.x]}>
            {spec.data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
          </Pie>
          <Tooltip formatter={(v: any) => fmtVal(v, key)} />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  if (spec.kind === "scatter") {
    const key = series[0].key;
    const groups: Record<string, any[]> = {};
    spec.data.forEach((d) => (groups[spec.color ? String(d[spec.color]) : ""] ??= []).push(d));
    return (
      <ResponsiveContainer width="100%" height={height + 40}>
        <ScatterChart margin={{ bottom: 10 }}>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
          <XAxis type="number" dataKey={spec.x} name={spec.x.replace(/_/g, " ")} tick={{ fontSize: 11 }}
            label={{ value: spec.x.replace(/_/g, " "), position: "insideBottom", offset: -4, fontSize: 11 }} />
          <YAxis type="number" dataKey={key} tick={{ fontSize: 11 }} width={money ? 72 : 44}
            tickFormatter={(v) => (money ? inr(v) : num(v))} />
          <Tooltip formatter={(v: any, n: any) => fmtVal(v, String(n))} cursor={{ strokeDasharray: "3 3" }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {Object.entries(groups).map(([g, pts], i) => (
            <Scatter key={g} name={g || series[0].label} data={pts} fill={COLORS[i % COLORS.length]} fillOpacity={0.75} />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    );
  }

  if (spec.kind === "waterfall") {
    const key = series[0].key;
    const bars: { label: string; base: number; size: number; raw: number; color: string }[] = [];
    let cum = spec.start?.value ?? 0;
    if (spec.start) bars.push({ label: spec.start.label, base: 0, size: cum, raw: cum, color: "#94a3b8" });
    spec.data.forEach((d) => {
      const v = Number(d[key] ?? 0);
      bars.push({ label: String(d[spec.x]), base: v >= 0 ? cum : cum + v, size: Math.abs(v), raw: v,
        color: v >= 0 ? "#16a34a" : "#dc2626" });
      cum += v;
    });
    if (spec.end) bars.push({ label: spec.end.label, base: 0, size: spec.end.value, raw: spec.end.value, color: "#475569" });
    const lows = bars.filter((b) => b.base > 0).map((b) => b.base);
    const lo = spec.start && lows.length ? Math.floor(Math.min(...lows, spec.end?.value ?? Infinity) * 0.9) : 0;
    return (
      <ResponsiveContainer width="100%" height={height + 30}>
        <BarChart data={bars}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={0} tickFormatter={(v) => (v.length > 18 ? v.slice(0, 17) + "…" : v)} />
          <YAxis domain={[lo, "auto"]} allowDataOverflow tick={{ fontSize: 11 }} width={72}
            tickFormatter={(v) => (money ? inr(v) : num(v))} />
          <Tooltip formatter={(_v: any, _n: any, item: any) => fmtVal(item?.payload?.raw, key)}
            labelFormatter={(l) => l} />
          <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="size" stackId="w" isAnimationActive={false}>
            {bars.map((b, i) => <Cell key={i} fill={b.color} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  }

  const xs = spec.data.map((d) => d[spec.x]);
  const areas = (spec.annotations ?? [])
    .map((a) => {
      const x1 = xs.find((x) => x >= a.start) ?? xs[0];
      const after = xs.filter((x) => x <= a.end);
      return { ...a, x1, x2: after[after.length - 1] ?? xs[xs.length - 1] };
    })
    .filter((a) => a.x1 && a.x2 && a.x1 <= a.x2);

  const common = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
      <XAxis dataKey={spec.x} tickFormatter={tickX} tick={{ fontSize: 11 }} minTickGap={16} />
      <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => (money ? inr(v) : num(v))} width={money ? 72 : 44} />
      <Tooltip formatter={(v: any, _n: any, item: any) => fmtVal(v, item?.dataKey)} labelFormatter={tickX} />
      {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
      {areas.map((a) => (
        <ReferenceArea key={a.name} x1={a.x1} x2={a.x2} fill="#fde68a" fillOpacity={0.35}
          label={{ value: a.name, position: "insideTop", fontSize: 11 }} />
      ))}
    </>
  );

  return (
    <ResponsiveContainer width="100%" height={height}>
      {spec.kind === "line" ? (
        <LineChart data={spec.data}>
          {common}
          {series.map((s, i) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={COLORS[i]}
              dot={false} strokeWidth={i === 0 ? 1.5 : 2.5} />
          ))}
        </LineChart>
      ) : spec.kind === "area" ? (
        <AreaChart data={spec.data}>
          {common}
          {series.map((s, i) => (
            <Area key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={COLORS[i]} fill={COLORS[i]} fillOpacity={0.15} />
          ))}
        </AreaChart>
      ) : (
        <BarChart data={spec.data}>
          {common}
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} fill={COLORS[i]}
              stackId={spec.kind === "stacked_bar" ? "s" : undefined}
              radius={spec.kind === "stacked_bar" ? undefined : [3, 3, 0, 0]} />
          ))}
        </BarChart>
      )}
    </ResponsiveContainer>
  );
}

function Funnel({ spec, fmt }: { spec: ChartSpec; fmt: (v: any, k?: string) => any }) {
  const key = spec.series[0].key;
  const top = Math.max(...spec.data.map((d) => Number(d[key] ?? 0)), 1);
  return (
    <div className="space-y-1.5 py-1">
      {spec.data.map((d, i) => {
        const v = Number(d[key] ?? 0);
        const w = Math.max(8, (100 * v) / top);
        return (
          <div key={i} className="flex justify-center">
            <div className="flex items-center justify-between gap-3 rounded px-3 py-2 text-sm text-white"
              style={{ width: `${w}%`, background: COLORS[0], opacity: 1 - i * 0.14 }}>
              <span className="truncate">{d[spec.x]}</span>
              <span className="whitespace-nowrap font-semibold">{fmt(v, key)} · {Math.round((100 * v) / top)}%</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Heatmap({ spec }: { spec: ChartSpec }) {
  const rk = spec.row!, ck = spec.col!, vk = spec.value!;
  const rows = [...new Set(spec.data.map((d) => String(d[rk])))].sort();
  const cols = [...new Set(spec.data.map((d) => Number(d[ck])))].sort((a, b) => a - b);
  const cell = new Map(spec.data.map((d) => [`${d[rk]}|${d[ck]}`, d[vk]]));
  return (
    <div className="overflow-x-auto">
      <table className="text-xs">
        <thead>
          <tr><th className="px-2 py-1 text-left font-medium text-muted">First month</th>
            {cols.map((c) => <th key={c} className="px-2 py-1 font-medium text-muted">M{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <td className="px-2 py-1 font-medium">{r}</td>
              {cols.map((c) => {
                const v = cell.get(`${r}|${c}`);
                return (
                  <td key={c} className="px-2 py-1 text-center"
                    style={{ background: v == null ? "transparent" : `rgba(37,99,235,${Math.min(1, Number(v) / 100)})`,
                      color: v != null && Number(v) > 60 ? "white" : "#0f172a" }}>
                    {v == null ? "" : `${num(v)}${spec.unit ?? ""}`}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

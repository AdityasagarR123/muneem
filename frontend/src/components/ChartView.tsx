import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ReferenceArea,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { inr, num, shortDate } from "../lib/format";

export const COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

type Spec = {
  kind: "line" | "bar" | "pie";
  x: string;
  unit?: string;
  series: { key: string; label: string }[];
  data: Record<string, any>[];
  annotations?: { name: string; start: string; end: string }[];
};

const isDate = (v: any) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v);

export default function ChartView({ spec, height = 260 }: { spec: Spec; height?: number }) {
  if (!spec?.data?.length) return <p className="text-sm text-slate-500">No data to chart.</p>;
  const money = spec.unit === "₹";
  const fmtVal = (v: any, key?: string) =>
    typeof v !== "number" ? v : key?.endsWith("_pct") || spec.unit === "%" ? `${num(v)}%` : money ? inr(v) : num(v);
  const tickX = (v: any) => (isDate(v) ? shortDate(v) : String(v).length > 14 ? String(v).slice(0, 13) + "…" : v);
  // don't mix rupees and percentages on one axis
  const series = spec.series.some((s) => s.key.endsWith("_rs"))
    ? spec.series.filter((s) => !s.key.endsWith("_pct"))
    : spec.series;

  if (spec.kind === "pie") {
    const key = series[0].key;
    return (
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie data={spec.data} dataKey={key} nameKey={spec.x} outerRadius="75%" label={(e) => e[spec.x]}>
            {spec.data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
          </Pie>
          <Tooltip formatter={(v: any) => fmtVal(v, key)} />
        </PieChart>
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
      ) : (
        <BarChart data={spec.data}>
          {common}
          {series.map((s, i) => <Bar key={s.key} dataKey={s.key} name={s.label} fill={COLORS[i]} radius={[3, 3, 0, 0]} />)}
        </BarChart>
      )}
    </ResponsiveContainer>
  );
}

import { Visual } from "../lib/api";
import { inr, num, pct } from "../lib/format";
import ChartView from "./ChartView";
import MermaidView from "./MermaidView";
import TableView from "./TableView";

export default function VisualView({ v }: { v: Visual }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      {v.title && <div className="mb-2 text-xs font-semibold text-slate-600">{v.title}</div>}
      {v.type === "chart" && <ChartView spec={v.spec} />}
      {v.type === "table" && <TableView columns={v.spec.columns} rows={v.spec.rows} total={v.spec.total_rows} />}
      {v.type === "mermaid" && <MermaidView code={v.spec.code} />}
      {v.type === "kpis" && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {v.spec.cards.map((c: any) => (
            <div key={c.key} className="rounded border border-slate-100 bg-slate-50 p-2">
              <div className="text-[11px] uppercase text-slate-500">{c.label}</div>
              <div className="font-semibold">{c.unit === "₹" ? inr(c.value) : c.unit === "%" ? pct(c.value) : num(c.value)}</div>
              {c.change_pct != null && (
                <div className={`text-xs ${c.change_pct >= 0 ? "text-green-700" : "text-red-600"}`}>{pct(c.change_pct, true)}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

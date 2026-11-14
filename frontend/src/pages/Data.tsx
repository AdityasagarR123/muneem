import { FormEvent, useState } from "react";
import { Badge, Card, ErrorBox, Spinner, useAsync } from "../components/ui";
import { api } from "../lib/api";

const TABLES = [
  { key: "items", label: "Items (product list)", required: true, hint: "item_id, name, category, mrp, cost, gst_rate, pack_size, supplier_id" },
  { key: "sales", label: "Sales (bill lines)", required: true, hint: "invoice_id/bill no, date, item_id, qty, price, payment mode" },
  { key: "inventory", label: "Stock", required: false, hint: "item_id, stock_qty, as_of_date" },
  { key: "expenses", label: "Expenses", required: false, hint: "date, category, amount, payment mode" },
  { key: "suppliers", label: "Suppliers", required: false, hint: "supplier_id, name, whatsapp, lead_time_days" },
];

export default function Data({ onChange }: { onChange: () => void }) {
  const status = useAsync(() => api.get("/status"), []);
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [workbook, setWorkbook] = useState<File | null>(null);
  const [partial, setPartial] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const form = new FormData();
    Object.entries(files).forEach(([k, f]) => f && form.append(k, f));
    if (workbook) form.append("workbook", workbook);
    form.append("allow_partial", String(partial));
    await run(() => api.upload("/upload", form));
  }
  async function run(fn: () => Promise<any>) {
    setBusy(true); setErr(null); setResult(null);
    try {
      const r = await fn();
      setResult(r);
      if (r.report?.ok) { status.reload(); onChange(); }
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }

  const ds = status.data?.dataset;
  const hasFiles = workbook || Object.values(files).some(Boolean);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-bold text-slate-900">Data</h1>
        <p className="text-sm text-slate-500">Upload exports from your billing app or Excel. Muneem checks every row before using it.</p>
      </header>

      <Card title="Current data" right={
        <button className="btn" disabled={busy} onClick={() => confirm("Replace current data with the synthetic sample?") && run(() => api.post("/sample"))}>
          Load sample data
        </button>}>
        {status.loading ? <Spinner /> : ds ? (
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <span>Source: <b>{ds.source}</b></span>
            <span>As of: <b>{ds.as_of}</b></span>
            <span>Sales: {ds.sales_from} → {ds.sales_to}</span>
            {Object.entries(ds.counts).map(([k, v]) => <span key={k}>{k}: <b>{String(v)}</b></span>)}
            <Badge tone={status.data.arango.status === "connected" ? "green" : "gray"}>
              ArangoDB {status.data.arango.status}{status.data.arango.persisted ? " · saved" : ""}
            </Badge>
          </div>
        ) : <p className="text-sm text-slate-500">No data loaded.</p>}
      </Card>

      <Card title="Upload your files">
        <form onSubmit={submit} className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            {TABLES.map((t) => (
              <label key={t.key} className="flex flex-col gap-1 rounded-md border border-slate-200 p-3 text-sm">
                <span className="font-medium">{t.label} {t.required && <span className="text-red-600">*</span>}</span>
                <span className="text-xs text-slate-500">{t.hint}</span>
                <input type="file" accept=".csv,.xlsx" className="text-xs"
                  onChange={(e) => setFiles({ ...files, [t.key]: e.target.files?.[0] ?? null })} />
              </label>
            ))}
            <label className="flex flex-col gap-1 rounded-md border border-dashed border-blue-300 bg-blue-50/40 p-3 text-sm">
              <span className="font-medium">…or one Excel workbook</span>
              <span className="text-xs text-slate-500">Sheets named items / sales / stock / expenses / suppliers</span>
              <input type="file" accept=".xlsx" className="text-xs" onChange={(e) => setWorkbook(e.target.files?.[0] ?? null)} />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={partial} onChange={(e) => setPartial(e.target.checked)} />
              Load valid rows even if some rows have errors
            </label>
            <button className="btn btn-primary" disabled={busy || !hasFiles}>{busy ? "Checking…" : "Upload & check"}</button>
          </div>
        </form>
      </Card>

      {err != null && <ErrorBox error={err} />}
      {result && <Report r={result} />}
    </div>
  );
}

function Report({ r }: { r: any }) {
  const rep = r.report;
  return (
    <Card title={<span className="flex items-center gap-2">Validation report
      <Badge tone={rep.ok ? "green" : "red"}>{rep.ok ? "Loaded" : "Not loaded"}</Badge></span>}>
      <div className="space-y-3 text-sm">
        {rep.ok && <p className="text-green-700">Loaded {Object.entries(rep.rows_loaded).map(([k, v]) => `${v} ${k}`).join(", ")} · as of {rep.as_of}</p>}
        {rep.notes?.length > 0 && <ul className="list-disc pl-5 text-slate-600">{rep.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
        {rep.error_count > 0 && <IssueTable title={`${rep.error_count} error(s)`} tone="red" issues={rep.errors} summary={rep.error_summary} />}
        {rep.warning_count > 0 && <IssueTable title={`${rep.warning_count} warning(s)`} tone="amber" issues={rep.warnings} summary={rep.warning_summary} />}
      </div>
    </Card>
  );
}

function IssueTable({ title, tone, issues, summary }: { title: string; tone: string; issues: any[]; summary: Record<string, number> }) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-2"><Badge tone={tone}>{title}</Badge></div>
      <ul className="mb-2 text-xs text-slate-600">{Object.entries(summary).map(([k, v]) => <li key={k}>{v}× {k}</li>)}</ul>
      <div className="max-h-64 overflow-auto rounded border border-slate-200">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-100 text-left"><tr>
            <th className="px-2 py-1">Table</th><th className="px-2 py-1">Row</th><th className="px-2 py-1">Column</th>
            <th className="px-2 py-1">Value</th><th className="px-2 py-1">Problem</th></tr></thead>
          <tbody>{issues.map((i, k) => (
            <tr key={k} className="border-t border-slate-100">
              <td className="px-2 py-1">{i.table}</td><td className="px-2 py-1">{i.row ?? "–"}</td>
              <td className="px-2 py-1">{i.column ?? "–"}</td><td className="px-2 py-1">{i.value ?? ""}</td>
              <td className="px-2 py-1">{i.message}</td></tr>))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

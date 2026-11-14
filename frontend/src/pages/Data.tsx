import { useState } from "react";
import { Badge, Card, ErrorBox, PageHeader, Spinner, useAsync } from "../components/ui";
import { api } from "../lib/api";

const ENTITIES: Record<string, string> = {
  items: "Products / item master", sales: "Bill lines (sales)", inventory: "Stock", expenses: "Expenses",
  suppliers: "Suppliers", customers: "Customers", repayments: "Udhaar repayments",
};
const STATUS: Record<string, [string, string]> = {
  auto: ["green", "sure"], check: ["amber", "please check"], missing: ["red", "needed"], unmapped: ["gray", "optional"],
};

export default function Data({ onChange }: { onChange: () => void }) {
  const status = useAsync(() => api.get("/status"), []);
  const agent = useAsync(() => api.get("/agent/status"), []);
  const [analysis, setAnalysis] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const run = async (fn: () => Promise<any>, then: (r: any) => void) => {
    setBusy(true); setErr(null);
    try { then(await fn()); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const ds = status.data?.dataset;

  return (
    <div className="space-y-6">
      <PageHeader title="Data" sub="Bring records from any billing app, spreadsheet or database. Muneem works out what each column means and asks only when unsure." />

      <Card title="Current data" right={
        <button className="btn" disabled={busy} onClick={() => confirm("Replace current data with the synthetic sample?") &&
          run(() => api.post("/sample"), (r) => { setResult(r); status.reload(); onChange(); })}>Load sample data</button>}>
        {status.loading ? <Spinner /> : ds ? (
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate-700">
            <span>Source: <b>{ds.source}</b></span><span>As of: <b>{ds.as_of}</b></span>
            <span>Sales: {ds.sales_from} → {ds.sales_to}</span>
            {Object.entries(ds.counts).map(([k, v]) => <span key={k}>{k.replace("_", " ")}: <b>{String(v)}</b></span>)}
            <Badge tone={status.data.postgres?.persisted ? "green" : "gray"}>Postgres {status.data.postgres?.status}{status.data.postgres?.persisted ? " · saved" : ""}</Badge>
            <Badge tone={status.data.arango?.persisted ? "green" : "gray"}>ArangoDB {status.data.arango?.status}{status.data.arango?.persisted ? " · graph synced" : ""}</Badge>
          </div>
        ) : <p className="text-sm text-slate-500">No data loaded.</p>}
      </Card>

      {!analysis && (
        <div className="grid gap-4 md:grid-cols-2">
          <FileImport busy={busy} aiAvailable={!!agent.data?.llm_configured}
            onAnalyze={(form) => run(() => api.upload("/ingest/analyze", form), (a) => { setAnalysis(a); setResult(null); })} />
          <SqlImport busy={busy} onAnalyze={(body) => run(() => api.post("/ingest/sql/analyze", body), (a) => { setAnalysis(a); setResult(null); })}
            onError={setErr} />
        </div>
      )}
      {err != null && <ErrorBox error={err} />}
      {analysis && (
        <Review a={analysis} busy={busy} onCancel={() => setAnalysis(null)}
          onCommit={(body) => run(() => api.post(`/ingest/${analysis.ingest_id}/commit`, body), (r) => {
            setResult(r); if (r.report?.ok) { setAnalysis(null); status.reload(); onChange(); }
          })} />
      )}
      {result && <Report r={result} />}
      <Webhook />
    </div>
  );
}

function FileImport({ busy, aiAvailable, onAnalyze }: { busy: boolean; aiAvailable: boolean; onAnalyze: (f: FormData) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [ai, setAi] = useState(false);
  return (
    <Card title="Upload files">
      <label className="flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-600 hover:bg-slate-50">
        <span className="font-medium text-slate-800">Choose CSV or Excel files</span>
        <span className="text-xs text-slate-500">Any column names - products, bills, stock, expenses, customers, udhaar…</span>
        <input type="file" multiple accept=".csv,.xlsx" className="hidden" onChange={(e) => setFiles([...(e.target.files ?? [])])} />
      </label>
      {files.length > 0 && <ul className="mt-2 text-xs text-slate-600">{files.map((f) => <li key={f.name}>• {f.name}</li>)}</ul>}
      <div className="mt-3 flex items-center justify-between gap-3">
        <label className={`flex items-center gap-1.5 text-xs ${aiAvailable ? "text-slate-600" : "text-slate-400"}`}
          title="Only for columns the rules can't place; you confirm every suggestion">
          <input type="checkbox" disabled={!aiAvailable} checked={ai} onChange={(e) => setAi(e.target.checked)} />
          Let AI suggest unclear columns
        </label>
        <button className="btn btn-dark" disabled={busy || !files.length} onClick={() => {
          const form = new FormData(); files.forEach((f) => form.append("files", f)); form.append("use_ai", String(ai)); onAnalyze(form);
        }}>{busy ? "Reading…" : "Analyze"}</button>
      </div>
    </Card>
  );
}

function SqlImport({ busy, onAnalyze, onError }: { busy: boolean; onAnalyze: (b: any) => void; onError: (e: unknown) => void }) {
  const [url, setUrl] = useState("");
  const [tables, setTables] = useState<any[] | null>(null);
  const [pick, setPick] = useState<string[]>([]);
  return (
    <Card title="Connect a database (read-only)">
      <input className="input w-full" type="password" autoComplete="off" placeholder="postgresql://user:password@host/db"
        value={url} onChange={(e) => { setUrl(e.target.value); setTables(null); }} />
      <p className="mt-1 text-xs text-slate-500">PostgreSQL or MySQL. Muneem only reads; the address isn't saved.</p>
      {!tables ? (
        <button className="btn mt-3" disabled={busy || url.length < 10} onClick={async () => {
          try { setTables(await api.post("/ingest/sql/tables", { url })); setPick([]); } catch (e) { onError(e); }
        }}>List tables</button>
      ) : (
        <div className="mt-3 space-y-1 text-sm">
          {tables.map((t) => (
            <label key={t.table} className="flex items-center gap-2">
              <input type="checkbox" checked={pick.includes(t.table)}
                onChange={(e) => setPick(e.target.checked ? [...pick, t.table] : pick.filter((x) => x !== t.table))} />
              <span>{t.table}</span><span className="text-xs text-slate-400">{t.rows} rows</span>
            </label>
          ))}
          <button className="btn btn-dark mt-2" disabled={busy || !pick.length} onClick={() => onAnalyze({ url, tables: pick })}>Analyze tables</button>
        </div>
      )}
    </Card>
  );
}

function Review({ a, busy, onCancel, onCommit }: { a: any; busy: boolean; onCancel: () => void; onCommit: (b: any) => void }) {
  const [choices, setChoices] = useState<Record<string, any>>(() => Object.fromEntries(a.tables.map((t: any) => [t.key, {
    include: true, entity: t.entity, mapping: Object.fromEntries(t.mapping.map((m: any) => [m.field, m.source ?? ""])),
  }])));
  const [name, setName] = useState("My billing export");
  const [remember, setRemember] = useState(true);
  const [partial, setPartial] = useState(false);
  const set = (key: string, patch: any) => setChoices({ ...choices, [key]: { ...choices[key], ...patch } });
  const toConfirm = a.summary.fields_to_confirm;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
        <span>Read <b>{a.tables.length}</b> table(s). <b>{a.summary.fields_auto}</b> fields matched automatically;{" "}
          {toConfirm ? <b className="text-amber-700">{toConfirm} need a look (highlighted)</b> : <b className="text-green-700">nothing needs checking</b>}.</span>
        <button className="btn" onClick={onCancel}>Start over</button>
      </div>

      {a.tables.map((t: any) => {
        const ch = choices[t.key];
        const changedEntity = ch.entity !== t.entity;
        return (
          <Card key={t.key} title={
            <span className="flex flex-wrap items-center gap-2">
              <input type="checkbox" checked={ch.include} onChange={(e) => set(t.key, { include: e.target.checked })} />
              {t.name} <span className="font-normal text-slate-400">· {t.rows} rows</span>
              {t.remembered && <Badge tone="violet">remembered: {t.remembered}</Badge>}
            </span>}
            right={
              <label className="flex items-center gap-2 text-xs text-slate-500">This is
                <select className={`input py-1 text-sm ${t.entity_status === "check" ? "border-amber-400" : ""}`} value={ch.entity}
                  onChange={(e) => set(t.key, { entity: e.target.value, mapping: {} })}>
                  {Object.entries(ENTITIES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <Badge tone={t.entity_confidence >= 0.6 ? "green" : "amber"}>{Math.round(t.entity_confidence * 100)}%</Badge>
              </label>}>
            {changedEntity ? (
              <p className="text-sm text-amber-700">Type changed - analyze again to get matching suggestions for {ENTITIES[ch.entity]}, or pick the columns below.</p>
            ) : null}
            {ch.include && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="py-1.5 pr-3 font-medium">Field</th><th className="pr-3 font-medium">Column in your file</th>
                    <th className="pr-3 font-medium">Example</th><th className="font-medium">Match</th></tr></thead>
                  <tbody>
                    {(changedEntity ? [] : t.mapping).filter((m: any) => m.required || m.source || m.candidates.length).map((m: any) => {
                      const [tone, label] = STATUS[m.status] ?? ["gray", m.status];
                      const src = ch.mapping[m.field] ?? "";
                      const prof = t.columns.find((c: any) => c.column === src);
                      const flag = m.status === "check" || (m.status === "missing" && m.required);
                      return (
                        <tr key={m.field} className={`border-t border-slate-100 ${flag ? "bg-amber-50/60" : ""}`}>
                          <td className="py-1.5 pr-3">{m.label}{m.required && <span className="text-red-500"> *</span>}</td>
                          <td className="pr-3">
                            <select className="input w-full py-1 text-sm" value={src}
                              onChange={(e) => set(t.key, { mapping: { ...ch.mapping, [m.field]: e.target.value } })}>
                              <option value="">— not in this file —</option>
                              {t.columns.map((c: any) => <option key={c.column} value={c.column}>{c.column} ({c.type})</option>)}
                            </select>
                          </td>
                          <td className="max-w-[160px] truncate pr-3 text-xs text-slate-500">{prof?.examples?.slice(0, 2).join(", ")}</td>
                          <td><Badge tone={tone} title={m.reason}>{src === (m.source ?? "") ? `${label}${m.confidence ? ` ${Math.round(m.confidence * 100)}%` : ""}` : "your choice"}</Badge></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {t.unmapped_columns.length > 0 && <p className="mt-2 text-xs text-slate-400">Not used: {t.unmapped_columns.join(", ")}</p>}
              </div>
            )}
          </Card>
        );
      })}

      {a.links.length > 0 && (
        <Card title="How your files connect">
          <ul className="space-y-1 text-sm">
            {a.links.map((l: any) => (
              <li key={l.from + l.to} className="flex flex-wrap items-center gap-2">
                <Badge tone={l.status === "ok" ? "green" : l.status === "partial" ? "amber" : "red"}>{l.coverage_pct}%</Badge>
                {l.meaning} <span className="text-xs text-slate-400">({l.from} → {l.to}{l.missing_values ? `, ${l.missing_values} codes not found` : ""})</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 px-4 py-3 text-sm">
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember this layout as</label>
        <input className="input w-56" value={name} onChange={(e) => setName(e.target.value)} disabled={!remember} maxLength={120} />
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={partial} onChange={(e) => setPartial(e.target.checked)} />
          Load valid rows even if some rows have errors</label>
        <button className="btn btn-dark ml-auto" disabled={busy} onClick={() => onCommit({
          tables: a.tables.map((t: any) => ({ key: t.key, include: choices[t.key].include, entity: choices[t.key].entity,
            mapping: Object.fromEntries(Object.entries(choices[t.key].mapping).filter(([, v]) => v)) })),
          remember, source_name: name, allow_partial: partial,
        })}>{busy ? "Importing…" : "Import"}</button>
      </div>
    </div>
  );
}

function Webhook() {
  const [open, setOpen] = useState(false);
  return (
    <Card title="Live updates from your billing app (webhook)" right={<button className="btn" onClick={() => setOpen(!open)}>{open ? "Hide" : "How"}</button>}>
      {open ? (
        <div className="space-y-2 text-sm text-slate-600">
          <p>Set <code>WEBHOOK_SECRET</code> in <code>.env</code>, then POST new records as JSON to:</p>
          <pre className="overflow-x-auto rounded-lg bg-slate-50 p-3 text-xs">{`POST /api/ingest/webhook/{sales|expenses|repayments|items|inventory|customers|suppliers}
X-Muneem-Signature: sha256=<HMAC-SHA256(secret, raw body)>

{"records": [{"bill_no": "B1", "date": "2026-11-14", "item_id": "I001", "qty": 2, "rate": 265, "payment": "upi"}]}`}</pre>
          <p>Bills, expenses and repayments are added; products, stock, suppliers and customers are updated by their code. Everything is re-checked before it replaces the current data - a bad batch changes nothing.</p>
        </div>
      ) : <p className="text-sm text-slate-500">Send new bills or stock changes automatically, signed with a shared secret.</p>}
    </Card>
  );
}

function Report({ r }: { r: any }) {
  const rep = r.report;
  return (
    <Card title={<span className="flex items-center gap-2">Import report <Badge tone={rep.ok ? "green" : "red"}>{rep.ok ? "Loaded" : "Not loaded"}</Badge></span>}>
      <div className="space-y-3 text-sm">
        {rep.ok && <p className="text-green-700">Loaded {Object.entries(rep.rows_loaded).map(([k, v]) => `${v} ${k}`).join(", ")} · as of {rep.as_of}</p>}
        {r.remembered?.length > 0 && <p className="text-slate-600">Remembered for next time: {r.remembered.join(", ")}</p>}
        {rep.notes?.length > 0 && <ul className="list-disc pl-5 text-slate-600">{rep.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>}
        {rep.error_count > 0 && <Issues title={`${rep.error_count} error(s)`} tone="red" issues={rep.errors} summary={rep.error_summary} />}
        {rep.warning_count > 0 && <Issues title={`${rep.warning_count} warning(s)`} tone="amber" issues={rep.warnings} summary={rep.warning_summary} />}
      </div>
    </Card>
  );
}

function Issues({ title, tone, issues, summary }: { title: string; tone: string; issues: any[]; summary: Record<string, number> }) {
  return (
    <div>
      <Badge tone={tone}>{title}</Badge>
      <ul className="my-2 text-xs text-slate-600">{Object.entries(summary).map(([k, v]) => <li key={k}>{v}× {k}</li>)}</ul>
      <div className="max-h-64 overflow-auto rounded border border-slate-200">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-100 text-left"><tr>
            <th className="px-2 py-1">Table</th><th className="px-2 py-1">Row</th><th className="px-2 py-1">Column</th>
            <th className="px-2 py-1">Value</th><th className="px-2 py-1">Problem</th></tr></thead>
          <tbody>{issues.map((i, k) => (
            <tr key={k} className="border-t border-slate-100"><td className="px-2 py-1">{i.table}</td><td className="px-2 py-1">{i.row ?? "–"}</td>
              <td className="px-2 py-1">{i.column ?? "–"}</td><td className="px-2 py-1">{i.value ?? ""}</td><td className="px-2 py-1">{i.message}</td></tr>))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

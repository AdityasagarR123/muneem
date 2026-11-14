import { useState } from "react";
import { Evidence } from "../lib/api";
import { cell } from "../lib/format";
import { Badge } from "./ui";

const SKIP = (k: string) => k.endsWith("_paise") || ["inputs", "windows", "items", "message", "context"].includes(k);

/** "Show calculation": which tool ran, with what inputs, the formula, and the rows behind the answer. */
export default function EvidencePanel({ evidence }: { evidence: Evidence[] }) {
  const [open, setOpen] = useState(false);
  if (!evidence.length) return null;
  return (
    <div className="mt-2">
      <button className="text-xs font-medium text-blue-700 hover:underline" onClick={() => setOpen(!open)}>
        {open ? "Hide calculation" : "Show calculation"}
      </button>
      {open && (
        <div className="mt-2 space-y-3">
          {evidence.map((e, i) => (
            <div key={i} className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <code className="rounded bg-white px-1.5 py-0.5 font-semibold">{e.tool}</code>
                {Object.keys(e.args).length > 0 && <code className="text-slate-500">{JSON.stringify(e.args)}</code>}
                {e.engine && e.engine !== "pandas" && <Badge tone="violet">{e.engine}</Badge>}
                {e.confidence !== "ok" && <Badge tone="amber">{e.confidence}</Badge>}
              </div>
              {e.error && <p className="text-red-600">{e.error}</p>}
              {Object.entries(e.resolved ?? {}).map(([k, r]: any) => (
                <p key={k} className="text-slate-600">Understood "{r.said}" as <b>{r.name ?? r.id}</b></p>
              ))}
              {e.formula && <p className="mt-1 text-slate-700"><b>Formula:</b> {e.formula}</p>}
              {e.assumptions?.length > 0 && <p className="mt-1 text-slate-500">Assumes: {e.assumptions.join("; ")}</p>}
              {e.rows?.length > 0 && <EvidenceRows rows={e.rows} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EvidenceRows({ rows }: { rows: Record<string, any>[] }) {
  const keys = Object.keys(rows[0]).filter((k) => !SKIP(k)).slice(0, 9);
  return (
    <div className="mt-2 max-h-56 overflow-auto rounded border border-slate-200 bg-white">
      <table className="w-full">
        <thead className="sticky top-0 bg-slate-100">
          <tr>{keys.map((k) => <th key={k} className="px-2 py-1 text-left font-medium">{k}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 20).map((r, i) => (
            <tr key={i} className="border-t border-slate-100">
              {keys.map((k) => <td key={k} className="whitespace-nowrap px-2 py-1">{cell(k, r[k])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

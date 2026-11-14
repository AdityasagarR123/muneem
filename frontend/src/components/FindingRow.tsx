import { useState } from "react";
import { api } from "../lib/api";
import { useActionRunner } from "../lib/actions";
import { paise } from "../lib/format";
import { pick, usePrefs } from "../lib/prefs";
import Icon from "./Icon";

export type Finding = {
  id: string; kind: string; category: "risk" | "opportunity" | "info"; severity: "high" | "medium" | "low";
  what: string; why: string; what_hi: string; why_hi: string; impact_paise: number | null; impact_label: string;
  confidence: string; action: { type: string; label: string; payload: Record<string, any> };
  evidence: { tool: string; args: Record<string, any> }; status: string; urgent: boolean;
};

const DOT: Record<string, string> = { high: "bg-red-500", medium: "bg-amber-400", low: "bg-slate-300" };
const CAT: Record<string, string> = { risk: "Risk", opportunity: "Opportunity", info: "Good to know" };

/** what happened · why it matters · ₹ impact · one action. compact = briefing row. */
export default function FindingRow({ f, compact = false, onStatus }: {
  f: Finding; compact?: boolean; onStatus?: (id: string, status: string) => void;
}) {
  const { lang } = usePrefs();
  const run = useActionRunner(lang);
  const [busy, setBusy] = useState(false);
  const act = async () => {
    setBusy(true);
    try {
      await run(f.action);
      api.post(`/findings/${encodeURIComponent(f.id)}/status`, { status: "acted" }).catch(() => {});
    } finally { setBusy(false); }
  };
  const setStatus = async (status: string) => {
    await api.post(`/findings/${encodeURIComponent(f.id)}/status`, { status });
    onStatus?.(f.id, status);
  };
  const askWhy = () => run({ type: "ask", label: "", payload: {
    question: lang === "en" ? `Explain: ${f.what}` : `Samjhaiye: ${f.what_hi}` } });

  return (
    <div className={`group flex gap-3 ${compact ? "py-3" : "rounded-xl border border-slate-200 bg-white p-4"}`}>
      <span className={`mt-2 h-2 w-2 shrink-0 rounded-full ${DOT[f.severity]}`} title={`${f.severity} priority`} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium text-slate-900">{pick(lang, f.what, f.what_hi)}</span>
          {f.urgent && <span className="text-[11px] font-semibold uppercase tracking-wide text-red-600">today</span>}
          {!compact && <span className="text-[11px] uppercase tracking-wide text-slate-400">{CAT[f.category]}</span>}
        </div>
        <p className={`mt-0.5 text-sm text-slate-600 ${compact ? "line-clamp-2" : ""}`}>{pick(lang, f.why, f.why_hi)}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {f.action?.label && (
            <button className="btn btn-dark" disabled={busy} onClick={act}>
              {busy ? "…" : f.action.label} <Icon name="arrowRight" size={14} />
            </button>
          )}
          {!compact && (
            <>
              <button className="btn" onClick={askWhy}>Ask Muneem</button>
              <button className="btn" onClick={() => setStatus("acted")}><Icon name="check" size={14} /> Done</button>
              <button className="btn" onClick={() => setStatus("dismissed")}>Dismiss</button>
            </>
          )}
        </div>
      </div>
      {f.impact_paise != null && (
        <div className="shrink-0 text-right">
          <div className="font-semibold tabular-nums text-slate-900">{paise(f.impact_paise)}</div>
          <div className="text-[11px] text-slate-500">{f.impact_label}</div>
        </div>
      )}
    </div>
  );
}

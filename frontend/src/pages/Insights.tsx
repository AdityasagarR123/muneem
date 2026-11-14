import { useState } from "react";
import FindingRow, { Finding } from "../components/FindingRow";
import { ErrorBox, PageHeader, Spinner, useAsync } from "../components/ui";
import { api } from "../lib/api";

const TABS = [
  { key: "", label: "All" }, { key: "risk", label: "Risks" },
  { key: "opportunity", label: "Opportunities" }, { key: "info", label: "Good to know" },
];

export default function Insights() {
  const [tab, setTab] = useState("");
  const [showDone, setShowDone] = useState(false);
  const q = useAsync(() => api.get<Finding[]>("/findings", { status: showDone ? "all" : undefined, limit: 50 }), [showDone]);
  const onStatus = (id: string, status: string) =>
    q.setData((list) => (list ?? []).map((f) => (f.id === id ? { ...f, status } : f))
      .filter((f) => showDone || f.status !== "dismissed"));
  const list = (q.data ?? []).filter((f) => !tab || f.category === tab);

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 md:px-6">
      <PageHeader title="Insights" sub="What happened, why it matters, and what to do next - found by rules on your data." />
      <div className="mb-4 flex flex-wrap items-center gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t.key ? "border-slate-900 font-medium text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
            {t.label}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1.5 pb-1 text-xs text-slate-500">
          <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> Show done & dismissed
        </label>
      </div>
      {q.loading ? <Spinner /> : q.error ? <ErrorBox error={q.error} onRetry={q.reload} /> : (
        <div className="space-y-3">
          {list.length === 0 && <p className="text-sm text-slate-500">Nothing here.</p>}
          {list.map((f) => (
            <div key={f.id} className={f.status === "acted" || f.status === "dismissed" ? "opacity-60" : ""}>
              <FindingRow f={f} onStatus={onStatus} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

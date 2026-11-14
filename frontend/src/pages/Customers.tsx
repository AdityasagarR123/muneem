import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ChartView from "../components/ChartView";
import { Badge, Card, ErrorBox, PageHeader, Spinner, useAsync } from "../components/ui";
import { api, Insight } from "../lib/api";
import { useActionRunner } from "../lib/actions";
import { inr, num, paise } from "../lib/format";
import { usePrefs } from "../lib/prefs";

const SEG_TONE: Record<string, string> = {
  Champions: "green", Loyal: "blue", New: "violet", Promising: "blue", "At risk": "red",
  Hibernating: "gray", "Needs attention": "amber",
};
const SEG_GOAL: Record<string, string> = {
  Champions: "reward_champions", Loyal: "reward_champions", New: "welcome_new", "At risk": "win_back",
};

export default function Customers() {
  const navigate = useNavigate();
  const { lang } = usePrefs();
  const run = useActionRunner(lang);
  const [segment, setSegment] = useState("");
  const [q, setQ] = useState("");
  const segs = useAsync(() => api.get<Insight>("/customers/segments"), []);
  const all = useAsync(() => api.get<{ total: number; customers: any[] }>("/customers", { limit: 1000 }), []);

  const list = useMemo(() => (all.data?.customers ?? []).filter((c) =>
    (!segment || c.segment === segment) &&
    (!q || `${c.name} ${c.phone} ${c.customer_id}`.toLowerCase().includes(q.toLowerCase()))), [all.data, segment, q]);

  if (segs.loading || all.loading) return <div className="p-8"><Spinner /></div>;
  if (segs.error || all.error) return <div className="p-8"><ErrorBox error={segs.error ?? all.error} /></div>;
  const h = segs.data!.headline;
  const points = (all.data?.customers ?? []).map((c) => ({
    recency_days: c.recency_days, monthly_value_rs: c.monthly_value_rs, segment: c.segment }));

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 md:px-6">
      <PageHeader title="Customers"
        sub={`${h.customers} customers on ${h.bills_linked_pct}% of bills (the rest are anonymous counter sales).`} />

      <div className="mb-5 flex flex-wrap gap-2">
        <button onClick={() => setSegment("")}
          className={`rounded-full border px-3 py-1 text-sm ${!segment ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
          All · {all.data?.total}
        </button>
        {segs.data!.rows.filter((r) => r.customers).map((r) => (
          <button key={r.segment} onClick={() => setSegment(r.segment === segment ? "" : r.segment)} title={r.description}
            className={`rounded-full border px-3 py-1 text-sm ${segment === r.segment ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
            {r.segment} · {r.customers} <span className="opacity-60">({num(r.revenue_share_pct)}% of sales)</span>
          </button>
        ))}
      </div>

      {segment && (
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
          <span className="text-slate-700">
            <b>{segment}</b>: {segs.data!.rows.find((r) => r.segment === segment)?.description}.{" "}
            <span className="text-slate-500">Suggested: {segs.data!.rows.find((r) => r.segment === segment)?.suggested_action}.</span>
          </span>
          {SEG_GOAL[segment] && (
            <button className="btn btn-dark" onClick={() => run({ type: "campaign_draft", label: "", payload: {
              goal: SEG_GOAL[segment], segment: segment === "At risk" ? null : { type: "segment", name: segment } } })}>
              Draft messages to {segment}
            </button>
          )}
        </div>
      )}

      <Card title="Days since last bill vs spend per month" className="mb-5">
        <ChartView height={240} spec={{ kind: "scatter", x: "recency_days", unit: "₹", color: "segment",
          series: [{ key: "monthly_value_rs", label: "Spend per month ₹" }],
          data: segment ? points.filter((p) => p.segment === segment) : points }} />
        <p className="text-xs text-slate-500">Top-right = used to spend a lot, now quiet: worth a message first.</p>
      </Card>

      <div className="mb-2 flex items-center justify-between gap-3">
        <input className="input w-72" placeholder="Search name or phone…" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="text-xs text-slate-500">{list.length} shown</span>
      </div>
      <div className="overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2 font-medium">Customer</th><th className="px-3 py-2 font-medium">Segment</th>
              <th className="px-3 py-2 text-right font-medium">Last bill</th>
              <th className="px-3 py-2 text-right font-medium">Usually every</th>
              <th className="px-3 py-2 text-right font-medium">Spend / month</th>
              <th className="px-3 py-2 text-right font-medium">Udhaar</th>
            </tr>
          </thead>
          <tbody>
            {list.slice(0, 200).map((c) => (
              <tr key={c.customer_id} onClick={() => navigate(`/customers/${c.customer_id}`)}
                className="cursor-pointer border-t border-slate-100 hover:bg-slate-50">
                <td className="px-3 py-2"><div className="font-medium text-slate-900">{c.name || c.customer_id}</div>
                  <div className="text-xs text-slate-400">{c.area}</div></td>
                <td className="px-3 py-2"><Badge tone={SEG_TONE[c.segment]}>{c.segment}</Badge>
                  {c.at_risk && <span className="ml-1 text-[11px] text-red-600">stopped?</span>}</td>
                <td className="px-3 py-2 text-right tabular-nums">{c.recency_days} d ago</td>
                <td className="px-3 py-2 text-right tabular-nums">{c.usual_gap_days != null ? `${num(c.usual_gap_days)} d` : "–"}</td>
                <td className="px-3 py-2 text-right tabular-nums">{inr(c.monthly_value_rs)}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${c.udhaar_overdue ? "text-red-600" : ""}`}>
                  {c.udhaar_outstanding_paise > 0 ? paise(c.udhaar_outstanding_paise) : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CustomerDetail() {
  const { cid } = useParams();
  const { lang } = usePrefs();
  const run = useActionRunner(lang);
  const q = useAsync(() => api.get<Insight>(`/customers/${cid}`), [cid]);
  if (q.loading) return <div className="p-8"><Spinner /></div>;
  if (q.error) return <div className="p-8"><ErrorBox error={q.error} /></div>;
  const h = q.data!.headline;
  const u = h.udhaar;
  const goal = u?.overdue ? "udhaar_reminder" : h.at_risk ? "win_back" : h.segment === "New" ? "welcome_new" : "reward_champions";
  const label = { udhaar_reminder: "Draft udhaar reminder", win_back: "Draft win-back message",
    welcome_new: "Draft welcome message", reward_champions: "Draft thank-you message" }[goal];

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 md:px-6">
      <Link to="/customers" className="text-sm text-slate-500 hover:text-slate-900">← Customers</Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{h.name || h.customer_id}</h1>
          <p className="text-sm text-slate-500">{h.phone} · {h.area} {h.khata && "· khata customer"}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={SEG_TONE[h.segment]}>{h.segment}</Badge>
          <button className="btn btn-dark" onClick={() => run({ type: "campaign_draft", label: "", payload: {
            goal, segment: { type: "customer_ids", ids: [h.customer_id] }, holdout_pct: 0 } })}>{label}</button>
        </div>
      </div>

      {h.at_risk && (
        <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          Usually comes every {num(h.usual_gap_days)} days - last bill was {h.recency_days} days ago. Spends about {inr(h.monthly_value_rs)} a month.
        </div>
      )}

      <div className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200 sm:grid-cols-4">
        {[["Bills", num(h.bills)], ["Total spend", inr(h.revenue_rs)], ["Average bill", inr(h.avg_bill_rs)],
          ["Last bill", `${h.recency_days} days ago`], ["Usually every", h.usual_gap_days != null ? `${num(h.usual_gap_days)} days` : "–"],
          ["Spend / month", inr(h.monthly_value_rs)], ["Udhaar owed", u ? inr(u.outstanding_rs) : "–"],
          ["Oldest unpaid", u?.outstanding_rs > 0 ? `${u.days_overdue} days` : "–"]].map(([k, v]) => (
          <div key={k} className="bg-white p-3"><div className="text-[11px] text-slate-500">{k}</div>
            <div className="font-semibold tabular-nums text-slate-900">{v}</div></div>
        ))}
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <Card title="Monthly spend">
          <ChartView height={200} spec={{ kind: "bar", x: "month", unit: "₹", series: [{ key: "revenue_rs", label: "₹" }], data: q.data!.rows }} />
        </Card>
        <Card title="What they buy">
          <ul className="divide-y divide-slate-100 text-sm">
            {(h.top_items ?? []).map((t: any) => (
              <li key={t.name} className="flex justify-between py-1.5"><span>{t.name}</span>
                <span className="tabular-nums text-slate-500">{num(t.bills)} bills · {inr(t.revenue_rs)}</span></li>
            ))}
          </ul>
        </Card>
      </div>
      <p className="mt-4 text-xs text-slate-400">{q.data!.formula}</p>
    </div>
  );
}

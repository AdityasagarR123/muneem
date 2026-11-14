import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ChartView from "../components/ChartView";
import { bucketOf, RECENCY_BUCKETS, RecencyBars, SEG_COLOR, SegmentDonut } from "../components/SegmentCharts";
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
  const [bucket, setBucket] = useState("");
  const [q, setQ] = useState("");
  const segs = useAsync(() => api.get<Insight>("/customers/segments"), []);
  const all = useAsync(() => api.get<{ total: number; customers: any[] }>("/customers", { limit: 1000 }), []);

  const inSeg = useMemo(() => (all.data?.customers ?? []).filter((c) => !segment || c.segment === segment),
    [all.data, segment]);
  const list = useMemo(() => inSeg.filter((c) =>
    (!bucket || bucketOf(c.recency_days) === bucket) &&
    (!q || `${c.name} ${c.phone} ${c.customer_id}`.toLowerCase().includes(q.toLowerCase()))), [inSeg, bucket, q]);

  if (segs.loading || all.loading) return <div className="p-8"><Spinner /></div>;
  if (segs.error || all.error) return <div className="p-8"><ErrorBox error={segs.error ?? all.error} /></div>;
  const h = segs.data!.headline;
  const segRow = segs.data!.rows.find((r) => r.segment === segment);
  const bucketLabel = RECENCY_BUCKETS.find((b) => b.key === bucket)?.label;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 md:px-6">
      <PageHeader title="Customers"
        sub={`${h.customers} customers on ${h.bills_linked_pct}% of bills (the rest are anonymous counter sales).`} />

      <div className="mb-5 grid gap-4 lg:grid-cols-[1.15fr_1fr]">
        <Card title="Who brings the sales" right={<span className="text-xs text-muted">share of last 90 days · click to filter</span>}>
          <SegmentDonut rows={segs.data!.rows as any} total={all.data?.total ?? 0} active={segment}
            onPick={(s) => { setSegment(s); setBucket(""); }} />
        </Card>
        <Card title={`When did ${segment ? segment.toLowerCase() + " customers" : "customers"} last come?`}
          right={<span className="text-xs text-muted">click a bar to filter</span>}>
          <RecencyBars customers={inSeg} color={SEG_COLOR[segment] ?? "rgb(var(--accent))"} active={bucket} onPick={setBucket} />
          <p className="text-xs text-muted">Faded bars = not seen for over 2 weeks: message these first.</p>
        </Card>
      </div>

      {segment && segRow && (
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm shadow-card"
          style={{ borderLeft: `4px solid ${SEG_COLOR[segment]}` }}>
          <span className="text-ink">
            <b>{segment}</b>: {segRow.description}.{" "}
            <span className="text-muted">Suggested: {segRow.suggested_action}.</span>
          </span>
          {SEG_GOAL[segment] && (
            <button className="btn btn-dark" onClick={() => run({ type: "campaign_draft", label: "", payload: {
              goal: SEG_GOAL[segment], segment: segment === "At risk" ? null : { type: "segment", name: segment } } })}>
              Draft messages to {segment}
            </button>
          )}
        </div>
      )}

      <div className="mb-2 flex items-center justify-between gap-3">
        <input className="input w-72" placeholder="Search name or phone…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {segment && <button className="rounded-full bg-surface-2 px-2.5 py-1 text-ink hover:bg-line" onClick={() => setSegment("")}>{segment} ✕</button>}
          {bucket && <button className="rounded-full bg-surface-2 px-2.5 py-1 text-ink hover:bg-line" onClick={() => setBucket("")}>Last came: {bucketLabel} ✕</button>}
          <span>{list.length} shown</span>
        </div>
      </div>
      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="w-full text-sm">
          <thead className="bg-surface-2/60 text-left text-xs uppercase tracking-wide text-muted">
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
                className="cursor-pointer border-t border-line/60 hover:bg-surface-2">
                <td className="px-3 py-2"><div className="font-medium text-ink">{c.name || c.customer_id}</div>
                  <div className="text-xs text-muted/80">{c.area}</div></td>
                <td className="px-3 py-2"><span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink"><span className="h-2 w-2 rounded-full" style={{ background: SEG_COLOR[c.segment] }} />{c.segment}</span>
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
      <Link to="/customers" className="text-sm text-muted hover:text-ink">← Customers</Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">{h.name || h.customer_id}</h1>
          <p className="text-sm text-muted">{h.phone} · {h.area} {h.khata && "· khata customer"}</p>
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

      <div className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
        {[["Bills", num(h.bills)], ["Total spend", inr(h.revenue_rs)], ["Average bill", inr(h.avg_bill_rs)],
          ["Last bill", `${h.recency_days} days ago`], ["Usually every", h.usual_gap_days != null ? `${num(h.usual_gap_days)} days` : "–"],
          ["Spend / month", inr(h.monthly_value_rs)], ["Udhaar owed", u ? inr(u.outstanding_rs) : "–"],
          ["Oldest unpaid", u?.outstanding_rs > 0 ? `${u.days_overdue} days` : "–"]].map(([k, v]) => (
          <div key={k} className="bg-surface p-3"><div className="text-[11px] text-muted">{k}</div>
            <div className="font-semibold tabular-nums text-ink">{v}</div></div>
        ))}
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <Card title="Monthly spend">
          <ChartView height={200} spec={{ kind: "bar", x: "month", unit: "₹", series: [{ key: "revenue_rs", label: "₹" }], data: q.data!.rows }} />
        </Card>
        <Card title="What they buy">
          <ul className="divide-y divide-line/60 text-sm">
            {(h.top_items ?? []).map((t: any) => (
              <li key={t.name} className="flex justify-between py-1.5"><span>{t.name}</span>
                <span className="tabular-nums text-muted">{num(t.bills)} bills · {inr(t.revenue_rs)}</span></li>
            ))}
          </ul>
        </Card>
      </div>
      <p className="mt-4 text-xs text-muted/80">{q.data!.formula}</p>
    </div>
  );
}

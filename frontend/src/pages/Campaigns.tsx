import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Card, ErrorBox, PageHeader, Spinner, useAsync } from "../components/ui";
import { api } from "../lib/api";
import { num, paise, pct } from "../lib/format";
import { usePrefs } from "../lib/prefs";

const STATUS_TONE: Record<string, string> = { draft: "blue", sent: "green", cancelled: "gray" };
const VERDICT_TONE: Record<string, string> = { worked: "green", "no clear effect": "amber", "too few to tell": "gray" };
const SEGMENTS = ["Champions", "Loyal", "New", "Promising", "At risk", "Hibernating", "Needs attention"];

export default function Campaigns() {
  const navigate = useNavigate();
  const list = useAsync(() => api.get<any[]>("/campaigns"), []);
  const [creating, setCreating] = useState(false);
  return (
    <div className="mx-auto max-w-4xl px-4 py-8 md:px-6">
      <PageHeader title="Campaigns" sub="Personal WhatsApp messages to groups of customers - measured against a hold-out group that gets nothing."
        right={<button className="btn btn-dark" onClick={() => setCreating(!creating)}>{creating ? "Close" : "New campaign"}</button>} />
      {creating && <NewCampaign onCreated={(id) => navigate(`/campaigns/${id}`)} />}
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : (
        <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
          {list.data!.length === 0 && <p className="p-4 text-sm text-slate-500">No campaigns yet.</p>}
          {list.data!.map((c) => (
            <Link key={c.campaign_id} to={`/campaigns/${c.campaign_id}`} className="flex items-center gap-4 px-4 py-3 hover:bg-slate-50">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-slate-900">{c.name}</div>
                <div className="truncate text-xs text-slate-500">{c.sample_message}</div>
              </div>
              <div className="shrink-0 text-right text-xs text-slate-500">
                <div><Badge tone={STATUS_TONE[c.status]}>{c.status}</Badge></div>
                <div className="mt-1">{c.targets} messaged · {c.holdout} hold-out</div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function NewCampaign({ onCreated }: { onCreated: (id: string) => void }) {
  const { lang } = usePrefs();
  const goals = useAsync(() => api.get<any[]>("/campaigns/goals"), []);
  const [f, setF] = useState<any>({ goal: "win_back", language: lang, offer: "", segment: "", item_a: "", item_b: "" });
  const [preview, setPreview] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const body = () => ({ goal: f.goal, language: f.language, offer: f.offer,
    segment: f.segment ? { type: "segment", name: f.segment } : null,
    item_a: f.goal === "cross_sell" ? f.item_a : null, item_b: f.goal === "cross_sell" ? f.item_b : null });
  useEffect(() => { setPreview(null); }, [f]);
  const run = async (create: boolean) => {
    setErr(null);
    try {
      if (create) onCreated((await api.post<any>("/campaigns", body())).campaign_id);
      else setPreview(await api.post("/campaigns/preview", body()));
    } catch (e) { setErr(e); }
  };
  return (
    <Card className="mb-6">
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        <label className="flex flex-col gap-1">Goal
          <select className="input" value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })}>
            {(goals.data ?? []).map((g) => <option key={g.goal} value={g.goal}>{g.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Message language
          <select className="input" value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}>
            <option value="hinglish">Hinglish</option><option value="en">English</option><option value="hi">हिंदी</option>
          </select>
        </label>
        {f.goal === "cross_sell" ? (
          <>
            <label className="flex flex-col gap-1">Customers who buy
              <input className="input" placeholder="e.g. aata 5kg" value={f.item_a} onChange={(e) => setF({ ...f, item_a: e.target.value })} /></label>
            <label className="flex flex-col gap-1">…suggest
              <input className="input" placeholder="e.g. sarson ka tel" value={f.item_b} onChange={(e) => setF({ ...f, item_b: e.target.value })} /></label>
          </>
        ) : f.goal !== "udhaar_reminder" && (
          <label className="flex flex-col gap-1">Who (optional)
            <select className="input" value={f.segment} onChange={(e) => setF({ ...f, segment: e.target.value })}>
              <option value="">Default for this goal</option>
              {SEGMENTS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        )}
        {f.goal !== "udhaar_reminder" && (
          <label className="flex flex-col gap-1 sm:col-span-2">Offer line (optional - only if you really offer it)
            <input className="input" maxLength={200} placeholder="e.g. Free home delivery this week." value={f.offer}
              onChange={(e) => setF({ ...f, offer: e.target.value })} /></label>
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <button className="btn" onClick={() => run(false)}>Preview</button>
        <button className="btn btn-dark" onClick={() => run(true)}>Create draft</button>
      </div>
      {err != null && <div className="mt-3"><ErrorBox error={err} /></div>}
      {preview && (
        <div className="mt-4 rounded-lg bg-slate-50 p-3 text-sm">
          <b>{preview.targets}</b> customers get a message, <b>{preview.holdout}</b> are held out.
          {preview.examples.map((m: string, i: number) => <p key={i} className="mt-2 rounded-lg bg-white p-2 text-slate-700 shadow-sm">{m}</p>)}
        </div>
      )}
    </Card>
  );
}

export function CampaignDetail() {
  const { id } = useParams();
  const c = useAsync(() => api.get<any>(`/campaigns/${id}`), [id]);
  const result = useAsync(() => api.get<any>(`/campaigns/${id}/result`), [id, c.data?.status]);
  const [offer, setOffer] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  if (c.loading) return <div className="p-8"><Spinner /></div>;
  if (c.error) return <div className="p-8"><ErrorBox error={c.error} /></div>;
  const k = c.data!;
  const targets = k.members.filter((m: any) => m.group === "target");
  const holdout = k.members.length - targets.length;
  const save = async (patch: any) => {
    setErr(null);
    try { c.setData(await api.patch(`/campaigns/${id}`, patch)); } catch (e) { setErr(e); }
  };
  const status = async (s: string) => {
    if (s === "sent" && !confirm("Mark as sent? Do this after you have sent the messages on WhatsApp.")) return;
    try { c.setData(await api.post(`/campaigns/${id}/status`, { status: s })); } catch (e) { setErr(e); }
  };

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 md:px-6">
      <Link to="/campaigns" className="text-sm text-slate-500 hover:text-slate-900">← Campaigns</Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{k.name}</h1>
          <p className="text-sm text-slate-500">{targets.length} get a message · {holdout} held out ({k.holdout_pct}%) · measured over {k.measure_after_days} days{k.sent_at && ` · sent ${k.sent_at.slice(0, 10)}`}</p>
        </div>
        <Badge tone={STATUS_TONE[k.status]}>{k.status}</Badge>
      </div>

      {k.status === "sent" && result.data && <Result r={result.data} />}

      {k.status === "draft" && (
        <Card className="mt-5" title="Before sending">
          <div className="flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col gap-1">Language
              <select className="input" value={k.language} onChange={(e) => save({ language: e.target.value })}>
                <option value="hinglish">Hinglish</option><option value="en">English</option><option value="hi">हिंदी</option>
              </select></label>
            {k.goal !== "udhaar_reminder" && (
              <label className="flex min-w-[260px] flex-1 flex-col gap-1">Offer line (optional)
                <input className="input" value={offer ?? k.offer} onChange={(e) => setOffer(e.target.value)} maxLength={200} /></label>
            )}
            {offer !== null && offer !== k.offer && <button className="btn btn-dark" onClick={() => { save({ offer }); setOffer(null); }}>Save</button>}
          </div>
          <p className="mt-3 text-xs text-slate-500">{k.note}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <a className="btn" href={`/api/campaigns/${id}/export.csv`}>Download CSV (for broadcast tools)</a>
            <button className="btn btn-dark" onClick={() => status("sent")}>I have sent the messages ✓</button>
            <button className="btn" onClick={() => confirm("Cancel this campaign?") && status("cancelled")}>Cancel</button>
          </div>
        </Card>
      )}
      {err != null && <div className="mt-3"><ErrorBox error={err} /></div>}

      <h2 className="mb-2 mt-6 text-sm font-semibold text-slate-900">Messages ({targets.length})</h2>
      <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
        {targets.slice(0, 100).map((m: any) => (
          <div key={m.customer_id} className="flex items-start gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-slate-900">{m.name || m.customer_id}
                {m.amount_paise != null && <span className="ml-2 text-xs font-normal text-slate-500">owes {paise(m.amount_paise)}</span>}</div>
              <p className="mt-0.5 text-sm text-slate-600">{m.message}</p>
            </div>
            {k.status === "draft" && (
              <a className="btn btn-green shrink-0" href={m.whatsapp_url} target="_blank" rel="noreferrer">WhatsApp</a>
            )}
          </div>
        ))}
      </div>
      {holdout > 0 && <p className="mt-2 text-xs text-slate-500">{holdout} hold-out customers get no message - they show what would have happened anyway.</p>}
    </div>
  );
}

function Result({ r }: { r: any }) {
  if (!r.groups) return <Card className="mt-5"><p className="text-sm text-slate-600">{r.message}</p></Card>;
  const t = r.groups.target, h = r.groups.holdout;
  const max = Math.max(t.rate_pct ?? 0, h.rate_pct ?? 0, 1);
  return (
    <Card className="mt-5" title={<span className="flex items-center gap-2">Did it work?
      <Badge tone={VERDICT_TONE[r.verdict] ?? "gray"}>{r.verdict}</Badge>
      {r.status === "measuring" && <Badge tone="blue">still measuring</Badge>}</span>}>
      <div className="space-y-2 text-sm">
        {[["Got the message", t], ["Hold-out (no message)", h]].map(([label, g]: any) => (
          <div key={label} className="flex items-center gap-3">
            <span className="w-44 shrink-0 text-slate-600">{label}</span>
            <div className="h-6 flex-1 rounded bg-slate-100">
              <div className={`flex h-6 items-center rounded px-2 text-xs font-medium text-white ${g === t ? "bg-slate-900" : "bg-slate-400"}`}
                style={{ width: `${Math.max(8, (100 * (g.rate_pct ?? 0)) / max)}%` }}>{pct(g.rate_pct)}</div>
            </div>
            <span className="w-28 shrink-0 text-right text-xs tabular-nums text-slate-500">{g.converted}/{g.members} came back</span>
          </div>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 text-sm">
        <Stat k="Difference" v={`${r.lift_points > 0 ? "+" : ""}${num(r.lift_points)} points`} />
        <Stat k="Extra sales" v={r.incremental_value_paise != null ? paise(r.incremental_value_paise) : "–"} />
        <Stat k="Chance it's luck (p)" v={r.p_value != null ? String(r.p_value) : "–"} />
      </div>
      {r.caution && <p className="mt-2 text-xs text-amber-700">{r.caution}</p>}
      <p className="mt-2 text-xs text-slate-400">Window {r.window?.[0]} → {r.window?.[1]}. {r.formula}</p>
    </Card>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return <div className="bg-white p-3"><div className="text-[11px] text-slate-500">{k}</div><div className="font-semibold tabular-nums">{v}</div></div>;
}

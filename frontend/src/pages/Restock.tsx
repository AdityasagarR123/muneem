import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import OrderPipeline from "../components/OrderPipeline";
import { Badge, Card, ErrorBox, Spinner, TierBadge, useAsync } from "../components/ui";
import { api, PurchaseOrder, RestockDraft } from "../lib/api";
import { inr, num, paise } from "../lib/format";

const STATUS_TONE: Record<string, string> = { draft: "blue", sent: "amber", received: "green", cancelled: "gray" };
const TABS = ["draft", "sent", "received", "cancelled", "all"] as const;

export default function Restock() {
  const location = useLocation() as { state?: { draftId?: string } };
  const [tab, setTab] = useState<(typeof TABS)[number]>("draft");
  const [notice, setNotice] = useState<string | null>(null);
  const orders = useAsync(() => api.get<PurchaseOrder[]>("/restock/orders", tab === "all" ? {} : { status: tab }), [tab]);
  const suppliers = useAsync(() => api.get("/graph/supplier-dependency"), []);
  const allOrders = useAsync(() => api.get<PurchaseOrder[]>("/restock/orders"), [orders.data]);

  useEffect(() => {
    if (location.state?.draftId) setNotice("New draft created from your request - review the quantities, then send.");
  }, [location.state?.draftId]);

  const onChanged = (po: PurchaseOrder, msg?: string) => {
    orders.setData((list) => (list ?? []).map((p) => (p.po_id === po.po_id ? po : p)));
    if (msg) setNotice(msg);
  };

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-bold text-ink">Restock</h1>
        <p className="text-sm text-muted">
          Muneem drafts the orders and messages. <b>You</b> check them and send them yourself.
        </p>
      </header>

      <NewDraft suppliers={suppliers.data?.rows ?? []} onCreated={(d) => {
        setTab("draft");
        orders.reload();
        setNotice(d.orders.length ? `Drafted ${d.orders.length} order(s), total ${paise(d.total_incl_gst_paise)} incl. GST` +
          (d.deferred.length ? ` · ${d.deferred.length} item(s) did not fit the budget` : "") : "Nothing needs ordering right now.");
      }} />

      {allOrders.data && allOrders.data.length > 0 && (
        <OrderPipeline orders={[...allOrders.data].sort((a, b) =>
          (["draft", "sent", "received", "cancelled"].indexOf(a.status) - ["draft", "sent", "received", "cancelled"].indexOf(b.status))
          || b.created_at.localeCompare(a.created_at))} />
      )}

      {notice && <div className="rounded-md border border-accent/30 bg-accent-soft p-3 text-sm text-accent">{notice}</div>}

      <div className="flex flex-wrap gap-1 border-b border-line">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium capitalize ${tab === t ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink"}`}>
            {t}
          </button>
        ))}
        <a className="ml-auto self-center text-sm text-accent hover:underline" href="/api/restock/export.csv">
          Download fresh order list (CSV)
        </a>
      </div>

      {orders.loading ? <Spinner /> : orders.error ? <ErrorBox error={orders.error} onRetry={orders.reload} /> :
        orders.data!.length === 0 ? <p className="text-sm text-muted">No {tab === "all" ? "" : tab} orders.</p> :
          <div className="space-y-4">{orders.data!.map((po) => <OrderCard key={po.po_id} po={po} onChanged={onChanged} />)}</div>}
    </div>
  );
}

function NewDraft({ suppliers, onCreated }: { suppliers: any[]; onCreated: (d: RestockDraft) => void }) {
  const [budget, setBudget] = useState("");
  const [supplier, setSupplier] = useState("");
  const [language, setLanguage] = useState("hinglish");
  const [showPrices, setShowPrices] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function create() {
    setBusy(true);
    setErr(null);
    try {
      onCreated(await api.post<RestockDraft>("/restock/draft", {
        budget_rs: budget ? Number(budget) : null, supplier: supplier || null, language, show_prices: showPrices,
      }));
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <Card title="Make a new restock list">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">Budget (₹, optional)
          <input className="input w-36" type="number" min={0} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="no limit" />
        </label>
        <label className="flex flex-col gap-1">Supplier
          <select className="input w-56" value={supplier} onChange={(e) => setSupplier(e.target.value)}>
            <option value="">All suppliers</option>
            {suppliers.map((s) => <option key={s.supplier_id} value={s.supplier_id}>{s.supplier}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Message language
          <select className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="hinglish">Hinglish</option><option value="en">English</option><option value="hi">हिंदी</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 pb-1.5">
          <input type="checkbox" checked={showPrices} onChange={(e) => setShowPrices(e.target.checked)} /> Show estimated total
        </label>
        <button className="btn btn-primary" onClick={create} disabled={busy}>{busy ? "Working…" : "Create draft"}</button>
      </div>
      {err != null && <div className="mt-2"><ErrorBox error={err} /></div>}
    </Card>
  );
}

function OrderCard({ po, onChanged }: { po: PurchaseOrder; onChanged: (po: PurchaseOrder, msg?: string) => void }) {
  const [qty, setQty] = useState<Record<string, number>>(() => Object.fromEntries(po.lines.map((l) => [l.item_id, l.qty])));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setQty(Object.fromEntries(po.lines.map((l) => [l.item_id, l.qty])));
  }, [po]);

  const dirty = po.lines.some((l) => qty[l.item_id] !== l.qty);
  const isDraft = po.status === "draft";

  async function act(fn: () => Promise<PurchaseOrder>, msg?: string) {
    setBusy(true);
    setErr(null);
    try { onChanged(await fn(), msg); } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  const save = () => act(() => api.patch(`/restock/orders/${po.po_id}`, { quantities: qty }));
  const setLang = (language: string) => act(() => api.patch(`/restock/orders/${po.po_id}`, { language }));
  const status = (s: string, msg: string) => act(() => api.post(`/restock/orders/${po.po_id}/status`, { status: s }), msg);

  return (
    <Card title={
      <span className="flex flex-wrap items-center gap-2">
        {po.supplier} <Badge tone={STATUS_TONE[po.status]}>{po.status}</Badge>
        <span className="text-xs font-normal text-muted/80">{po.po_id} · delivery by {po.expected_delivery}</span>
      </span>}
      right={<span className="text-sm font-semibold">{paise(po.total_incl_gst_paise)} <span className="font-normal text-muted">incl. GST</span></span>}>
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="overflow-x-auto lg:col-span-3">
          <table className="w-full text-sm">
            <thead><tr className="border-b text-left text-xs uppercase text-muted">
              <th className="py-1.5 pr-2">Item</th><th className="pr-2">Status</th><th className="pr-2">Days left</th>
              <th className="pr-2">Qty</th><th className="pr-2 text-right">₹ incl. GST</th>
            </tr></thead>
            <tbody>
              {po.lines.map((l) => (
                <tr key={l.item_id} className="border-b border-line/60" title={l.why}>
                  <td className="py-1.5 pr-2">{l.name}
                    {l.edited && <span className="ml-1 text-[11px] text-muted/80">(suggested {l.suggested_qty})</span>}</td>
                  <td className="pr-2"><TierBadge tier={l.tier} /></td>
                  <td className="pr-2">{num(l.days_left)}</td>
                  <td className="pr-2">
                    {isDraft ? (
                      <input type="number" min={0} step={l.pack_size} className="input w-20 py-0.5"
                        value={qty[l.item_id] ?? 0} onChange={(e) => setQty({ ...qty, [l.item_id]: Math.max(0, Number(e.target.value)) })} />
                    ) : l.qty}
                    {l.pack_size > 1 && <span className="ml-1 text-[11px] text-muted/80">pack {l.pack_size}</span>}
                  </td>
                  <td className="pr-2 text-right">{inr(l.value_incl_gst_paise / 100)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {isDraft && dirty && (
            <div className="mt-2 flex gap-2">
              <button className="btn btn-primary" onClick={save} disabled={busy}>Save quantities</button>
              <button className="btn" onClick={() => setQty(Object.fromEntries(po.lines.map((l) => [l.item_id, l.qty])))}>Undo</button>
            </div>
          )}
        </div>
        <div className="lg:col-span-2">
          <div className="mb-1 flex items-center justify-between text-xs text-muted">
            <span>WhatsApp message</span>
            {isDraft && (
              <select className="input py-0 text-xs" value={po.language} onChange={(e) => setLang(e.target.value)} disabled={busy}>
                <option value="hinglish">Hinglish</option><option value="en">English</option><option value="hi">हिंदी</option>
              </select>
            )}
          </div>
          <pre className="whitespace-pre-wrap rounded-md border border-green-200 bg-green-50 p-3 font-sans text-sm text-ink">{po.message}</pre>
          <div className="mt-2 flex flex-wrap gap-2">
            <a className="btn btn-green" href={po.whatsapp_url} target="_blank" rel="noreferrer">Open in WhatsApp</a>
            <button className="btn" onClick={() => { navigator.clipboard.writeText(po.message); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
              {copied ? "Copied ✓" : "Copy"}
            </button>
            <a className="btn" href={`/api/restock/export.csv?po_id=${po.po_id}`}>CSV</a>
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line/60 pt-3">
        {po.status === "draft" && <button className="btn btn-primary" disabled={busy || dirty}
          onClick={() => status("sent", `${po.supplier}: marked as sent.`)}>I have sent it ✓</button>}
        {po.status === "sent" && <button className="btn btn-green" disabled={busy}
          onClick={() => confirm(`Goods from ${po.supplier} arrived? This adds the quantities to stock.`) &&
            status("received", `${po.supplier}: received - stock updated, alerts recalculated.`)}>Goods received</button>}
        {(po.status === "draft" || po.status === "sent") && <button className="btn" disabled={busy}
          onClick={() => confirm("Cancel this order?") && status("cancelled", "Order cancelled.")}>Cancel order</button>}
        <span className="ml-auto text-xs text-muted/80">
          {po.history.map((h) => `${h.status} ${new Date(h.at).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}`).join(" → ")}
        </span>
      </div>
      {err != null && <div className="mt-2"><ErrorBox error={err} /></div>}
    </Card>
  );
}

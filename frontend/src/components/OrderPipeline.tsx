import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Box, Check, ChevronDown, Circle, Clock3, FilePen, List, PackageCheck, Send, Truck, X } from "lucide-react";
import type { PurchaseOrder } from "../lib/api";
import { paise } from "../lib/format";

/* Order lifecycle drawn as connected isometric blocks (adapted from the BuildPipeline design).
   Read-only: it shows what happened to each supplier order; actions stay on the order card. */

type State = "done" | "waiting" | "late" | "stopped" | "todo";
type Stage = { id: string; name: string; icon: typeof Circle; state: State; when?: string; logs: string[] };

const LABEL: Record<State, string> = { done: "Done", waiting: "Waiting", late: "Late", stopped: "Cancelled", todo: "Not yet" };
const daysBetween = (a: Date, b: Date) => Math.round((b.getTime() - a.getTime()) / 86_400_000);
const fmt = (iso?: string) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "");

function stagesOf(po: PurchaseOrder): Stage[] {
  const at = (s: string) => po.history.find((h) => h.status === s)?.at;
  const sent = at("sent"), received = at("received"), cancelled = at("cancelled");
  const recEntry = po.history.find((h) => h.status === "received");
  const late = !received && !cancelled && !!sent && new Date(po.expected_delivery + "T23:59:59") < new Date();
  const items = po.lines.length;
  const stop = (reached: boolean): State => (cancelled && !reached ? "stopped" : "todo");
  const short = recEntry?.received_qty
    ? po.lines.filter((l) => (recEntry.received_qty![l.item_id] ?? l.qty) < l.qty) : [];
  return [
    { id: "draft", name: "Drafted", icon: FilePen, state: "done", when: po.created_at,
      logs: [`${items} item(s), ${paise(po.total_incl_gst_paise)} incl. GST`, `Message ready in ${po.language}`,
        ...po.lines.slice(0, 3).map((l) => `${l.name}: ${l.qty} pcs`)] },
    { id: "sent", name: "Sent", icon: Send, state: sent ? "done" : cancelled ? "stopped" : "waiting", when: sent,
      logs: sent ? [`Owner sent the WhatsApp message`, `Delivery expected by ${po.expected_delivery} (${po.lead_time_days} day lead time)`]
        : cancelled ? ["Cancelled before sending"] : ["Waiting for you to send the message to " + po.supplier] },
    { id: "delivered", name: "Delivered", icon: Truck, when: received,
      state: received ? "done" : late ? "late" : sent ? "waiting" : stop(false),
      logs: received ? [`Received ${fmt(received)}`, short.length ? `${short.length} item(s) came short` : "All quantities as ordered"]
        : late ? [`Expected by ${po.expected_delivery} - ${daysBetween(new Date(po.expected_delivery), new Date())} day(s) late`, `Call ${po.supplier}`]
        : sent ? [`Expected by ${po.expected_delivery}`] : ["Not sent yet"] },
    { id: "stock", name: "Stock updated", icon: PackageCheck, when: received, state: received ? "done" : stop(false),
      logs: received ? po.lines.slice(0, 4).map((l) => `+${recEntry?.received_qty?.[l.item_id] ?? l.qty} ${l.name}`)
        : ["Stock is added when you mark the order received"] },
  ];
}

function layout(count: number, width: number) {
  const cols = Math.max(1, Math.min(count, Math.floor((width - 16) / 120)));
  const rows = Math.ceil(count / cols);
  return {
    height: Math.max(150, rows * 132 + 14),
    nodes: Array.from({ length: count }, (_, i) => {
      const row = Math.floor(i / cols), col = row % 2 ? cols - 1 - (i % cols) : i % cols;
      const x = (col + 0.5) * width / cols, y = 76 + row * 132;
      const base = [[x, y - 18], [x + 30, y - 4], [x, y + 10], [x - 30, y - 4]];
      return { x, y, base, roof: base.map(([px, py]) => [px, py - 24]) };
    }),
  };
}
const pts = (v: number[][]) => v.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ");

export default function OrderPipeline({ orders }: { orders: PurchaseOrder[] }) {
  const id = useId();
  const [poId, setPoId] = useState(orders[0]?.po_id);
  const [pick, setPick] = useState<string | null>(null);
  const [view, setView] = useState<"blocks" | "list">("blocks");
  const [width, setWidth] = useState(640);
  const ref = useRef<HTMLDivElement>(null);
  const po = orders.find((o) => o.po_id === poId) ?? orders[0];
  const stages = useMemo(() => (po ? stagesOf(po) : []), [po]);
  const scene = useMemo(() => layout(stages.length, width), [stages.length, width]);
  const current = stages.find((s) => s.state === "late") ?? stages.find((s) => s.state === "waiting") ?? stages[stages.length - 1];
  const selected = stages.find((s) => s.id === pick) ?? current;
  const done = stages.filter((s) => s.state === "done").length;
  const overall: State = po?.status === "cancelled" ? "stopped" : stages.some((s) => s.state === "late") ? "late"
    : po?.status === "received" ? "done" : "waiting";

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ob = new ResizeObserver(() => setWidth(Math.max(240, el.getBoundingClientRect().width)));
    ob.observe(el);
    return () => ob.disconnect();
  }, [view, po?.po_id]);

  if (!po) return null;
  const age = daysBetween(new Date(po.created_at), new Date());

  return (
    <section className="op" aria-label="Order progress">
      <style>{CSS}</style>
      <header className="op-head">
        <div className="op-title"><span className="op-emblem"><Truck size={17} strokeWidth={1.7} /></span>
          <div><h2>Order progress</h2><p>From draft to stock on the shelf.</p></div></div>
        <div className="op-tools">
          <label className="op-select"><select value={po.po_id} onChange={(e) => { setPoId(e.target.value); setPick(null); }}>
            {orders.map((o) => <option key={o.po_id} value={o.po_id}>{o.supplier} · {o.status}</option>)}
          </select><ChevronDown size={12} /></label>
          <div className="op-switch" role="group" aria-label="View">
            <button aria-pressed={view === "blocks"} onClick={() => setView("blocks")}><Box size={14} />Blocks</button>
            <button aria-pressed={view === "list"} onClick={() => setView("list")}><List size={14} />List</button>
          </div>
        </div>
      </header>

      <div className="op-stats">
        <div><span>Status</span><strong data-state={overall}>{overall === "waiting" ? (po.status === "draft" ? "Not sent" : "On the way") : LABEL[overall]}</strong><small>{po.po_id}</small></div>
        <div><span>Order value</span><strong>{paise(po.total_incl_gst_paise)}</strong><small>{po.lines.length} items incl. GST</small></div>
        <div><span>Steps done</span><strong>{done}<em> / {stages.length}</em></strong><small>drafted {age === 0 ? "today" : `${age} day(s) ago`}</small></div>
      </div>

      {view === "blocks" ? (
        <div className="op-plot" ref={ref} style={{ height: scene.height }}>
          <svg width="100%" height={scene.height} viewBox={`0 0 ${width} ${scene.height}`} role="img" aria-label={stages.map((s) => `${s.name}: ${LABEL[s.state]}`).join(". ")}>
            <defs><marker id={`${id}-a`} markerWidth={5} markerHeight={5} refX={4} refY={2.5} orient="auto"><path d="M0,0 L5,2.5 L0,5 Z" fill="rgb(var(--line))" /></marker></defs>
            {scene.nodes.slice(0, -1).map((n, i) => {
              const nx = scene.nodes[i + 1], dir = nx.x > n.x ? 1 : -1;
              const d = nx.y === n.y ? `M${n.x + dir * 36},${n.y - 28} L${nx.x - dir * 37},${nx.y - 28}`
                : `M${n.x + (n.x > width / 2 ? 36 : -36)},${n.y - 28} H${n.x + (n.x > width / 2 ? 55 : -55)} V${nx.y - 28} H${nx.x + (n.x > width / 2 ? 37 : -37)}`;
              return <path key={i} d={d} fill="none" stroke="rgb(var(--line))" strokeWidth={1.4}
                strokeDasharray={stages[i + 1].state === "done" ? undefined : "3 4"} markerEnd={`url(#${id}-a)`} />;
            })}
            {scene.nodes.map((n, i) => (
              <g key={stages[i].id} className="op-block" data-state={stages[i].state}
                style={{ transform: selected?.id === stages[i].id ? "translateY(-5px)" : "none" }}>
                <polygon points={pts(n.base.map(([x, y]) => [x, y + 5]))} className="op-ground" />
                <polygon className="op-l" points={pts([n.roof[3], n.roof[2], n.base[2], n.base[3]])} />
                <polygon className="op-r" points={pts([n.roof[1], n.roof[2], n.base[2], n.base[1]])} />
                <polygon className="op-roof" points={pts(n.roof)} />
              </g>
            ))}
          </svg>
          {scene.nodes.map((n, i) => {
            const s = stages[i], Icon = s.icon, SI = s.state === "done" ? Check : s.state === "stopped" ? X : s.state === "todo" ? Circle : Clock3;
            return (
              <button key={s.id} className="op-station" data-state={s.state} aria-pressed={selected?.id === s.id}
                style={{ left: n.x - 56, top: n.y - 48 }} onClick={() => setPick(s.id)}>
                <span className="op-icon"><Icon size={17} strokeWidth={1.6} /></span>
                <strong>{s.name}</strong>
                <span className="op-state"><SI size={10} />{LABEL[s.state]}</span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="op-list">
          {stages.map((s) => {
            const Icon = s.icon;
            return (
              <button key={s.id} className="op-row" data-state={s.state} aria-pressed={selected?.id === s.id} onClick={() => setPick(s.id)}>
                <Icon size={16} /><span><strong>{s.name}</strong><small>{s.when ? fmt(s.when) : "–"}</small></span>
                <span className="op-row-state">{LABEL[s.state]}</span>
              </button>
            );
          })}
        </div>
      )}

      {selected && (
        <div className="op-log">
          <div className="op-log-head"><strong>{selected.name}</strong><span data-state={selected.state}>{LABEL[selected.state]}</span>
            {selected.when && <time>{fmt(selected.when)}</time>}</div>
          <ul>{selected.logs.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </div>
      )}
    </section>
  );
}

const CSS = `
.op{--ok:47 111 79;--ok-fill:206 226 213;--wait:176 128 40;--wait-fill:242 228 196;--bad:178 74 56;--bad-fill:240 213 205;--off-fill:226 223 216;
  background:rgb(var(--surface));border:1px solid rgb(var(--line));border-radius:14px;box-shadow:0 1px 3px rgb(30 25 15/.06);overflow:hidden;font-size:13px;color:rgb(var(--ink))}
.op button,.op select{font:inherit;color:inherit;cursor:pointer}
.op-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:20px 22px 0}
.op-title{display:flex;gap:11px;align-items:center}.op-title h2{font-size:15px;font-weight:600;margin:0}.op-title p{font-size:12px;color:rgb(var(--muted));margin:2px 0 0}
.op-emblem{width:34px;height:34px;border:1px solid rgb(var(--line));border-radius:9px;display:grid;place-items:center;background:rgb(var(--canvas))}
.op-tools{display:flex;gap:8px;align-items:center}
.op-select{position:relative;display:flex;align-items:center}.op-select select{appearance:none;background:rgb(var(--surface));border:1px solid rgb(var(--line));border-radius:7px;height:32px;padding:0 26px 0 9px;font-size:12px;max-width:220px}
.op-select svg{position:absolute;right:9px;pointer-events:none;color:rgb(var(--muted))}
.op-switch{display:flex;gap:2px;padding:3px;background:rgb(var(--canvas));border:1px solid rgb(var(--line));border-radius:8px}
.op-switch button{display:flex;align-items:center;gap:5px;border:0;background:transparent;border-radius:5px;height:25px;padding:0 8px;font-size:12px;color:rgb(var(--muted))}
.op-switch button[aria-pressed=true]{background:rgb(var(--ink));color:rgb(var(--surface))}
.op-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:22px 22px 6px}
.op-stats>div{display:flex;flex-direction:column}.op-stats>div+div{padding-left:18px;border-left:1px solid rgb(var(--line))}
.op-stats span,.op-stats small{color:rgb(var(--muted));font-size:12px}.op-stats small{font-size:11px}
.op-stats strong{font-size:21px;font-weight:600;letter-spacing:-.5px;margin:2px 0;font-variant-numeric:tabular-nums;white-space:nowrap}
.op-stats em{font-size:14px;font-style:normal;font-weight:400;color:rgb(var(--muted))}
.op-stats strong[data-state=done]{color:rgb(var(--ok))}.op-stats strong[data-state=waiting]{color:rgb(var(--wait))}.op-stats strong[data-state=late],.op-stats strong[data-state=stopped]{color:rgb(var(--bad))}
.op-plot{position:relative;margin:0 10px}
.op-block{--top:var(--off-fill);transition:transform .2s ease}.op-block[data-state=done]{--top:var(--ok-fill)}.op-block[data-state=waiting]{--top:var(--wait-fill)}.op-block[data-state=late],.op-block[data-state=stopped]{--top:var(--bad-fill)}
.op-roof{fill:rgb(var(--top))}.op-l{fill:color-mix(in srgb,rgb(var(--top)) 88%,rgb(var(--ink)) 12%)}.op-r{fill:color-mix(in srgb,rgb(var(--top)) 74%,rgb(var(--ink)) 26%)}.op-ground{fill:rgb(var(--canvas))}
.op-station{position:absolute;width:112px;height:106px;border:0;background:transparent;padding:0;display:flex;flex-direction:column;align-items:center;border-radius:8px}
.op-icon{height:40px;display:flex;align-items:center;justify-content:center;color:rgb(var(--muted));transition:transform .18s}
.op-station:hover .op-icon{transform:translateY(-2px)}
.op-station[data-state=done] .op-icon{color:rgb(var(--ok))}.op-station[data-state=waiting] .op-icon{color:rgb(var(--wait))}.op-station[data-state=late] .op-icon,.op-station[data-state=stopped] .op-icon{color:rgb(var(--bad))}
.op-station strong{font-size:12px;font-weight:600;margin-top:24px}
.op-state{display:flex;align-items:center;gap:4px;font-size:11px;color:rgb(var(--muted))}
.op-station[data-state=done] .op-state svg{color:rgb(var(--ok))}.op-station[data-state=late] .op-state,.op-station[data-state=stopped] .op-state{color:rgb(var(--bad))}
.op-station[aria-pressed=true] strong{text-decoration:underline;text-underline-offset:3px}
.op-list{padding:10px 22px 14px}.op-row{display:flex;align-items:center;gap:10px;width:100%;min-height:50px;padding:8px;background:transparent;border:0;border-radius:6px;text-align:left}
.op-row+.op-row{border-top:1px solid rgb(var(--line))}.op-row:hover,.op-row[aria-pressed=true]{background:rgb(var(--canvas))}
.op-row>span:nth-child(2){flex:1}.op-row strong{display:block;font-size:12px;font-weight:600}.op-row small{color:rgb(var(--muted));font-size:11px}
.op-row-state{font-size:11px;color:rgb(var(--muted))}.op-row[data-state=done] .op-row-state{color:rgb(var(--ok))}.op-row[data-state=waiting] .op-row-state{color:rgb(var(--wait))}.op-row[data-state=late] .op-row-state,.op-row[data-state=stopped] .op-row-state{color:rgb(var(--bad))}
.op-log{margin:4px 22px 20px;border:1px solid rgb(var(--line));border-radius:8px;background:rgb(var(--canvas))}
.op-log-head{display:flex;align-items:center;gap:8px;padding:8px 11px;border-bottom:1px solid rgb(var(--line));font-size:12px}
.op-log-head span{font-size:11px;color:rgb(var(--muted))}.op-log-head span[data-state=done]{color:rgb(var(--ok))}.op-log-head span[data-state=waiting]{color:rgb(var(--wait))}.op-log-head span[data-state=late]{color:rgb(var(--bad))}
.op-log-head time{margin-left:auto;font-size:11px;color:rgb(var(--muted))}
.op-log ul{margin:0;padding:9px 11px;list-style:none}.op-log li{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11.5px;line-height:1.7}
@media(max-width:560px){.op-head{padding:16px 16px 0}.op-stats{margin:18px 16px 6px}.op-stats>div+div{padding-left:10px}.op-stats strong{font-size:18px}.op-log{margin-inline:16px}}
@media(prefers-reduced-motion:reduce){.op *{transition:none!important}}
`;

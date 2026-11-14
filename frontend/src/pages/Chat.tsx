import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import EvidencePanel from "../components/EvidencePanel";
import VisualView from "../components/VisualView";
import { Badge } from "../components/ui";
import { Envelope, streamChat } from "../lib/api";
import { RUNNABLE, useActionRunner } from "../lib/actions";

type Msg = {
  role: "user" | "assistant";
  text: string;
  steps?: string[];
  env?: Envelope;
  streaming?: boolean;
  error?: string;
};

const STARTERS = [
  "Kal kya order karna chahiye?",
  "Kis supplier ko pehle call karu?",
  "Which items haven't sold in 30 days?",
  "Sharma Traders 3 din late ho gaya to kya hoga?",
  "Atta ke saath kya bikta hai?",
  "Mithai ki bikri dikhao",
  "Supply flow dikhao",
  "Sharma ji ko order ka WhatsApp message likh do",
];

const TOOL_LABEL: Record<string, string> = {
  get_kpis: "reading business summary", stock_status: "checking stock", reorder_plan: "working out the order",
  dead_stock: "finding dead stock", slow_movers: "finding slow items", margin_report: "calculating margins",
  payment_mix: "checking payments", sales_trend: "reading sales trend", top_items: "ranking items",
  expense_report: "checking expenses", supplier_priority: "ranking suppliers (graph)",
  supplier_risk: "simulating supplier delay (graph)", supplier_dependency: "checking supplier dependence (graph)",
  bought_together: "finding items bought together (graph)", substitutes: "finding substitutes (graph)",
  supply_path: "drawing supply path (graph)", supply_flow: "drawing supply flow (graph)",
  money_flow: "drawing money flow", restock_draft: "drafting purchase orders", morning_brief: "preparing brief",
  find_item: "looking up names",
};

export default function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"auto" | "rules">("auto");
  const conv = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  const update = (fn: (m: Msg) => Msg) =>
    setMsgs((all) => [...all.slice(0, -1), fn(all[all.length - 1])]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setMsgs((m) => [...m, { role: "user", text: q }, { role: "assistant", text: "", steps: [], streaming: true }]);
    try {
      await streamChat({ message: q, conversation_id: conv.current, mode }, (ev, data) => {
        if (ev === "meta") conv.current = data.conversation_id;
        else if (ev === "tool_call") update((m) => ({ ...m, steps: [...(m.steps ?? []), TOOL_LABEL[data.tool] ?? data.tool] }));
        else if (ev === "token") update((m) => ({ ...m, text: m.text + data.text }));
        else if (ev === "final") update((m) => ({ ...m, text: data.answer_md, env: data, streaming: false }));
      });
    } catch (e) {
      update((m) => ({ ...m, streaming: false, error: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  }

  const onSubmit = (e: FormEvent) => { e.preventDefault(); send(input); };

  return (
    <div className="mx-auto flex h-[calc(100vh-3rem)] max-w-4xl flex-col">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Ask Muneem</h1>
          <p className="text-sm text-slate-500">English, Hinglish or हिंदी · every number is computed, never guessed</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <label className="text-slate-500">Mode</label>
          <select className="input py-1" value={mode} onChange={(e) => setMode(e.target.value as any)}>
            <option value="auto">AI (Gemini)</option>
            <option value="rules">Rules only (offline)</option>
          </select>
          {msgs.length > 0 && <button className="btn" onClick={() => { setMsgs([]); conv.current = null; }}>New chat</button>}
        </div>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto rounded-lg border border-slate-200 bg-white p-4">
        {msgs.length === 0 && (
          <div className="py-8 text-center">
            <div className="text-4xl">📒</div>
            <p className="mt-2 text-slate-600">Namaste! Ask about orders, stock, suppliers, margins or sales.</p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {STARTERS.map((s) => <button key={s} className="btn" onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {msgs.map((m, i) => m.role === "user"
          ? <div key={i} className="flex justify-end"><div className="max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-white">{m.text}</div></div>
          : <AssistantMsg key={i} m={m} onAsk={send} />)}
        <div ref={bottom} />
      </div>

      <form onSubmit={onSubmit} className="mt-3 flex gap-2">
        <input className="input flex-1 py-2" value={input} onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. Budget ₹10,000 hai, kya order karu?" maxLength={500} disabled={busy} />
        <button className="btn btn-primary px-4" disabled={busy || !input.trim()}>{busy ? "…" : "Send"}</button>
      </form>
    </div>
  );
}

function AssistantMsg({ m, onAsk }: { m: Msg; onAsk: (q: string) => void }) {
  const env = m.env;
  const run = useActionRunner(env?.language === "en" ? "en" : env?.language === "hi" ? "hi" : "hinglish");
  const [running, setRunning] = useState<string | null>(null);
  return (
    <div className="max-w-full">
      {m.steps && m.steps.length > 0 && (
        <div className="mb-1 flex flex-wrap gap-1">
          {m.steps.map((s, i) => <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">⚙ {s}</span>)}
        </div>
      )}
      {m.error ? (
        <div className="rounded-md bg-red-50 p-2 text-sm text-red-700">Something went wrong: {m.error}</div>
      ) : (
        <div className="prose-chat text-[15px] leading-relaxed">
          {m.text ? <ReactMarkdown>{m.text}</ReactMarkdown> : m.streaming && <span className="text-slate-400">Thinking…</span>}
        </div>
      )}
      {env && (
        <>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <Badge tone={env.mode === "llm" ? "blue" : "gray"}>{env.mode === "llm" ? env.model ?? "AI" : "rules"}</Badge>
            {env.grounding.checked > 0 && (env.grounding.ungrounded.length === 0
              ? <Badge tone="green" title="Every number in the answer was found in the computed data">✓ {env.grounding.checked} numbers verified</Badge>
              : <Badge tone="amber" title={env.grounding.ungrounded.join(", ")}>⚠ {env.grounding.ungrounded.length} unverified number(s)</Badge>)}
            {env.confidence === "insufficient_data" && <Badge tone="amber">not enough history</Badge>}
            <span className="text-[11px] text-slate-400">{(env.latency_ms / 1000).toFixed(1)}s</span>
          </div>
          {env.warnings.map((w, i) => <p key={i} className="mt-1 text-xs text-amber-700">⚠ {w}</p>)}
          {env.visuals.length > 0 && (
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              {env.visuals.map((v, i) => (
                <div key={i} className={v.type === "table" || v.type === "mermaid" || v.type === "kpis" ? "lg:col-span-2" : ""}>
                  <VisualView v={v} />
                </div>
              ))}
            </div>
          )}
          {env.actions.filter((a) => RUNNABLE.has(a.type)).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {env.actions.filter((a) => RUNNABLE.has(a.type)).map((a, i) => (
                <button key={i} className={`btn ${a.type === "open_whatsapp" ? "btn-green" : a.type === "restock_draft" ? "btn-primary" : ""}`}
                  disabled={running !== null}
                  onClick={async () => { setRunning(a.label); try { await run(a); } finally { setRunning(null); } }}>
                  {running === a.label ? "…" : a.label}
                </button>
              ))}
            </div>
          )}
          <EvidencePanel evidence={env.evidence} />
          {env.suggestions.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {env.suggestions.map((s) => (
                <button key={s} className="rounded-full border border-slate-200 px-2.5 py-0.5 text-xs text-slate-600 hover:bg-slate-50" onClick={() => onAsk(s)}>{s}</button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

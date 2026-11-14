import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { usePrefs } from "../lib/prefs";
import Icon, { Logo } from "./Icon";

const WORKSPACE = [
  { to: "/", label: "Home", icon: "chat", end: true },
  { to: "/insights", label: "Insights", icon: "spark" },
  { to: "/customers", label: "Customers", icon: "users" },
  { to: "/campaigns", label: "Campaigns", icon: "megaphone" },
  { to: "/orders", label: "Stock & orders", icon: "box" },
  { to: "/business", label: "Business", icon: "chart" },
  { to: "/data", label: "Data", icon: "database" },
];

type Chat = { conversation_id: string; title: string };

export default function Sidebar({ chats, status, insightCount, onNavigate }: {
  chats: Chat[]; status: any; insightCount?: number; onNavigate?: () => void;
}) {
  const navigate = useNavigate();
  const loc = useLocation();
  const { lang, setLang } = usePrefs();
  const item = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 rounded-lg px-3 py-2 text-[15px] ${isActive
      ? "bg-surface-2 font-medium text-ink" : "text-ink/80 hover:bg-surface-2"}`;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-5 pb-4 pt-5">
        <Logo />
        <div>
          <div className="text-lg font-semibold leading-tight tracking-tight text-ink">Muneem</div>
          <div className="text-[11px] leading-tight text-muted">hisaab bhi, salaah bhi</div>
        </div>
      </div>

      <div className="px-4">
        <button onClick={() => { navigate("/", { state: { fresh: Date.now() } }); onNavigate?.(); }}
          className="flex w-full items-center justify-center gap-2 rounded-xl border border-line bg-surface px-3 py-2.5 text-[15px] font-medium text-ink shadow-sm hover:bg-surface-2">
          <Icon name="plus" /> New chat
        </button>
      </div>

      <div className="mt-6 px-5 text-[11px] font-semibold uppercase tracking-wider text-muted/80">Workspace</div>
      <nav className="mt-2 space-y-0.5 px-3">
        {WORKSPACE.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={item} onClick={onNavigate}>
            <Icon name={n.icon} className="text-muted" />
            <span className="flex-1">{n.label}</span>
            {n.to === "/insights" && insightCount ? (
              <span className="rounded-full bg-ink px-1.5 text-[11px] font-medium text-white">{insightCount}</span>
            ) : null}
          </NavLink>
        ))}
      </nav>

      <div className="mx-5 my-4 border-t border-line" />
      <div className="px-5 text-[11px] font-semibold uppercase tracking-wider text-muted/80">Recent chats</div>
      <div className="mt-2 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-3">
        {chats.length === 0 && <p className="px-2 text-sm text-muted/80">No chats yet</p>}
        {chats.map((c) => {
          const active = loc.pathname === `/c/${c.conversation_id}`;
          return (
            <NavLink key={c.conversation_id} to={`/c/${c.conversation_id}`} onClick={onNavigate}
              className={`block truncate rounded-lg px-3 py-1.5 text-sm ${active ? "bg-surface-2 text-ink" : "text-ink/80 hover:bg-surface-2"}`}>
              {c.title}
            </NavLink>
          );
        })}
      </div>

      <div className="space-y-3 border-t border-line px-5 py-4">
        <div className="flex items-center justify-between gap-2 text-xs text-muted">
          <span>Language</span>
          <div className="flex overflow-hidden rounded-lg border border-line bg-surface">
            {(["en", "hinglish", "hi"] as const).map((l) => (
              <button key={l} onClick={() => setLang(l)}
                className={`px-2 py-1 ${lang === l ? "bg-ink text-white" : "text-ink/80 hover:bg-surface-2"}`}>
                {l === "en" ? "EN" : l === "hinglish" ? "Hinglish" : "हिं"}
              </button>
            ))}
          </div>
        </div>
        {status && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted/80">
            <span>Data as of {status.dataset?.as_of ?? "–"}</span>
            <Dot ok={status.postgres?.status === "connected"} label="Postgres" />
            <Dot ok={status.arango?.status === "connected"} label="ArangoDB" />
          </div>
        )}
      </div>
    </div>
  );
}

function Dot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-green-500" : "bg-slate-300"}`} />{label}
    </span>
  );
}

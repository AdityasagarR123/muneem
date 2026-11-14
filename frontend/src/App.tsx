import { NavLink, Route, Routes } from "react-router-dom";
import { Badge, useAsync } from "./components/ui";
import { api } from "./lib/api";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import Data from "./pages/Data";
import Restock from "./pages/Restock";

const NAV = [
  { to: "/", label: "Dashboard", icon: "📊" },
  { to: "/chat", label: "Ask Muneem", icon: "💬" },
  { to: "/restock", label: "Restock", icon: "🛒" },
  { to: "/data", label: "Data", icon: "📁" },
];

export default function App() {
  const status = useAsync(() => Promise.all([api.get("/status"), api.get("/agent/status")]), []);
  const [st, agent] = status.data ?? [null, null];

  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-slate-200 bg-white md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:border-b-0 md:border-r">
        <div className="flex items-center gap-2 px-4 py-3 md:py-5">
          <span className="text-2xl">📒</span>
          <div>
            <div className="font-bold leading-tight text-slate-900">Muneem</div>
            <div className="text-[11px] leading-tight text-slate-500">hisaab bhi, salaah bhi</div>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:pb-0">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium ${
                  isActive ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50"}`}>
              <span>{n.icon}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        {st && (
          <div className="hidden space-y-1.5 px-4 pt-6 text-xs text-slate-500 md:block">
            <div>Data as of <b className="text-slate-700">{st.dataset?.as_of ?? "–"}</b></div>
            <div>Source: {st.dataset?.source ?? "–"}</div>
            <div className="flex flex-wrap gap-1 pt-1">
              <Badge tone={st.arango.status === "connected" ? "green" : st.arango.configured ? "red" : "gray"}
                title={st.arango.error ?? ""}>
                ArangoDB: {st.arango.status}{st.arango.status === "connected" ? ` · ${st.arango.sync}` : ""}
              </Badge>
              <Badge tone={agent?.llm_configured ? "blue" : "gray"}>
                AI: {agent?.llm_configured ? agent.llm.models[0] : "rules only"}
              </Badge>
            </div>
          </div>
        )}
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/restock" element={<Restock />} />
          <Route path="/data" element={<Data onChange={status.reload} />} />
        </Routes>
      </main>
    </div>
  );
}

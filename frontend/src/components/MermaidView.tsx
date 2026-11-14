import { useEffect, useId, useState } from "react";

let ready: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  ready ??= import("mermaid").then((m) => {
    m.default.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral",
      flowchart: { htmlLabels: true, curve: "basis" } });
    return m.default;
  });
  return ready;
}

/** Renders Mermaid code produced by the backend. Falls back to the source on a parse error. */
export default function MermaidView({ code }: { code: string }) {
  const id = "m" + useId().replace(/[^a-zA-Z0-9]/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setSvg(null);
    setFailed(false);
    loadMermaid()
      .then((m) => m.render(id, code))
      .then((r) => alive && setSvg(r.svg))
      .catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [code, id]);

  if (failed) return <pre className="overflow-x-auto rounded bg-slate-100 p-2 text-xs">{code}</pre>;
  if (!svg) return <p className="text-sm text-slate-500">Drawing diagram…</p>;
  return <div className="overflow-x-auto [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />;
}

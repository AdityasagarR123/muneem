import { cell } from "../lib/format";
import { TierBadge } from "./ui";

type Col = { key: string; label: string };

export default function TableView({ columns, rows, total }: { columns: Col[]; rows: Record<string, any>[]; total?: number }) {
  if (!rows?.length) return <p className="text-sm text-muted">Nothing to show.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            {columns.map((c) => <th key={c.key} className="px-2 py-1.5 font-medium">{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line/60 last:border-0">
              {columns.map((c) => (
                <td key={c.key} className="whitespace-nowrap px-2 py-1.5">
                  {c.key === "tier" ? <TierBadge tier={r[c.key]} /> : cell(c.key + (c.label.includes("₹") ? "_rs" : ""), r[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total !== undefined && total > rows.length && (
        <p className="mt-1 text-xs text-muted">Showing {rows.length} of {total}</p>
      )}
    </div>
  );
}

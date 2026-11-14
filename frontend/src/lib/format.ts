const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const numFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

/** Rupees (not paise) -> "₹1,23,456" */
export function inr(rupees: number | null | undefined): string {
  if (rupees === null || rupees === undefined || Number.isNaN(rupees)) return "–";
  const sign = rupees < 0 ? "-" : "";
  return `${sign}₹${inrFmt.format(Math.abs(rupees))}`;
}

export const paise = (p: number | null | undefined) => inr(p === null || p === undefined ? p : p / 100);

export function num(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return numFmt.format(v);
}

export function pct(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined) return "–";
  return `${signed && v > 0 ? "+" : ""}${numFmt.format(v)}%`;
}

/** Format a table/chart cell by its key name. */
export function cell(key: string, v: any): string {
  if (v === null || v === undefined || v === "") return "–";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") {
    if (key.endsWith("_rs") || key.includes("₹")) return inr(v);
    if (key.endsWith("_pct")) return pct(v);
    return num(v);
  }
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? x.name ?? JSON.stringify(x) : x)).join(", ");
  if (typeof v === "object") return v.name ?? JSON.stringify(v);
  return String(v);
}

export function shortDate(s: string): string {
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

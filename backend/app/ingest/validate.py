"""Validate raw tables and build a clean Dataset. Never crashes on bad data:
every problem becomes a row-level entry in the report.

Strict mode (default): any error -> nothing is loaded.
Partial mode: rows with errors are dropped, the rest is loaded.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import re

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.core.money import taxable_from_inclusive
from app.core.text import sanitize_text
from app.ingest.schema import (
    DEFAULT_LEAD_TIME, LEGACY_GST, OPTIONAL_TABLES, PAYMENT_MAP, REQUIRED_TABLES, TABLES,
    TRUE_WORDS, VALID_GST, Col,
)
from app.store.dataset import Dataset

ISSUE_CAP = 200


class Issue(BaseModel):
    table: str
    row: int | None = None  # spreadsheet row number (header = row 1)
    column: str | None = None
    value: str | None = None
    message: str


class ValidationReport(BaseModel):
    ok: bool = False
    mode: str = "strict"
    errors: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    error_summary: dict[str, int] = Field(default_factory=dict)
    warning_summary: dict[str, int] = Field(default_factory=dict)
    rows_in: dict[str, int] = Field(default_factory=dict)
    rows_loaded: dict[str, int] = Field(default_factory=dict)
    as_of: str | None = None


class _Collector:
    def __init__(self, report: ValidationReport):
        self.r = report
        self.esum: Counter = Counter()
        self.wsum: Counter = Counter()

    def _add(self, level: str, table: str, message: str, rows=None, column=None, values=None):
        bucket = self.r.errors if level == "error" else self.r.warnings
        summ = self.esum if level == "error" else self.wsum
        key = f"{table}: {message}"
        if rows is None:
            summ[key] += 1
            if len(bucket) < ISSUE_CAP:
                bucket.append(Issue(table=table, column=column, message=message))
            return
        values = list(values) if values is not None else [None] * len(rows)
        for r, v in zip(rows, values):
            summ[key] += 1
            if len(bucket) < ISSUE_CAP:
                bucket.append(Issue(table=table, row=int(r) + 2, column=column,
                                    value=None if v is None else sanitize_text(v, 60),
                                    message=message))

    def error(self, table, message, rows=None, column=None, values=None):
        self._add("error", table, message, rows, column, values)

    def warn(self, table, message, rows=None, column=None, values=None):
        self._add("warning", table, message, rows, column, values)

    def finish(self):
        self.r.error_count = sum(self.esum.values())
        self.r.warning_count = sum(self.wsum.values())
        self.r.error_summary = dict(self.esum)
        self.r.warning_summary = dict(self.wsum)


# ---------------------------------------------------------------- parsers
def _blank(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().eq("")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(object), errors="coerce").astype(float)


def parse_money(s: pd.Series) -> pd.Series:
    cleaned = s.astype(str).str.replace(r"(?i)(rs\.?|inr|₹|,|\s)", "", regex=True)
    return (_num(cleaned) * 100).round()


def parse_rate(s: pd.Series) -> pd.Series:
    x = _num(s.astype(str).str.replace("%", "", regex=False).str.strip())
    frac = (x > 0) & (x < 1)
    x = x.where(~frac, x * 100)
    return x.round(2)


def parse_date(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    out = pd.to_datetime(s, format="ISO8601", errors="coerce")
    rest = out.isna() & s.ne("")
    if rest.any():
        alt = pd.to_datetime(s[rest], dayfirst=True, format="mixed", errors="coerce")
        out = out.astype("datetime64[ns]")
        out.loc[rest] = alt.astype("datetime64[ns]")
    return pd.to_datetime(out).dt.normalize().astype("datetime64[ns]")


def parse_payment(s: pd.Series, default: str) -> tuple[pd.Series, pd.Series]:
    low = s.astype(str).str.strip().str.lower()
    mapped = low.map(PAYMENT_MAP)
    blank = low.eq("")
    mapped = mapped.where(~blank, default)
    unknown = mapped.isna()
    return mapped.fillna("other"), unknown


# ---------------------------------------------------------------- tables
def _map_columns(table: str, df: pd.DataFrame, col: _Collector) -> dict[str, str] | None:
    spec = TABLES[table]
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for c in spec:
        src = c.name if c.name in df.columns else next(
            (a for a in c.aliases if a in df.columns and a not in used), None)
        if src is not None:
            mapping[c.name] = src
            used.add(src)
    missing = [c.name for c in spec if c.required and c.name not in mapping]
    if missing:
        col.error(table, f"Missing required column(s): {', '.join(missing)}. "
                         f"Found: {', '.join(df.columns[:15])}")
        return None
    extra = [c for c in df.columns if c not in used]
    if extra:
        col.r.notes.append(f"{table}: ignored column(s) {', '.join(extra[:10])}")
    renamed = [f"{src}->{dst}" for dst, src in mapping.items() if src != dst]
    if renamed:
        col.r.notes.append(f"{table}: recognised {', '.join(renamed)}")
    return mapping


def _process(table: str, df: pd.DataFrame, col: _Collector, max_text: int):
    """Returns (clean DataFrame, bad-row mask) or (None, None) on a table-level error."""
    mapping = _map_columns(table, df, col)
    if mapping is None:
        return None, None
    out = pd.DataFrame(index=df.index)
    bad = pd.Series(False, index=df.index)
    spec: list[Col] = TABLES[table]
    for c in spec:
        if c.name not in mapping:
            out[c.name] = c.default
            continue
        raw = df[mapping[c.name]].astype(str)
        blank = _blank(raw)
        if c.kind in ("id", "text"):
            val = raw.map(lambda v: sanitize_text(v, max_text))
            if c.kind == "id" or c.required:
                miss = val.eq("") if c.required else pd.Series(False, index=df.index)
                if c.required and miss.any():
                    col.error(table, f"{c.name} is empty", rows=df.index[miss], column=c.name)
                    bad |= miss
            if not c.required:
                val = val.where(~val.eq(""), c.default if c.default is not None else "")
            out[c.name] = val
        elif c.kind == "money":
            val = parse_money(raw)
            invalid = val.isna() & ~blank
            neg = val < 0
            if c.required:
                invalid |= blank
            if invalid.any():
                col.error(table, "not a valid amount", rows=df.index[invalid], column=c.name,
                          values=raw[invalid])
            if neg.any():
                col.error(table, "amount cannot be negative", rows=df.index[neg], column=c.name,
                          values=raw[neg])
            bad |= invalid | neg
            out[c.name] = val
        elif c.kind == "rate":
            val = parse_rate(raw)
            invalid = val.isna()
            legacy = val.isin(list(LEGACY_GST))
            off = ~invalid & ~val.isin(list(VALID_GST | LEGACY_GST))
            if invalid.any():
                col.error(table, "GST rate missing or not a number", rows=df.index[invalid],
                          column=c.name, values=raw[invalid])
            if off.any():
                col.error(table, f"GST rate must be one of {sorted(VALID_GST)}", rows=df.index[off],
                          column=c.name, values=raw[off])
            if legacy.any():
                col.warn(table, "legacy GST slab (12%/28% were merged in Sept 2025) - "
                                "kept as given, please verify", rows=df.index[legacy],
                         column=c.name, values=raw[legacy])
            bad |= invalid | off
            out[c.name] = val
        elif c.kind in ("int", "qty"):
            val = _num(raw)
            invalid = val.isna() & ~blank
            if c.required:
                invalid |= blank
            if c.kind == "int":
                invalid |= val.notna() & ((val % 1) != 0)
            if invalid.any():
                col.error(table, "not a valid number", rows=df.index[invalid], column=c.name,
                          values=raw[invalid])
            bad |= invalid
            if c.default is not None:
                val = val.where(val.notna() | invalid, c.default)
            out[c.name] = val
        elif c.kind == "date":
            val = parse_date(raw)
            invalid = val.isna() & ~blank
            if c.required:
                invalid |= blank
            if invalid.any():
                col.error(table, "date not recognised (use YYYY-MM-DD or DD-MM-YYYY)",
                          rows=df.index[invalid], column=c.name, values=raw[invalid])
            bad |= invalid
            out[c.name] = val
        elif c.kind == "bool":
            out[c.name] = raw.str.strip().str.lower().isin(TRUE_WORDS)
        elif c.kind == "payment":
            val, unknown = parse_payment(raw, c.default or "cash")
            if unknown.any():
                col.warn(table, "unknown payment mode, stored as 'other'", rows=df.index[unknown],
                         column=c.name, values=raw[unknown])
            out[c.name] = val
    return out, bad


def validate_tables(raw: dict[str, pd.DataFrame], *, allow_partial: bool = False,
                    max_rows: int = 200_000, max_text: int = 80, source: str = "upload",
                    today: date | None = None) -> tuple[ValidationReport, Dataset | None]:
    report = ValidationReport(mode="partial" if allow_partial else "strict")
    col = _Collector(report)
    today = today or date.today()
    table_error = False

    for t in REQUIRED_TABLES:
        if t not in raw:
            col.error(t, f"'{t}' table is required")
            table_error = True
    for t, df in raw.items():
        report.rows_in[t] = int(len(df))
        if len(df) > max_rows:
            col.error(t, f"too many rows ({len(df)}); limit is {max_rows}")
            table_error = True
        if len(df) == 0 and t in REQUIRED_TABLES:
            col.error(t, "table is empty")
            table_error = True
    if table_error:
        col.finish()
        return report, None

    clean: dict[str, pd.DataFrame] = {}
    bad: dict[str, pd.Series] = {}
    for t in (*REQUIRED_TABLES, *OPTIONAL_TABLES):
        if t in raw:
            c, b = _process(t, raw[t], col, max_text)
            if c is None:
                table_error = True
            else:
                clean[t], bad[t] = c, b
    if table_error:
        col.finish()
        return report, None

    # ---- items ---------------------------------------------------------
    items = clean["items"]
    dup = items["item_id"].duplicated(keep="first") & ~bad["items"]
    if dup.any():
        col.error("items", "duplicate item_id", rows=items.index[dup], column="item_id",
                  values=items.loc[dup, "item_id"])
        bad["items"] |= dup
    pack_bad = items["pack_size"].notna() & (items["pack_size"] < 1)
    if pack_bad.any():
        col.warn("items", "pack_size below 1, using 1", rows=items.index[pack_bad], column="pack_size")
        items.loc[pack_bad, "pack_size"] = 1
    lt_bad = items["lead_time_days"].notna() & ((items["lead_time_days"] < 0) |
                                                (items["lead_time_days"] > 60))
    if lt_bad.any():
        col.error("items", "lead_time_days must be 0-60", rows=items.index[lt_bad],
                  column="lead_time_days")
        bad["items"] |= lt_bad
    ok_i = ~bad["items"]
    below = ok_i & items["cost_ex_gst"].notna() & items["mrp_incl_gst"].notna() & (
        taxable_from_inclusive(items["mrp_incl_gst"].fillna(0).astype("int64"),
                               items["gst_rate"].fillna(0).astype("int64")) < items["cost_ex_gst"])
    if below.any():
        col.warn("items", "MRP (after GST) is below cost - every sale loses money",
                 rows=items.index[below], column="mrp_incl_gst")

    # ---- suppliers -----------------------------------------------------
    if "suppliers" in clean:
        sup = clean["suppliers"]
        sdup = sup["supplier_id"].duplicated(keep="first") & ~bad["suppliers"]
        if sdup.any():
            col.error("suppliers", "duplicate supplier_id", rows=sup.index[sdup], column="supplier_id")
            bad["suppliers"] |= sdup
        sup = sup[~bad["suppliers"]].copy()
    else:
        sup = pd.DataFrame(columns=["supplier_id", "name", "whatsapp_placeholder", "lead_time_days"])
    known_sup = set(sup["supplier_id"])
    missing_sup = sorted(set(items.loc[ok_i, "supplier_id"]) - known_sup)
    if missing_sup:
        col.warn("suppliers", f"{len(missing_sup)} supplier id(s) used by items but not listed - "
                              f"created with the id as name: {', '.join(missing_sup[:8])}")
        sup = pd.concat([sup, pd.DataFrame({
            "supplier_id": missing_sup,
            "name": ["Unassigned supplier" if s == "UNASSIGNED" else s for s in missing_sup],
            "whatsapp_placeholder": "", "lead_time_days": np.nan})], ignore_index=True)
    sup_lead = sup.set_index("supplier_id")["lead_time_days"]
    lt = items["lead_time_days"]
    from_sup = items["supplier_id"].map(sup_lead)
    items["lead_time_days"] = lt.where(lt.notna(), from_sup).fillna(DEFAULT_LEAD_TIME)
    sup["lead_time_days"] = pd.to_numeric(sup["lead_time_days"], errors="coerce").fillna(
        DEFAULT_LEAD_TIME)

    item_ids_valid = set(items.loc[~bad["items"], "item_id"])

    # ---- sales ---------------------------------------------------------
    sales = clean["sales"]
    orphan = ~sales["item_id"].isin(item_ids_valid) & ~bad["sales"]
    if orphan.any():
        col.error("sales", "item_id not found in items", rows=sales.index[orphan], column="item_id",
                  values=sales.loc[orphan, "item_id"])
        bad["sales"] |= orphan
    qty_bad = sales["qty"].notna() & (sales["qty"] <= 0)
    if qty_bad.any():
        col.error("sales", "qty must be greater than 0 (returns are not supported yet)",
                  rows=sales.index[qty_bad], column="qty", values=sales.loc[qty_bad, "qty"])
        bad["sales"] |= qty_bad
    future = sales["date"].notna() & (sales["date"] > pd.Timestamp(today))
    if future.any():
        col.error("sales", "date is in the future", rows=sales.index[future], column="date")
        bad["sales"] |= future
    mrp = sales["item_id"].map(items.drop_duplicates("item_id").set_index("item_id")["mrp_incl_gst"])
    no_price = sales["unit_price_incl_gst"].isna()
    if no_price.any():
        report.notes.append(f"sales: {int(no_price.sum())} line(s) without price - item MRP used")
        sales["unit_price_incl_gst"] = sales["unit_price_incl_gst"].where(~no_price, mrp)
    dupl = sales.duplicated(["invoice_id", "item_id"], keep=False) & ~bad["sales"]
    if dupl.any():
        col.warn("sales", "same item appears twice on one invoice (kept both lines)",
                 rows=sales.index[dupl], column="item_id")

    # ---- inventory -----------------------------------------------------
    if "inventory" in clean:
        inv = clean["inventory"]
        unknown = ~inv["item_id"].isin(item_ids_valid) & ~bad["inventory"]
        if unknown.any():
            col.warn("inventory", "item_id not in items - row ignored", rows=inv.index[unknown],
                     column="item_id", values=inv.loc[unknown, "item_id"])
        idup = inv["item_id"].duplicated(keep="first") & ~bad["inventory"] & ~unknown
        if idup.any():
            col.error("inventory", "duplicate item_id in stock list", rows=inv.index[idup],
                      column="item_id")
            bad["inventory"] |= idup
        neg = inv["stock_qty"].notna() & (inv["stock_qty"] < 0)
        if neg.any():
            col.warn("inventory", "negative stock - treated as 0 in calculations",
                     rows=inv.index[neg], column="stock_qty", values=inv.loc[neg, "stock_qty"])
        inv = inv[~bad["inventory"] & ~unknown]
    else:
        col.warn("inventory", "no stock list uploaded - stock assumed 0, reorder advice is limited")
        inv = pd.DataFrame(columns=["item_id", "stock_qty", "as_of_date"])

    # ---- customers & identity resolution --------------------------------
    customers, repayments = _resolve_customers(clean, bad, col)

    # ---- decide --------------------------------------------------------
    n_err = sum(col.esum.values())
    if n_err and not allow_partial:
        report.notes.append("Nothing was loaded. Fix the errors, or upload again with "
                            "allow_partial=true to load only the valid rows.")
        col.finish()
        return report, None

    items_ok = items[~bad["items"]].copy()
    sales_ok = sales[~bad["sales"] & sales["item_id"].isin(set(items_ok["item_id"]))].copy()
    if items_ok.empty or sales_ok.empty:
        col.error("sales" if items_ok.shape[0] else "items", "no valid rows left to load")
        col.finish()
        return report, None

    missing_stock = set(items_ok["item_id"]) - set(inv["item_id"])
    if "inventory" in clean and missing_stock:
        col.warn("inventory", f"{len(missing_stock)} item(s) have no stock row - assumed 0")

    span = (sales_ok["date"].max() - sales_ok["date"].min()).days + 1
    if span < 14:
        col.warn("sales", f"only {span} day(s) of sales - forecasts need at least 14")

    if "expenses" in clean:
        exp = clean["expenses"][~bad["expenses"]].copy()
        exp["category"] = exp["category"].str.lower()
    else:
        exp = pd.DataFrame(columns=["date", "category", "amount", "payment_mode"])

    last_sale = sales_ok["date"].max()
    inv_date = inv["as_of_date"].dropna().max() if len(inv) else pd.NaT
    as_of_ts = max(last_sale, inv_date) if pd.notna(inv_date) else last_sale
    if pd.notna(inv_date) and inv_date < last_sale:
        col.warn("inventory", "stock list is older than the latest sale - days of stock may be "
                              "optimistic")
    inv = inv.assign(as_of_date=inv["as_of_date"].fillna(as_of_ts) if len(inv) else [])

    ds = Dataset(
        items=pd.DataFrame({
            "item_id": items_ok["item_id"], "name": items_ok["name"],
            "category": items_ok["category"], "hsn": items_ok["hsn"].fillna(""),
            "mrp_paise": items_ok["mrp_incl_gst"].astype("int64"),
            "cost_paise": items_ok["cost_ex_gst"].astype("int64"),
            "gst_rate": items_ok["gst_rate"].round().astype("int64"),
            "pack_size": items_ok["pack_size"].fillna(1).astype("int64"),
            "supplier_id": items_ok["supplier_id"],
            "lead_time_days": items_ok["lead_time_days"].astype("int64"),
        }),
        sales=pd.DataFrame({
            "invoice_id": sales_ok["invoice_id"], "date": sales_ok["date"],
            "item_id": sales_ok["item_id"], "qty": sales_ok["qty"],
            "unit_price_paise": sales_ok["unit_price_incl_gst"].astype("int64"),
            "payment_mode": sales_ok["payment_mode"],
            "customer_id": sales_ok["customer_id"],
        }),
        inventory=pd.DataFrame({
            "item_id": inv["item_id"], "stock_qty": inv["stock_qty"].astype(float),
            "as_of_date": inv["as_of_date"],
        }),
        expenses=pd.DataFrame({
            "date": exp["date"], "category": exp["category"],
            "amount_paise": exp["amount"].fillna(0).astype("int64"),
            "payment_mode": exp["payment_mode"],
        }),
        suppliers=pd.DataFrame({
            "supplier_id": sup["supplier_id"], "name": sup["name"],
            "whatsapp_placeholder": sup["whatsapp_placeholder"].fillna(""),
            "lead_time_days": sup["lead_time_days"].astype("int64"),
        }),
        as_of=as_of_ts.date(),
        source=source,
        customers=customers,
        repayments=repayments[repayments["customer_id"].isin(set(customers["customer_id"]))]
        if len(repayments) else repayments,
    )
    report.ok = True
    report.as_of = ds.as_of.isoformat()
    report.rows_loaded = {
        "items": len(ds.items), "sales": len(ds.sales), "inventory": len(ds.inventory),
        "expenses": len(ds.expenses), "suppliers": len(ds.suppliers),
    }
    col.finish()
    return report, ds


# ---------------------------------------------------------------- identity resolution
def phone_key(p) -> str:
    """Normalise a phone for matching: last 10 digits ('+91 98765-43210' == '9876543210').
    Masked placeholders ('+91-XXXXX-10023') are kept as-is so they still act as identities."""
    s = str(p or "").strip()
    if not s:
        return ""
    if "x" in s.lower():
        return re.sub(r"\s", "", s.upper())
    d = re.sub(r"\D", "", s)
    return d[-10:] if len(d) >= 10 else d


def _id_from_phone(key: str) -> str:
    return "C-" + re.sub(r"[^0-9A-Za-z]", "", key)[-10:]


def _resolve_customers(clean: dict, bad: dict, col: _Collector):
    """Customer list + sales + repayments -> one consistent set of customer ids.

    * customer rows without an id get one from their phone
    * two customer rows with the same phone are merged (first one kept)
    * bills / repayments may reference a customer by id or only by phone
    * ids or phones seen on bills but missing from the list become new customers
    """
    cols = ["customer_id", "name", "phone", "area", "khata"]
    if "customers" in clean:
        cust = clean["customers"][~bad["customers"]].copy()
    else:
        cust = pd.DataFrame(columns=cols)
    cust["phone_key"] = cust["phone"].map(phone_key) if len(cust) else pd.Series(dtype=str)
    noid = cust["customer_id"].eq("") if len(cust) else pd.Series(dtype=bool)
    if len(cust):
        fill = noid & cust["phone_key"].ne("")
        cust.loc[fill, "customer_id"] = cust.loc[fill, "phone_key"].map(_id_from_phone)
        nothing = cust["customer_id"].eq("")
        if nothing.any():
            col.warn("customers", "customer row has neither id nor phone - ignored",
                     rows=cust.index[nothing])
            cust = cust[~nothing]
        dupid = cust["customer_id"].duplicated(keep="first")
        if dupid.any():
            col.warn("customers", "duplicate customer_id - first row kept", rows=cust.index[dupid],
                     column="customer_id", values=cust.loc[dupid, "customer_id"])
            cust = cust[~dupid]
    remap: dict[str, str] = {}
    if len(cust):
        has = cust["phone_key"].ne("")
        first = cust[has].drop_duplicates("phone_key").set_index("phone_key")["customer_id"]
        dup = has & cust["phone_key"].duplicated(keep="first")
        if dup.any():
            for r in cust[dup].itertuples():
                remap[r.customer_id] = first[r.phone_key]
            col.warn("customers", f"{int(dup.sum())} duplicate customer(s) merged - same phone number")
            cust = cust[~dup]
        phone_to_id = first.to_dict()
    else:
        phone_to_id = {}
    known = set(cust["customer_id"])
    created: dict[str, dict] = {}

    def resolve(cid: str, phone: str) -> str:
        cid = remap.get(cid, cid)
        if cid:
            if cid not in known and cid not in created:
                created[cid] = {"customer_id": cid, "name": "", "phone": phone, "area": "", "khata": False}
            return cid
        key = phone_key(phone)
        if not key:
            return ""
        if key in phone_to_id:
            return phone_to_id[key]
        new = _id_from_phone(key)
        phone_to_id[key] = new
        if new not in known:
            created.setdefault(new, {"customer_id": new, "name": "", "phone": phone, "area": "",
                                     "khata": False})
        return new

    sales = clean["sales"]
    sales["customer_id"] = [resolve(str(c or ""), str(p or "")) for c, p in
                            zip(sales["customer_id"], sales["customer_phone"])]
    if created and "customers" in clean:
        col.warn("customers", f"{len(created)} customer(s) on bills were not in the customer list - added")
    credit_anon = (sales["payment_mode"].eq("credit") & sales["customer_id"].eq("") & ~bad["sales"])
    if credit_anon.any():
        n = sales.loc[credit_anon, "invoice_id"].nunique()
        col.warn("sales", f"{n} credit (udhaar) bill(s) have no customer - they cannot be tracked in the khata")
    credit_cust = set(sales.loc[sales["payment_mode"].eq("credit"), "customer_id"]) - {""}

    rep = pd.DataFrame(columns=["date", "customer_id", "amount_paise", "mode"])
    if "repayments" in clean:
        r = clean["repayments"][~bad["repayments"]].copy()
        before = set(created)
        r["customer_id"] = [resolve(str(c or ""), str(p or "")) for c, p in
                            zip(r["customer_id"], r["customer_phone"])]
        for cid in set(created) - before:  # repayment-only ids are suspicious: drop, don't invent
            created.pop(cid, None)
        unknown = r["customer_id"].eq("") | ~r["customer_id"].isin(known | set(created))
        if unknown.any():
            col.warn("repayments", "repayment for an unknown customer - ignored",
                     rows=r.index[unknown], column="customer_id")
        r = r[~unknown]
        rep = pd.DataFrame({"date": r["date"], "customer_id": r["customer_id"],
                            "amount_paise": r["amount"].astype("int64"), "mode": r["mode"]})

    out = pd.concat([cust[cols], pd.DataFrame(list(created.values()), columns=cols)],
                    ignore_index=True)
    out["khata"] = out["khata"].astype(bool) | out["customer_id"].isin(credit_cust)
    return out, rep

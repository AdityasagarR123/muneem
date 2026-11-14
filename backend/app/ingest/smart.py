"""Smart ingestion: understand an unknown export before loading it.

    analyze(files)  ->  per table: profile, what it is (entity), column -> field mapping with a
                        confidence for every field, links between tables, remembered mappings
    owner confirms only what is uncertain
    commit(mapping) ->  columns renamed to canonical fields -> the normal row-level validator
                        -> dataset; confirmed mappings are remembered for next time

Deterministic first: header words + value patterns + value checks decide. An optional AI step
may *propose* mappings for columns still unknown; those always need the owner's confirmation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.core.text import normalize_header
from app.ingest.profile import GST_VALUES, numeric_values, profile_table
from app.ingest.readers import match_sheet
from app.ingest.schema import PAYMENT_MAP, TABLES, Col

AUTO, CHECK = 0.75, 0.5

ENTITY_LABEL = {"items": "Products / item master", "sales": "Bill lines (sales)", "inventory": "Stock",
                "expenses": "Expenses", "suppliers": "Suppliers", "customers": "Customers",
                "repayments": "Udhaar repayments"}
FIELD_LABEL = {
    "item_id": "Item code", "name": "Name", "category": "Category", "hsn": "HSN code",
    "mrp_incl_gst": "MRP (incl. GST)", "cost_ex_gst": "Purchase cost (ex GST)", "gst_rate": "GST %",
    "pack_size": "Pack / case size", "supplier_id": "Supplier code", "lead_time_days": "Delivery days",
    "invoice_id": "Bill number", "date": "Date", "qty": "Quantity", "unit_price_incl_gst": "Price paid (incl. GST)",
    "payment_mode": "Payment mode", "customer_id": "Customer code", "customer_phone": "Customer phone",
    "stock_qty": "Stock in hand", "as_of_date": "Stock date", "amount": "Amount", "whatsapp_placeholder": "Phone",
    "phone": "Phone", "area": "Area", "khata": "Khata / credit allowed", "mode": "Payment mode",
}

# extra words seen in Indian billing exports -> the words our field names use
SYN = {
    "dt": "date", "bill": "invoice", "inv": "invoice", "voucher": "invoice", "receipt": "invoice",
    "no": "id", "num": "id", "number": "id", "code": "id", "sr": "id", "desc": "name",
    "description": "name", "particulars": "name", "product": "item", "sku": "item", "article": "item",
    "quantity": "qty", "pcs": "qty", "nos": "qty", "units": "qty", "rate": "price", "selling": "price",
    "sale": "price", "amt": "amount", "value": "amount", "tax": "gst", "vat": "gst", "igst": "gst",
    "mob": "phone", "mobile": "phone", "contact": "phone", "whatsapp": "phone", "cust": "customer",
    "party": "customer", "client": "customer", "buyer": "customer", "vendor": "supplier",
    "distributor": "supplier", "closing": "stock", "balance": "stock", "onhand": "stock",
    "group": "category", "dept": "category", "department": "category", "mode": "payment", "pay": "payment",
    "tender": "payment", "purchase": "cost", "landing": "cost", "buying": "cost", "case": "pack",
    "carton": "pack", "lead": "lead", "delivery": "lead", "locality": "area", "address": "area",
    "udhar": "khata", "udhaar": "khata", "credit": "khata", "head": "category", "pct": "rate",
    "percent": "rate", "paid": "amount",
}
NOISE = {"of", "the", "rs", "inr", "in", "a"}

KIND_TYPES = {
    "id": {"identifier": 1.0, "integer": 0.8, "text": 0.55, "category": 0.45},
    "text": {"text": 1.0, "category": 0.9, "identifier": 0.6, "integer": 0.5},
    "money": {"money": 1.0, "decimal": 0.9, "integer": 0.7},
    "rate": {"percent": 1.0, "integer": 0.5, "decimal": 0.4},
    "int": {"integer": 1.0, "decimal": 0.4},
    "qty": {"integer": 1.0, "decimal": 0.9},
    "date": {"date": 1.0},
    "payment": {"category": 1.0, "text": 0.5},
    "bool": {"boolean": 1.0, "category": 0.5},
}
PHONE_FIELDS = {"customer_phone", "phone", "whatsapp_placeholder"}


def _tokens(h: str) -> list[str]:
    toks = [t for t in normalize_header(h).split("_") if t and t not in NOISE]
    return [SYN.get(t, t) for t in toks]


def header_score(header: str, col: Col) -> float:
    norm = normalize_header(header)
    if norm == col.name:
        return 1.0
    if norm in col.aliases:
        return 0.95
    h = set(_tokens(header))
    best = 0.0
    for target in (col.name, *col.aliases):
        t = set(_tokens(target))
        if not t:
            continue
        hit = h & t
        if not hit or hit == {"id"}:
            continue
        score = 0.85 * len(hit) / len(t) - 0.1 * len(h - t) / max(len(h), 1)
        best = max(best, score)
    return round(max(best, 0.0), 3)


def type_score(profile: dict, col: Col) -> float:
    kind = profile.get("type")
    if col.name in PHONE_FIELDS:
        return {"phone": 1.0, "text": 0.4, "identifier": 0.3}.get(kind, 0.0)
    if col.name == "customer_id":
        return {"identifier": 1.0, "phone": 0.6, "text": 0.5, "integer": 0.6}.get(kind, 0.0)
    return KIND_TYPES.get(col.kind, {}).get(kind, 0.0)


def value_bonus(profile: dict, raw: pd.Series, col: Col) -> float:
    if col.kind == "rate":
        v = numeric_values(raw)
        if len(v) and np.isin(np.round(v, 2), list(GST_VALUES)).mean() > 0.9:
            return 0.3
    if col.kind == "payment" and profile.get("payment_like_pct", 0) >= 60:
        return 0.35
    if col.kind == "bool" and profile.get("bool_like_pct", 0) >= 80:
        return 0.2
    if col.kind in ("qty", "int") and profile.get("stats", {}).get("median", 1e9) < 500:
        return 0.05
    return 0.0


@dataclass
class FieldMatch:
    field: str
    source: str | None
    score: float
    candidates: list[dict]
    status: str = "missing"
    reason: str = ""


def map_fields(entity: str, df: pd.DataFrame, profiles: list[dict]) -> list[FieldMatch]:
    spec = TABLES[entity]
    prof = {p["column"]: p for p in profiles}
    pairs = []
    for col in spec:
        for c in df.columns:
            p = prof[c]
            if p.get("type") == "empty":
                continue
            hs, ts = header_score(c, col), type_score(p, col)
            if ts == 0 and hs < 0.9:
                continue
            s = round(min(1.0, 0.6 * hs + 0.4 * ts + value_bonus(p, df[c], col)), 3)
            pairs.append((s, col.name, c, hs, ts))
    pairs.sort(key=lambda x: -x[0])
    by_field: dict[str, list] = {}
    for s, f, c, hs, ts in pairs:
        by_field.setdefault(f, []).append({"column": c, "score": s, "header": hs, "type": ts})
    taken: set[str] = set()
    chosen: dict[str, tuple] = {}
    required = {c.name for c in spec if c.required}
    for s, f, c, hs, ts in pairs:  # greedy: best pairs first, each column used once
        # weak guesses are offered only for required fields; optional ones stay empty instead
        if f in chosen or c in taken or s < (0.35 if f in required else 0.5):
            continue
        chosen[f] = (c, s, hs, ts)
        taken.add(c)
    out = []
    for col in spec:
        cands = by_field.get(col.name, [])[:4]
        if col.name not in chosen:
            out.append(FieldMatch(col.name, None, 0.0, cands, "missing" if col.required else "unmapped",
                                  "no column looks like this" if col.required else ""))
            continue
        c, s, hs, ts = chosen[col.name]
        rival = next((x for x in cands if x["column"] != c and x["column"] not in taken), None)
        if rival and s - rival["score"] < 0.08:
            s = round(s * 0.85, 3)  # two columns fit almost equally
        reason = ("header and values match" if hs >= 0.8 and ts >= 0.8 else
                  "values match, header unclear" if ts >= 0.8 else "header matches, values unusual")
        status = "auto" if s >= AUTO else "check"
        out.append(FieldMatch(col.name, c, s, cands, status, reason))
    _fix_mrp_cost(out, df)
    return out


def _fix_mrp_cost(matches: list[FieldMatch], df: pd.DataFrame) -> None:
    """MRP must be above cost: if the guessed columns say otherwise, swap and ask."""
    mrp = next((m for m in matches if m.field == "mrp_incl_gst" and m.source), None)
    cost = next((m for m in matches if m.field == "cost_ex_gst" and m.source), None)
    if not (mrp and cost):
        return
    a, b = numeric_values(df[mrp.source]), numeric_values(df[cost.source])
    if len(a) and len(b) and np.median(a) < np.median(b):
        mrp.source, cost.source = cost.source, mrp.source
        for m in (mrp, cost):
            m.score = min(m.score, 0.7)
            m.status, m.reason = "check", "swapped: MRP should be the higher price"


def classify(df: pd.DataFrame, profiles: list[dict], name_hint: str = "") -> list[dict]:
    """Rank what this table could be."""
    hint = match_sheet(name_hint) if name_hint else None
    ranked = []
    for entity, spec in TABLES.items():
        matches = map_fields(entity, df, profiles)
        req = [m for m in matches if any(c.name == m.field and c.required for c in spec)]
        if not req:  # tables whose fields are all optional (customers): judge on what matched
            req = [m for m in matches if m.source] or matches
        opt = [m for m in matches if m not in req]
        req_score = float(np.mean([m.score for m in req])) if req else 0.0
        opt_score = float(np.mean([1.0 if m.source else 0.0 for m in opt])) if opt else 0.0
        # how much of the file this table type explains: a stock list can't explain bill numbers
        real_cols = [p["column"] for p in profiles if p.get("type") != "empty" and p.get("distinct", 0) > 1]
        explained = len({m.source for m in matches if m.source} & set(real_cols)) / max(len(real_cols), 1)
        score = 0.6 * req_score + 0.15 * opt_score + 0.25 * explained + (0.25 if hint == entity else 0.0)
        if any(m.source is None for m in req):
            score *= 0.6
        ranked.append({"entity": entity, "label": ENTITY_LABEL[entity], "score": round(min(score, 1.0), 3)})
    ranked.sort(key=lambda r: -r["score"])
    return ranked


def signature(columns: list[str]) -> str:
    norm = sorted(normalize_header(c) for c in columns)
    return hashlib.sha1("|".join(norm).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ relationships
EXPECTED_LINKS = [
    ("sales", "item_id", "items", "item_id"), ("inventory", "item_id", "items", "item_id"),
    ("items", "supplier_id", "suppliers", "supplier_id"), ("sales", "customer_id", "customers", "customer_id"),
    ("repayments", "customer_id", "customers", "customer_id"),
]


def _vals(s: pd.Series) -> set:
    v = s.astype(str).str.strip()
    return set(v[v.ne("")])


def check_links(tables: dict[str, tuple[pd.DataFrame, dict[str, str]]]) -> list[dict]:
    """tables: entity -> (df, field->column). Coverage of child values found in the parent."""
    out = []
    for ce, cf, pe, pf in EXPECTED_LINKS:
        if ce not in tables or pe not in tables:
            continue
        cdf, cmap = tables[ce]
        pdf, pmap = tables[pe]
        if cf not in cmap or pf not in pmap:
            continue
        child, parent = _vals(cdf[cmap[cf]]), _vals(pdf[pmap[pf]])
        if not child:
            continue
        cov = len(child & parent) / len(child)
        out.append({"from": f"{ce}.{cmap[cf]}", "to": f"{pe}.{pmap[pf]}",
                    "meaning": f"{ENTITY_LABEL[ce]} → {ENTITY_LABEL[pe]}",
                    "coverage_pct": round(100 * cov, 1), "missing_values": len(child - parent),
                    "status": "ok" if cov >= 0.95 else "partial" if cov >= 0.5 else "broken"})
    return out


def discover_links(frames: dict[str, pd.DataFrame], profiles: dict[str, list[dict]]) -> list[dict]:
    """Any column whose values (≥95%) are found in a unique key column of another table."""
    keys = []
    for t, df in frames.items():
        for p in profiles[t]:
            if p["type"] in ("identifier", "integer", "text", "phone") and p["distinct"] == p["rows"] \
                    and p["rows"] >= 3:
                keys.append((t, p["column"], _vals(df[p["column"]])))
    out = []
    for t, df in frames.items():
        for p in profiles[t]:
            if p["type"] not in ("identifier", "integer", "phone", "text") or p["distinct"] < 3:
                continue
            vals = _vals(df[p["column"]])
            for kt, kc, kv in keys:
                if kt == t:
                    continue
                cov = len(vals & kv) / len(vals) if vals else 0
                if cov >= 0.95:
                    out.append({"from": f"{t}.{p['column']}", "to": f"{kt}.{kc}",
                                "coverage_pct": round(100 * cov, 1)})
    return out


# ------------------------------------------------------------------ analysis
def analyze_table(key: str, df: pd.DataFrame, name_hint: str, remembered: dict | None) -> dict:
    profiles = profile_table(df)
    ranked = classify(df, profiles, name_hint)
    if remembered and remembered.get("entity") in TABLES and all(c in df.columns for c in
                                                                remembered["mapping"].values()):
        entity = remembered["entity"]
        mapping = []
        for col in TABLES[entity]:
            src = remembered["mapping"].get(col.name)
            mapping.append({"field": col.name, "label": FIELD_LABEL.get(col.name, col.name),
                            "required": col.required, "source": src, "confidence": 1.0 if src else 0.0,
                            "status": "auto" if src else ("missing" if col.required else "unmapped"),
                            "reason": f"remembered from '{remembered['name']}'" if src else "",
                            "candidates": []})
        conf = 1.0
    else:
        entity = ranked[0]["entity"]
        conf = ranked[0]["score"]
        if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < 0.1:
            conf = round(conf * 0.8, 3)
        mapping = [{"field": m.field, "label": FIELD_LABEL.get(m.field, m.field),
                    "required": any(c.name == m.field and c.required for c in TABLES[entity]),
                    "source": m.source, "confidence": m.score, "status": m.status, "reason": m.reason,
                    "candidates": m.candidates} for m in map_fields(entity, df, profiles)]
    used = {m["source"] for m in mapping if m["source"]}
    return {
        "key": key, "name": name_hint, "rows": int(len(df)), "columns": profiles,
        "entity": entity, "entity_label": ENTITY_LABEL[entity], "entity_confidence": conf,
        "entity_status": "auto" if conf >= 0.6 else "check",
        "alternatives": ranked[:3], "remembered": remembered["name"] if remembered else None,
        "signature": signature(list(df.columns)), "mapping": mapping,
        "unmapped_columns": [c for c in df.columns if c not in used],
        "needs_confirmation": sum(1 for m in mapping if m["status"] in ("check", "missing")
                                  and (m["required"] or m["source"])) + int(conf < 0.6),
    }


def analyze(frames: dict[str, pd.DataFrame], remembered: dict[str, dict]) -> dict:
    tables = [analyze_table(k, df, k, remembered.get(signature(list(df.columns))))
              for k, df in frames.items()]
    mapped = {}
    for t in tables:
        fmap = {m["field"]: m["source"] for m in t["mapping"] if m["source"]}
        mapped.setdefault(t["entity"], (frames[t["key"]], fmap))
    profs = {t["key"]: t["columns"] for t in tables}
    return {
        "tables": tables,
        "links": check_links(mapped),
        "discovered_links": discover_links(frames, profs),
        "summary": {
            "tables": len(tables),
            "fields_auto": sum(m["status"] == "auto" for t in tables for m in t["mapping"]),
            "fields_to_confirm": sum(t["needs_confirmation"] for t in tables),
            "entities": sorted({t["entity"] for t in tables}),
        },
    }


def build_raw(frames: dict[str, pd.DataFrame], choices: list[dict]) -> dict[str, pd.DataFrame]:
    """choices: [{key, entity, mapping: {field: column}}] -> raw tables with canonical headers."""
    raw: dict[str, list[pd.DataFrame]] = {}
    for ch in choices:
        df = frames[ch["key"]]
        entity = ch["entity"]
        if entity not in TABLES:
            raise ValueError(f"Unknown table type '{entity}'")
        fields = {c.name for c in TABLES[entity]}
        cols = {}
        for field, src in (ch.get("mapping") or {}).items():
            if not src:
                continue
            if field not in fields:
                raise ValueError(f"'{field}' is not a field of {entity}")
            if src not in df.columns:
                raise ValueError(f"Column '{src}' is not in {ch['key']}")
            cols[field] = df[src].astype(str)
        raw.setdefault(entity, []).append(pd.DataFrame(cols))
    return {e: pd.concat(parts, ignore_index=True) for e, parts in raw.items()}


# ------------------------------------------------------------------ dataset -> raw (for appends)
def dataset_to_raw(ds) -> dict[str, pd.DataFrame]:
    r = lambda s: (s / 100).map(lambda v: f"{v:.2f}")  # noqa: E731
    d = lambda s: pd.to_datetime(s).dt.strftime("%Y-%m-%d")  # noqa: E731
    it = ds.items
    out = {
        "items": pd.DataFrame({"item_id": it["item_id"], "name": it["name"], "category": it["category"],
                               "hsn": it["hsn"], "mrp_incl_gst": r(it["mrp_paise"]),
                               "cost_ex_gst": r(it["cost_paise"]), "gst_rate": it["gst_rate"].astype(str),
                               "pack_size": it["pack_size"].astype(str), "supplier_id": it["supplier_id"],
                               "lead_time_days": it["lead_time_days"].astype(str)}),
        "sales": pd.DataFrame({"invoice_id": ds.sales["invoice_id"], "date": d(ds.sales["date"]),
                               "item_id": ds.sales["item_id"], "qty": ds.sales["qty"].map(lambda v: f"{v:g}"),
                               "unit_price_incl_gst": r(ds.sales["unit_price_paise"]),
                               "payment_mode": ds.sales["payment_mode"], "customer_id": ds.sales["customer_id"]}),
        "suppliers": ds.suppliers.astype(str),
        "customers": ds.customers.assign(khata=ds.customers["khata"].map({True: "yes", False: "no"})).astype(str),
    }
    if len(ds.inventory):
        out["inventory"] = pd.DataFrame({"item_id": ds.inventory["item_id"],
                                         "stock_qty": ds.inventory["stock_qty"].map(lambda v: f"{v:g}"),
                                         "as_of_date": d(ds.inventory["as_of_date"])})
    if len(ds.expenses):
        out["expenses"] = pd.DataFrame({"date": d(ds.expenses["date"]), "category": ds.expenses["category"],
                                        "amount": r(ds.expenses["amount_paise"]),
                                        "payment_mode": ds.expenses["payment_mode"]})
    if len(ds.repayments):
        out["repayments"] = pd.DataFrame({"date": d(ds.repayments["date"]),
                                          "customer_id": ds.repayments["customer_id"],
                                          "amount": r(ds.repayments["amount_paise"]),
                                          "mode": ds.repayments["mode"]})
    return out


UPSERT_KEYS = {"items": "item_id", "inventory": "item_id", "suppliers": "supplier_id",
               "customers": "customer_id"}


def merge_records(current: dict[str, pd.DataFrame], entity: str, new: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Append (sales/expenses/repayments) or upsert by key (items/stock/suppliers/customers)."""
    out = dict(current)
    old = current.get(entity)
    if old is None or old.empty:
        out[entity] = new
        return out
    key = UPSERT_KEYS.get(entity)
    if key and key in new.columns:
        keep = old[~old[key].isin(set(new[key]))]
        out[entity] = pd.concat([keep, new], ignore_index=True)
    else:
        out[entity] = pd.concat([old, new], ignore_index=True)
    return out.copy()


def ai_suggest(llm, entity: str, missing: list[str], profiles: list[dict]) -> dict[str, str]:
    """Ask the LLM which column might hold each uncertain field. Only a proposal: the result is
    validated against real column names and always shown for confirmation."""
    if llm is None or not missing:
        return {}
    cols = [{"column": p["column"], "type": p["type"], "examples": p["examples"]} for p in profiles]
    prompt = (f"A shop uploaded a table that looks like '{ENTITY_LABEL[entity]}'. Columns with examples:\n"
              f"{json.dumps(cols, ensure_ascii=False)}\n\nFor each field below, name the single column that "
              f"holds it, or null if none does. Fields: {json.dumps({f: FIELD_LABEL.get(f, f) for f in missing})}\n"
              f"Reply with only a JSON object {{field: column_or_null}}.")
    try:
        contents = llm.start([], prompt)
        reply = llm.generate("You map spreadsheet columns to fields. Output JSON only.", contents, [],
                             allow_tools=False)
        text = re.sub(r"^```(json)?|```$", "", reply.text.strip(), flags=re.M).strip()
        data = json.loads(text)
    except Exception:
        return {}
    names = {p["column"] for p in profiles}
    return {f: c for f, c in data.items() if f in missing and isinstance(c, str) and c in names}

"""Turn uploaded bytes (CSV / XLSX) into raw string DataFrames."""

from __future__ import annotations

import io

import pandas as pd

from app.core.text import normalize_header

TABLE_NAMES = ("items", "sales", "inventory", "expenses", "suppliers")


class ReadError(ValueError):
    pass


def _clean_headers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalize_header(c) for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c]]
    return df.loc[:, ~pd.Index(df.columns).duplicated()]


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        raise ReadError("Could not decode file")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=None,
                         engine="python")
    except Exception as exc:
        raise ReadError(f"Not a readable CSV: {exc}") from exc
    return _clean_headers(df)


def read_xlsx_bytes(data: bytes) -> dict[str, pd.DataFrame]:
    try:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str, engine="openpyxl")
    except Exception as exc:
        raise ReadError(f"Not a readable .xlsx workbook: {exc}") from exc
    return {normalize_header(k): _clean_headers(v.fillna("")) for k, v in sheets.items()}


def read_upload(filename: str, data: bytes) -> pd.DataFrame | dict[str, pd.DataFrame]:
    name = (filename or "").lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        return read_csv_bytes(data)
    if name.endswith(".xlsx"):
        return read_xlsx_bytes(data)
    if name.endswith(".xls"):
        raise ReadError("Old .xls files are not supported - save as .xlsx or .csv")
    raise ReadError("Only .csv and .xlsx files are accepted")


def match_sheet(sheet_name: str) -> str | None:
    """'Sales Register' -> 'sales', 'stock' -> 'inventory'."""
    n = normalize_header(sheet_name)
    synonyms = {
        "items": ("items", "item", "products", "product", "item_master", "catalogue", "catalog"),
        "sales": ("sales", "sale", "bills", "invoices", "sales_register", "billing"),
        "inventory": ("inventory", "stock", "closing_stock", "stock_summary"),
        "expenses": ("expenses", "expense", "kharcha"),
        "suppliers": ("suppliers", "supplier", "vendors", "vendor", "distributors"),
    }
    for table, names in synonyms.items():
        if n in names or any(n.startswith(x) for x in names):
            return table
    return None


def read_dir(path) -> dict[str, pd.DataFrame]:
    """Read <table>.csv files from a folder (used for the sample data)."""
    from pathlib import Path

    out = {}
    for t in TABLE_NAMES:
        f = Path(path) / f"{t}.csv"
        if f.exists():
            out[t] = read_csv_bytes(f.read_bytes())
    return out

"""The in-memory dataset every analytic runs on.

Canonical frames (money always in integer paise):

items      item_id, name, category, hsn, mrp_paise, cost_paise, gst_rate, pack_size,
           supplier_id, lead_time_days
sales      invoice_id, date, item_id, qty, unit_price_paise, payment_mode
inventory  item_id, stock_qty, as_of_date
expenses   date, category, amount_paise, payment_mode
suppliers  supplier_id, name, whatsapp_placeholder, lead_time_days
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import cached_property
from uuid import uuid4

import numpy as np
import pandas as pd

from app.core.money import taxable_from_inclusive

ITEM_COLS = ["item_id", "name", "category", "hsn", "mrp_paise", "cost_paise", "gst_rate",
             "pack_size", "supplier_id", "lead_time_days"]
SALES_COLS = ["invoice_id", "date", "item_id", "qty", "unit_price_paise", "payment_mode",
              "customer_id"]
INVENTORY_COLS = ["item_id", "stock_qty", "as_of_date"]
EXPENSE_COLS = ["date", "category", "amount_paise", "payment_mode"]
SUPPLIER_COLS = ["supplier_id", "name", "whatsapp_placeholder", "lead_time_days"]
CUSTOMER_COLS = ["customer_id", "name", "phone", "area", "khata"]
REPAYMENT_COLS = ["date", "customer_id", "amount_paise", "mode"]


def _dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.normalize().astype("datetime64[ns]")


@dataclass
class Dataset:
    items: pd.DataFrame
    sales: pd.DataFrame
    inventory: pd.DataFrame
    expenses: pd.DataFrame
    suppliers: pd.DataFrame
    as_of: date
    source: str = "sample"
    version: str = field(default_factory=lambda: uuid4().hex[:12])
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    customers: pd.DataFrame | None = None
    repayments: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        if "customer_id" not in self.sales:
            self.sales = self.sales.assign(customer_id="")
        self.sales["customer_id"] = self.sales["customer_id"].fillna("").astype(str)
        cust = self.customers if self.customers is not None else pd.DataFrame(columns=CUSTOMER_COLS)
        for c in CUSTOMER_COLS:
            if c not in cust:
                cust = cust.assign(**{c: False if c == "khata" else ""})
        self.customers = cust[CUSTOMER_COLS].reset_index(drop=True)
        self.customers["customer_id"] = self.customers["customer_id"].astype(str)
        self.customers["khata"] = self.customers["khata"].fillna(False).astype(bool)
        for c in ("name", "phone", "area"):
            self.customers[c] = self.customers[c].fillna("").astype(str)
        rep = self.repayments if self.repayments is not None else pd.DataFrame(columns=REPAYMENT_COLS)
        self.repayments = rep[REPAYMENT_COLS].reset_index(drop=True)
        if len(self.repayments):
            self.repayments["date"] = _dt(self.repayments["date"])
        self.repayments["amount_paise"] = self.repayments["amount_paise"].astype("int64")
        self.repayments["customer_id"] = self.repayments["customer_id"].astype(str)

        self.items = self.items[ITEM_COLS].reset_index(drop=True)
        self.items["item_id"] = self.items["item_id"].astype(str)
        for c in ("mrp_paise", "cost_paise", "gst_rate", "pack_size", "lead_time_days"):
            self.items[c] = self.items[c].astype("int64")

        self.sales = self.sales[SALES_COLS].reset_index(drop=True)
        self.sales["date"] = _dt(self.sales["date"])
        self.sales["qty"] = self.sales["qty"].astype(float)
        self.sales["unit_price_paise"] = self.sales["unit_price_paise"].astype("int64")
        for c in ("invoice_id", "item_id", "payment_mode", "customer_id"):
            self.sales[c] = self.sales[c].astype(str)

        self.inventory = self.inventory[INVENTORY_COLS].reset_index(drop=True)
        self.inventory["item_id"] = self.inventory["item_id"].astype(str)
        self.inventory["stock_qty"] = self.inventory["stock_qty"].astype(float)

        self.expenses = self.expenses[EXPENSE_COLS].reset_index(drop=True)
        if len(self.expenses):
            self.expenses["date"] = _dt(self.expenses["date"])
        self.expenses["amount_paise"] = self.expenses["amount_paise"].astype("int64")

        self.suppliers = self.suppliers[SUPPLIER_COLS].reset_index(drop=True)
        self.suppliers["supplier_id"] = self.suppliers["supplier_id"].astype(str)

    # ---- derived, cached -------------------------------------------------
    @property
    def as_of_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.as_of)

    @cached_property
    def item_index(self) -> pd.DataFrame:
        return self.items.set_index("item_id")

    @cached_property
    def supplier_index(self) -> pd.DataFrame:
        return self.suppliers.set_index("supplier_id")

    @cached_property
    def stock(self) -> pd.Series:
        """item_id -> stock (negative clipped to 0, missing items = 0)."""
        s = self.inventory.groupby("item_id")["stock_qty"].sum().clip(lower=0)
        return s.reindex(self.items["item_id"], fill_value=0.0)

    @cached_property
    def sales_x(self) -> pd.DataFrame:
        """Sales lines enriched with GST back-out, cost and margin (all paise)."""
        s = self.sales.merge(
            self.items[["item_id", "name", "category", "supplier_id", "gst_rate", "cost_paise"]],
            on="item_id", how="inner",
        )
        line_total = np.rint(s["qty"].to_numpy() * s["unit_price_paise"].to_numpy()).astype("int64")
        rate = s["gst_rate"].to_numpy().astype("int64")
        taxable = taxable_from_inclusive(line_total, rate)
        cost = np.rint(s["qty"].to_numpy() * s["cost_paise"].to_numpy()).astype("int64")
        s["line_total_paise"] = line_total
        s["taxable_paise"] = taxable
        s["gst_paise"] = line_total - taxable
        s["cost_total_paise"] = cost
        s["margin_paise"] = taxable - cost
        return s

    @cached_property
    def first_sale(self) -> pd.Series:
        return self.sales.groupby("item_id")["date"].min()

    @cached_property
    def last_sale(self) -> pd.Series:
        return self.sales.groupby("item_id")["date"].max()

    @cached_property
    def customer_first_purchase(self) -> pd.Series:
        s = self.sales[self.sales["customer_id"] != ""]
        return s.groupby("customer_id")["date"].min()

    def sales_window(self, days: int, end: pd.Timestamp | None = None) -> pd.DataFrame:
        """Enriched sales in the `days` days ending at `end` (inclusive, default as_of)."""
        end = end if end is not None else self.as_of_ts
        start = end - pd.Timedelta(days=days - 1)
        s = self.sales_x
        return s[(s["date"] >= start) & (s["date"] <= end)]

    def summary(self) -> dict:
        s = self.sales
        return {
            "version": self.version,
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "loaded_at": self.loaded_at.isoformat(),
            "counts": {
                "items": int(len(self.items)),
                "sales_lines": int(len(s)),
                "invoices": int(s["invoice_id"].nunique()),
                "inventory": int(len(self.inventory)),
                "expenses": int(len(self.expenses)),
                "suppliers": int(len(self.suppliers)),
                "customers": int(len(self.customers)),
                "repayments": int(len(self.repayments)),
            },
            "customer_linked_bills_pct": round(
                100 * s.loc[s["customer_id"] != "", "invoice_id"].nunique() / max(s["invoice_id"].nunique(), 1), 1),
            "sales_from": s["date"].min().date().isoformat() if len(s) else None,
            "sales_to": s["date"].max().date().isoformat() if len(s) else None,
            "categories": sorted(self.items["category"].unique().tolist()),
        }

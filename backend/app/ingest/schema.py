"""Expected columns per table, with the header aliases real billing-app exports use."""

from __future__ import annotations

from dataclasses import dataclass, field

# Post-22-Sep-2025 GST slabs (indicative - verify). 12/28 accepted as legacy with a warning.
VALID_GST = {0, 5, 18, 40}
LEGACY_GST = {12, 28}

PAYMENT_MAP = {
    "upi": "UPI", "gpay": "UPI", "google pay": "UPI", "googlepay": "UPI", "phonepe": "UPI",
    "phone pe": "UPI", "paytm": "UPI", "bhim": "UPI", "qr": "UPI", "online": "UPI",
    "cash": "cash", "nakad": "cash", "naqad": "cash", "cod": "cash",
    "credit": "credit", "udhaar": "credit", "udhar": "credit", "khata": "credit",
    "due": "credit", "pay later": "credit",
    "card": "card", "debit card": "card", "credit card": "card", "pos": "card", "swipe": "card",
}
PAYMENT_MODES = ["UPI", "cash", "credit", "card", "other"]


@dataclass(frozen=True)
class Col:
    name: str
    kind: str  # id | text | money | int | qty | rate | date | payment
    required: bool = True
    aliases: tuple[str, ...] = field(default_factory=tuple)
    default: object = None


ID_ALIASES = ("id", "sku", "item_code", "code", "product_id", "product_code", "barcode")

TABLES: dict[str, list[Col]] = {
    "items": [
        Col("item_id", "id", aliases=ID_ALIASES),
        Col("name", "text", aliases=("item_name", "product", "product_name", "item", "description")),
        Col("category", "text", False, ("cat", "group", "item_group", "department"), "Uncategorised"),
        Col("hsn", "text", False, ("hsn_code", "hsn_sac"), ""),
        Col("mrp_incl_gst", "money", aliases=("mrp", "selling_price", "sale_price", "price", "rate")),
        Col("cost_ex_gst", "money", aliases=("cost", "cost_price", "purchase_price", "buying_price",
                                             "landing_cost")),
        Col("gst_rate", "rate", aliases=("gst", "gst_pct", "gst_percent", "tax_rate", "tax")),
        Col("pack_size", "int", False, ("pack", "case_size", "moq", "carton_size"), 1),
        Col("supplier_id", "id", False, ("supplier", "vendor", "vendor_id", "distributor"), "UNASSIGNED"),
        Col("lead_time_days", "int", False, ("lead_time", "lead_days", "delivery_days"), None),
    ],
    "sales": [
        Col("invoice_id", "id", aliases=("invoice", "invoice_no", "bill_no", "bill_number", "bill_id",
                                         "receipt_no", "voucher_no")),
        Col("date", "date", aliases=("bill_date", "invoice_date", "sale_date", "txn_date",
                                     "transaction_date")),
        Col("item_id", "id", aliases=ID_ALIASES),
        Col("qty", "qty", aliases=("quantity", "units", "qty_sold", "pcs")),
        Col("unit_price_incl_gst", "money", False, ("price", "rate", "unit_price", "selling_price", "mrp")),
        Col("payment_mode", "payment", False, ("payment", "mode", "pay_mode", "payment_method",
                                               "tender"), "cash"),
        Col("customer_id", "text", False, ("customer", "customer_code", "cust_id", "member_id",
                                           "khata_id"), ""),
        Col("customer_phone", "text", False, ("phone", "mobile", "mob", "mob_no", "mobile_no",
                                              "customer_mobile", "contact"), ""),
    ],
    "inventory": [
        Col("item_id", "id", aliases=ID_ALIASES),
        Col("stock_qty", "qty", aliases=("stock", "qty", "quantity", "closing_stock", "on_hand",
                                         "balance")),
        Col("as_of_date", "date", False, ("date", "stock_date", "as_of")),
    ],
    "expenses": [
        Col("date", "date", aliases=("expense_date", "txn_date")),
        Col("category", "text", aliases=("head", "type", "expense_head", "expense_type")),
        Col("amount", "money", aliases=("amt", "value", "total")),
        Col("payment_mode", "payment", False, ("payment", "mode", "pay_mode", "payment_method"), "cash"),
    ],
    "customers": [
        Col("customer_id", "text", False, ("id", "customer", "customer_code", "cust_id", "member_id"), ""),
        Col("name", "text", False, ("customer_name", "full_name"), ""),
        Col("phone", "text", False, ("mobile", "mob", "mob_no", "mobile_no", "contact", "whatsapp"), ""),
        Col("area", "text", False, ("locality", "address", "city"), ""),
        Col("khata", "bool", False, ("udhaar", "credit_allowed", "credit_account"), False),
    ],
    "repayments": [
        Col("date", "date", aliases=("payment_date", "paid_on", "txn_date")),
        Col("customer_id", "text", False, ("customer", "customer_code", "cust_id"), ""),
        Col("customer_phone", "text", False, ("phone", "mobile", "mob", "mob_no"), ""),
        Col("amount", "money", aliases=("amt", "paid", "amount_paid", "value")),
        Col("mode", "payment", False, ("payment_mode", "payment", "pay_mode"), "cash"),
    ],
    "suppliers": [
        Col("supplier_id", "id", aliases=("id", "vendor_id", "code")),
        Col("name", "text", aliases=("supplier_name", "vendor_name", "supplier")),
        Col("whatsapp_placeholder", "text", False, ("whatsapp", "phone", "mobile", "contact"), ""),
        Col("lead_time_days", "int", False, ("lead_time", "lead_days", "delivery_days"), None),
    ],
}

REQUIRED_TABLES = ("items", "sales")
OPTIONAL_TABLES = ("inventory", "expenses", "suppliers", "customers", "repayments")
TRUE_WORDS = {"yes", "y", "true", "1", "haan", "ha", "khata", "udhaar"}
DEFAULT_LEAD_TIME = 2

"""ArangoDB collections, edges, graph and indexes."""

from __future__ import annotations

DOC_COLLECTIONS = [
    "items", "suppliers", "categories", "invoices", "sales", "inventory", "expenses",
    "uploads", "chat_history", "meta", "purchase_orders", "customers", "repayments",
]

# edge collection -> (from collections, to collections)
EDGE_COLLECTIONS: dict[str, tuple[list[str], list[str]]] = {
    "supplies": (["suppliers"], ["items"]),      # supplier -> item
    "belongs_to": (["items"], ["categories"]),   # item -> category
    "contains": (["invoices"], ["items"]),       # bill -> item (qty, amount)
    "purchased": (["customers"], ["items"]),     # customer -> item (bills, qty, amount)
}

GRAPH_NAME = "kirana_graph"

# collection -> [(fields, unique)]
INDEXES: dict[str, list[tuple[list[str], bool]]] = {
    "items": [(["item_id"], True), (["category"], False)],
    "suppliers": [(["supplier_id"], True)],
    "invoices": [(["invoice_id"], True), (["date"], False)],
    "sales": [(["date"], False), (["item_id"], False)],
    "expenses": [(["date"], False)],
    "chat_history": [(["created_at"], False)],
    "uploads": [(["created_at"], False)],
    "purchase_orders": [(["created_at"], False), (["status"], False)],
    "customers": [(["customer_id"], True)],
    "repayments": [(["customer_id"], False), (["date"], False)],
}

# collections replaced on every dataset load (uploads/chat history are kept)
DATA_COLLECTIONS = [
    "items", "suppliers", "categories", "invoices", "sales", "inventory", "expenses",
    "supplies", "belongs_to", "contains", "customers", "repayments", "purchased",
]

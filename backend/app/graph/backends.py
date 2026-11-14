"""Two interchangeable graph engines with the same interface.

ArangoGraph   - the real thing: AQL traversals over kirana_graph in ArangoDB.
MemoryGraph   - the same answers computed with pandas; used when ArangoDB is not
                configured/reachable, and in tests (parity tests compare the two).

Both return plain dicts/lists - no maths beyond counting. Lift, money and stock
logic live in graph/insights.py.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.db import aql
from app.store.dataset import Dataset

log = logging.getLogger("muneem.graph")


class GraphBackend(Protocol):
    engine: str

    def suppliers_with_items(self) -> list[dict]: ...
    def supplier_items(self, supplier_id: str) -> list[str]: ...
    def item_context(self, item_id: str) -> dict | None: ...
    def co_purchase(self, item_id: str, n: int) -> dict: ...
    def supply_chain(self, supplier_id: str | None = None) -> list[dict]: ...


class MemoryGraph:
    engine = "memory-graph"

    def __init__(self, ds: Dataset):
        self.ds = ds
        self._pairs = ds.sales[["invoice_id", "item_id"]].drop_duplicates()

    def suppliers_with_items(self) -> list[dict]:
        items = self.ds.items
        out = []
        for s in self.ds.suppliers.sort_values("supplier_id").itertuples(index=False):
            ids = sorted(items.loc[items["supplier_id"] == s.supplier_id, "item_id"].tolist())
            out.append({"supplier_id": s.supplier_id, "name": s.name, "item_ids": ids})
        return out

    def supplier_items(self, supplier_id: str) -> list[str]:
        items = self.ds.items
        return sorted(items.loc[items["supplier_id"] == supplier_id, "item_id"].tolist())

    def item_context(self, item_id: str) -> dict | None:
        idx = self.ds.item_index
        if item_id not in idx.index:
            return None
        it = idx.loc[item_id]
        sup = None
        if it["supplier_id"] in self.ds.supplier_index.index:
            s = self.ds.supplier_index.loc[it["supplier_id"]]
            sup = {"supplier_id": it["supplier_id"], "name": s["name"],
                   "whatsapp_placeholder": s["whatsapp_placeholder"],
                   "lead_time_days": int(s["lead_time_days"])}
        items = self.ds.items
        peers = sorted(items.loc[(items["category"] == it["category"]) &
                                 (items["item_id"] != item_id), "item_id"].tolist())
        item = {"item_id": item_id, **{k: (v.item() if hasattr(v, "item") else v)
                                       for k, v in it.to_dict().items()}}
        return {"item": item, "supplier": sup, "category": it["category"], "peers": peers}

    def co_purchase(self, item_id: str, n: int) -> dict:
        p = self._pairs
        bills = p.loc[p["item_id"] == item_id, "invoice_id"]
        together = (p[p["invoice_id"].isin(set(bills)) & (p["item_id"] != item_id)]
                    ["item_id"].value_counts())
        counts = p["item_id"].value_counts()
        pairs = [{"item_id": iid, "together": int(c), "other_bills": int(counts.get(iid, 0))}
                 for iid, c in together.items()]
        pairs.sort(key=lambda r: (-r["together"], r["item_id"]))
        return {"base_bills": int(len(bills)), "total_bills": int(p["invoice_id"].nunique()),
                "pairs": pairs[:n]}

    def supply_chain(self, supplier_id: str | None = None) -> list[dict]:
        it = self.ds.items
        if supplier_id:
            it = it[it["supplier_id"] == supplier_id]
        sup = self.ds.supplier_index["name"]
        rows = [{"supplier_id": r.supplier_id, "supplier": sup.get(r.supplier_id, r.supplier_id),
                 "item_id": r.item_id, "item": r.name, "category": r.category,
                 "lead_time_days": int(r.lead_time_days)} for r in it.itertuples(index=False)]
        return sorted(rows, key=lambda r: (r["supplier_id"], r["item_id"]))


class ArangoGraph:
    engine = "arangodb"

    def __init__(self, db):
        self.db = db

    def _q(self, query: str, **bind) -> list:
        return list(self.db.aql.execute(query, bind_vars=bind, batch_size=5000))

    def suppliers_with_items(self) -> list[dict]:
        return self._q(aql.SUPPLIERS_WITH_ITEMS)

    def supplier_items(self, supplier_id: str) -> list[str]:
        return self._q(aql.SUPPLIER_ITEMS, supplier_id=supplier_id)

    def item_context(self, item_id: str) -> dict | None:
        res = self._q(aql.ITEM_CONTEXT, item_id=item_id)
        return res[0] if res else None

    def co_purchase(self, item_id: str, n: int) -> dict:
        res = self._q(aql.CO_PURCHASE, item_id=item_id, n=int(n))
        return res[0] if res else {"base_bills": 0, "total_bills": 0, "pairs": []}

    def supply_chain(self, supplier_id: str | None = None) -> list[dict]:
        return self._q(aql.SUPPLY_CHAIN, supplier_id=supplier_id)


class FallbackGraph:
    """Uses ArangoDB when it answers; silently falls back to memory if a query fails,
    and reports which engine actually answered."""

    def __init__(self, primary: ArangoGraph | None, memory: MemoryGraph):
        self.primary = primary
        self.memory = memory
        self.last_engine = memory.engine
        self.last_error: str | None = None

    @property
    def engine(self) -> str:
        return self.last_engine

    def _call(self, name: str, *args):
        if self.primary is not None:
            try:
                res = getattr(self.primary, name)(*args)
                self.last_engine = self.primary.engine
                return res
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("ArangoDB graph query %s failed, using memory: %s", name, exc)
        self.last_engine = self.memory.engine
        return getattr(self.memory, name)(*args)

    def suppliers_with_items(self):
        return self._call("suppliers_with_items")

    def supplier_items(self, supplier_id):
        return self._call("supplier_items", supplier_id)

    def item_context(self, item_id):
        return self._call("item_context", item_id)

    def co_purchase(self, item_id, n):
        return self._call("co_purchase", item_id, n)

    def supply_chain(self, supplier_id=None):
        return self._call("supply_chain", supplier_id)

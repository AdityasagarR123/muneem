"""Every AQL query Muneem runs lives in this file.

Rules:
* queries are fixed strings written by developers;
* user/LLM input reaches them ONLY as bind parameters (@name);
* AQL fetches and connects data - all money/inventory maths happens in Python.
"""

ALL_DOCS = """
FOR d IN @@coll
  RETURN UNSET(d, "_id", "_rev", "_key")
"""

SUPPLIERS_WITH_ITEMS = """
FOR s IN suppliers
  LET item_ids = (FOR i IN 1..1 OUTBOUND s supplies SORT i.item_id RETURN i.item_id)
  SORT s.supplier_id
  RETURN { supplier_id: s.supplier_id, name: s.name, item_ids: item_ids }
"""

SUPPLIER_ITEMS = """
FOR s IN suppliers
  FILTER s.supplier_id == @supplier_id
  LIMIT 1
  FOR i IN 1..1 OUTBOUND s supplies
    SORT i.item_id
    RETURN i.item_id
"""

ITEM_CONTEXT = """
FOR i IN items
  FILTER i.item_id == @item_id
  LIMIT 1
  LET supplier = FIRST(FOR s IN 1..1 INBOUND i supplies RETURN UNSET(s, "_id", "_rev", "_key"))
  LET category = FIRST(FOR c IN 1..1 OUTBOUND i belongs_to RETURN c.name)
  LET peers = (
    FOR c IN 1..1 OUTBOUND i belongs_to
      FOR p IN 1..1 INBOUND c belongs_to
        FILTER p._id != i._id
        SORT p.item_id
        RETURN p.item_id
  )
  RETURN { item: UNSET(i, "_id", "_rev", "_key"), supplier: supplier, category: category,
           peers: peers }
"""

CO_PURCHASE = """
FOR i IN items
  FILTER i.item_id == @item_id
  LIMIT 1
  LET bills = (FOR b IN 1..1 INBOUND i contains RETURN b._id)
  LET pairs = (
    FOR bid IN bills
      FOR o IN 1..1 OUTBOUND bid contains
        FILTER o._id != i._id
        COLLECT oid = o._id, other = o.item_id WITH COUNT INTO together
        SORT together DESC, other ASC
        LIMIT @n
        RETURN { item_id: other, together: together,
                 other_bills: LENGTH(FOR x IN 1..1 INBOUND oid contains RETURN 1) }
  )
  RETURN { base_bills: LENGTH(bills), total_bills: LENGTH(invoices), pairs: pairs }
"""

SUPPLY_CHAIN = """
FOR s IN suppliers
  FILTER @supplier_id == null OR s.supplier_id == @supplier_id
  FOR i, e IN 1..1 OUTBOUND s supplies
    LET category = FIRST(FOR c IN 1..1 OUTBOUND i belongs_to RETURN c.name)
    SORT s.supplier_id, i.item_id
    RETURN { supplier_id: s.supplier_id, supplier: s.name, item_id: i.item_id,
             item: i.name, category: category, lead_time_days: e.lead_time_days }
"""

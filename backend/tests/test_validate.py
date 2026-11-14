from datetime import date

import pandas as pd

from app.ingest.readers import read_csv_bytes
from app.ingest.validate import validate_tables

ITEMS = """item_id,name,category,mrp_incl_gst,cost_ex_gst,gst_rate,pack_size,supplier_id
A1,Atta 5kg,Staples,265,230,5,5,S1
A2,Oil 1L,Oil,165,150,5,12,S1
"""
SALES = """invoice_id,date,item_id,qty,unit_price_incl_gst,payment_mode
B1,2026-11-01,A1,2,265,UPI
B1,2026-11-01,A2,1,165,cash
B2,2026-11-02,A1,1,265,paytm
"""
STOCK = """item_id,stock_qty,as_of_date
A1,10,2026-11-02
A2,5,2026-11-02
"""
TODAY = date(2026, 11, 14)


def raw(**over):
    tables = {"items": ITEMS, "sales": SALES, "inventory": STOCK}
    tables.update(over)
    return {k: read_csv_bytes(v.encode()) for k, v in tables.items() if v is not None}


def test_clean_upload_loads():
    rep, ds = validate_tables(raw(), today=TODAY)
    assert rep.ok and ds is not None
    assert rep.rows_loaded["sales"] == 3
    assert set(ds.sales["payment_mode"]) == {"UPI", "cash"}  # paytm -> UPI
    assert any("only 2 day" in k for k in rep.warning_summary)


def test_missing_required_column_blocks():
    rep, ds = validate_tables(raw(items="item_id,name\nA1,Atta\n"), today=TODAY)
    assert not rep.ok and ds is None
    assert "Missing required column" in rep.errors[0].message


def test_bad_gst_rate_is_row_error_with_row_number():
    bad = ITEMS.replace("A2,Oil 1L,Oil,165,150,5", "A2,Oil 1L,Oil,165,150,7")
    rep, ds = validate_tables(raw(items=bad), today=TODAY)
    assert not rep.ok and ds is None
    err = next(e for e in rep.errors if e.column == "gst_rate")
    assert err.row == 3  # header is row 1


def test_partial_mode_drops_bad_rows():
    bad = ITEMS.replace("A2,Oil 1L,Oil,165,150,5", "A2,Oil 1L,Oil,165,150,7")
    rep, ds = validate_tables(raw(items=bad), allow_partial=True, today=TODAY)
    assert rep.ok
    assert list(ds.items["item_id"]) == ["A1"]
    assert set(ds.sales["item_id"]) == {"A1"}  # orphaned A2 sales dropped


def test_negative_stock_is_warning_and_clipped():
    rep, ds = validate_tables(raw(inventory=STOCK.replace("A2,5", "A2,-3")), today=TODAY)
    assert rep.ok
    assert ds.stock["A2"] == 0
    assert any("negative stock" in k for k in rep.warning_summary)


def test_orphan_sale_and_future_date():
    s = SALES + "B3,2026-11-03,ZZ,1,10,cash\nB4,2027-01-01,A1,1,265,cash\n"
    rep, _ = validate_tables(raw(sales=s), today=TODAY)
    msgs = " ".join(rep.error_summary)
    assert "item_id not found" in msgs and "future" in msgs


def test_messy_headers_dates_and_money():
    items = ("SKU,Product Name,Category,MRP,Cost Price,GST %\n"
             "A1,Atta 5kg,Staples,\"₹ 1,265.00\",\"Rs. 1,100\",5%\n")
    sales = "Bill No,Bill Date,SKU,Qty,Rate,Payment\nB1,03-11-2026,A1,2,1265,Udhaar\n"
    rep, ds = validate_tables(raw(items=items, sales=sales, inventory=None), today=TODAY)
    assert rep.ok, rep.errors
    assert ds.items.loc[0, "mrp_paise"] == 126500
    assert ds.items.loc[0, "cost_paise"] == 110000
    assert ds.sales.loc[0, "date"] == pd.Timestamp("2026-11-03")  # dd-mm-yyyy
    assert ds.sales.loc[0, "payment_mode"] == "credit"
    assert ds.items.loc[0, "supplier_id"] == "UNASSIGNED"
    assert any("no stock list" in k for k in rep.warning_summary)


def test_control_characters_are_stripped():
    items = ITEMS.replace("Atta 5kg", "Atta\x00 5kg\x1b[31m")
    rep, ds = validate_tables(raw(items=items), today=TODAY)
    assert rep.ok
    assert "\x00" not in ds.items.loc[0, "name"] and "\x1b" not in ds.items.loc[0, "name"]


def test_legacy_gst_slab_is_warning():
    items = ITEMS.replace("A2,Oil 1L,Oil,165,150,5", "A2,Oil 1L,Oil,165,130,12")
    rep, ds = validate_tables(raw(items=items), today=TODAY)
    assert rep.ok
    assert any("legacy GST" in k for k in rep.warning_summary)

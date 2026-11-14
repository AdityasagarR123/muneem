"""Live Google Sheets sync - Google is mocked; nothing leaves the machine."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ingest.sheets import SheetError, SheetSync, export_url
from app.main import create_app
from app.store.manager import DataManager

URL = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=42"
GOOD = ("Date,Expense head,Amount (Rs),Paid by\n"
        "2026-11-01,Rent,25000,UPI\n2026-11-05,Electricity,7650,cash\n2026-11-10,Salary,18000,UPI\n")


class FakeGoogle:
    def __init__(self, body=GOOD, status=200, ctype="text/csv"):
        self.body, self.status, self.ctype, self.calls = body, status, ctype, []

    def __call__(self, req: httpx.Request):
        self.calls.append(str(req.url))
        return httpx.Response(self.status, content=self.body.encode(), headers={"content-type": self.ctype})


MAPPING = {"date": "date", "category": "expense_head", "amount": "amount_rs", "payment_mode": "paid_by"}


@pytest.fixture()
def m(settings):
    mgr = DataManager(settings)
    mgr.startup()
    return mgr


def sync_with(m, google):
    m._sheets = SheetSync(m, http=httpx.Client(transport=httpx.MockTransport(google)))
    return m._sheets


# ------------------------------------------------------------------ links
@pytest.mark.parametrize("url,expected", [
    (URL, "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/export?format=csv&gid=42"),
    ("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit?usp=sharing",
     "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/export?format=csv&gid=0"),
    ("https://docs.google.com/spreadsheets/d/e/2PACX-1vQabcdefghijklmnopqrstuvwxyz/pubhtml",
     "https://docs.google.com/spreadsheets/d/e/2PACX-1vQabcdefghijklmnopqrstuvwxyz/pub?output=csv&gid=0"),
])
def test_export_url(url, expected):
    assert export_url(url) == expected


@pytest.mark.parametrize("url", ["http://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz01/edit",
                                 "https://evil.example.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz01/",
                                 "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz01/edit",
                                 "https://127.0.0.1/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz01/"])
def test_only_google_sheets_links(url):
    with pytest.raises(SheetError):
        export_url(url)


# ------------------------------------------------------------------ sync
def test_link_replaces_table_and_skips_unchanged(m):
    g = FakeGoogle()
    s = sync_with(m, g)
    before_sales = len(m.ds.sales)
    res = s.link(URL, "Shop expenses", "expenses", MAPPING)
    assert res["status"] == "ok" and res["rows"] == 3
    assert len(m.ds.expenses) == 3 and m.ds.expenses["amount_paise"].sum() == 5_065_000
    assert len(m.ds.sales) == before_sales  # other tables untouched
    version = m.ds.version
    out = s.sync()  # same content -> no rebuild
    assert out[0]["status"] == "ok" and m.ds.version == version
    assert all("export?format=csv&gid=42" in c for c in g.calls)


def test_sheet_edit_flows_in(m):
    g = FakeGoogle()
    s = sync_with(m, g)
    sid = s.link(URL, "Shop expenses", "expenses", MAPPING)["sheet_id"]
    g.body = GOOD + "2026-11-12,Repairs,1200,cash\n"
    assert s.sync(sid)["rows"] == 4
    assert len(m.ds.expenses) == 4


def test_broken_edit_keeps_old_data(m):
    g = FakeGoogle()
    s = sync_with(m, g)
    sid = s.link(URL, "Shop expenses", "expenses", MAPPING)["sheet_id"]
    g.body = "Date,Expense head,Amount (Rs),Paid by\nnot a date,Rent,abc,UPI\n"
    res = s.sync(sid)
    assert res["status"] == "error" and "nothing changed" in res["error"]
    assert len(m.ds.expenses) == 3


def test_renamed_column_is_reported(m):
    g = FakeGoogle()
    s = sync_with(m, g)
    sid = s.link(URL, "Shop expenses", "expenses", MAPPING)["sheet_id"]
    g.body = GOOD.replace("Amount (Rs)", "Total")
    res = s.sync(sid)
    assert res["status"] == "error" and "amount_rs" in res["error"]


@pytest.mark.parametrize("google,msg", [(FakeGoogle(status=403), "refused"),
                                        (FakeGoogle(body="<html>Sign in</html>", ctype="text/html"), "web page")])
def test_unshared_sheet_is_not_linked(m, google, msg):
    s = sync_with(m, google)
    with pytest.raises(SheetError, match=msg):
        s.link(URL, "x", "expenses", MAPPING)
    assert s.list() == []


# ------------------------------------------------------------------ API
def test_api_analyze_link_list_unlink():
    with TestClient(create_app()) as c:
        m = c.app.state.manager
        sync_with(m, FakeGoogle())
        a = c.post("/api/sheets/analyze", json={"url": URL, "name": "Shop expenses"}).json()
        t = a["tables"][0]
        assert t["entity"] == "expenses"
        mapping = {x["field"]: x["source"] for x in t["mapping"]}
        assert mapping["amount"] == "amount_rs"
        r = c.post("/api/sheets", json={"url": URL, "name": "Shop expenses", "entity": "expenses",
                                        "mapping": mapping}).json()
        sid = r["sheet"]["sheet_id"]
        assert c.get("/api/sheets").json()["sheets"][0]["sheet_id"] == sid
        assert c.post(f"/api/sheets/{sid}/sync").json()["sheet"]["status"] == "ok"
        assert c.delete(f"/api/sheets/{sid}").json()["ok"] is True
        assert c.post("/api/sheets/analyze", json={"url": "https://example.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz"}).status_code == 422

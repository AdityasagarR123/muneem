from datetime import date

import pytest

from app.analytics.festivals import Festival, FestivalCalendar
from app.analytics.stock import reorder_math, tier_for


def test_golden_reorder_case():
    # d = 6/day, L = 2, R = 2, sigma = 2, stock = 12, pack = 6
    m = reorder_math(d=6, sigma=2, lead=2, review=2, stock=12, pack=6, z=1.65)
    assert m["safety_stock"] == pytest.approx(4.667, abs=1e-3)
    assert m["order_qty_raw"] == 17
    assert m["order_qty"] == 18
    assert m["days_left"] == 2.0
    assert m["tier"] == "SOON"


def test_tiers():
    assert tier_for(1.5, 2, 2) == "CRITICAL"
    assert tier_for(2.0, 2, 2) == "SOON"
    assert tier_for(3.99, 2, 2) == "SOON"
    assert tier_for(4.0, 2, 2) == "OK"
    assert tier_for(None, 2, 2) == "OK"  # no demand -> nothing runs out


def test_no_order_when_stock_is_enough():
    m = reorder_math(d=1, sigma=0, lead=2, review=2, stock=50, pack=10)
    assert m["order_qty"] == 0


def test_zero_stock_with_demand_is_critical():
    assert reorder_math(d=3, sigma=1, lead=2, review=2, stock=0)["tier"] == "CRITICAL"


def test_forward_festival_multiplier():
    cal = FestivalCalendar([Festival("Test", date(2026, 1, 10), date(2026, 1, 20),
                                     date(2026, 1, 15), {"Sweets": 3.0})])
    m, names = cal.forward_multiplier("Sweets", date(2026, 1, 15), date(2026, 1, 15))
    assert m == 3.0 and names == ["Test"]
    m, names = cal.forward_multiplier("Sweets", date(2026, 2, 1), date(2026, 2, 3))
    assert m == 1.0 and names == []
    assert cal.forward_multiplier("Staples", date(2026, 1, 15), date(2026, 1, 15))[0] == 1.0


def test_every_item_has_a_row(ds, stock_df):
    assert len(stock_df) == len(ds.items)
    assert set(stock_df["tier"]) <= {"CRITICAL", "SOON", "OK", "UNKNOWN"}


def test_insufficient_history_is_never_a_guess(stock_df, expected):
    row = stock_df.set_index("item_id").loc[expected["insufficient_data_item"]]
    assert row["tier"] == "UNKNOWN"
    assert row["confidence"] == "insufficient_data"
    assert row["order_qty"] == 0

import numpy as np

from app.core.money import add_gst, fmt_inr, gst_from_inclusive, taxable_from_inclusive, to_paise


def test_gst_back_out_worked_example():
    # ₹105 incl. 5% GST -> taxable ₹100, GST ₹5
    assert taxable_from_inclusive(10500, 5) == 10000
    assert gst_from_inclusive(10500, 5) == 500


def test_margin_worked_example():
    taxable = taxable_from_inclusive(10500, 5)
    cost = 8800
    margin = taxable - cost
    assert margin == 1200
    assert round(100 * margin / taxable, 2) == 12.0


def test_gst_rounds_half_up_to_paisa():
    # 100 / 1.18 = 84.745... -> 84.75
    assert taxable_from_inclusive(10000, 18) == 8475
    assert taxable_from_inclusive(10000, 0) == 10000
    assert taxable_from_inclusive(4500, 40) == 3214  # 32.142857


def test_vectorised_gst():
    out = taxable_from_inclusive(np.array([10500, 11800, 2800]), np.array([5, 18, 0]))
    assert out.tolist() == [10000, 10000, 2800]


def test_add_gst_and_to_paise():
    assert add_gst(10000, 18) == 11800
    assert to_paise("105.505") == 10551
    assert to_paise(88) == 8800


def test_indian_grouping():
    assert fmt_inr(12345678, decimals=True) == "₹1,23,456.78"
    assert fmt_inr(12345678) == "₹1,23,457"
    assert fmt_inr(100000000) == "₹10,00,000"
    assert fmt_inr(-50000) == "-₹500"

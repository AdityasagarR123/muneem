"""Money helpers.

Rule: every amount inside Muneem is an integer number of paise. Conversion to
rupees happens only when presenting results.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def to_paise(value) -> int:
    """'105', 105, 105.0, '105.50' -> paise (half-up)."""
    d = Decimal(str(value).strip())
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def taxable_from_inclusive(price_incl_paise, gst_rate_pct):
    """Back GST out of a GST-inclusive price (MRP is GST-inclusive in India).

    taxable = price_incl / (1 + rate/100), rounded half-up to the paisa.
    Pure integer arithmetic, so it works on ints, numpy arrays and pandas Series.
    """
    den = 100 + gst_rate_pct
    num = price_incl_paise * 100
    return (2 * num + den) // (2 * den)


def gst_from_inclusive(price_incl_paise, gst_rate_pct):
    return price_incl_paise - taxable_from_inclusive(price_incl_paise, gst_rate_pct)


def add_gst(price_ex_paise, gst_rate_pct):
    """price_ex * (1 + rate/100), half-up."""
    num = price_ex_paise * (100 + gst_rate_pct)
    return (2 * num + 100) // 200


def rupees(paise) -> float:
    return round(float(paise) / 100.0, 2)


def fmt_inr(paise, decimals: bool = False) -> str:
    """Indian digit grouping: 12345678 paise -> '₹1,23,456.78' (or '₹1,23,457')."""
    p = int(round(float(paise)))
    neg = p < 0
    p = abs(p)
    if not decimals:
        p = ((p + 50) // 100) * 100
    rs, ps = divmod(p, 100)
    s = str(rs)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    out = f"₹{s}" + (f".{ps:02d}" if decimals else "")
    return f"-{out}" if neg else out

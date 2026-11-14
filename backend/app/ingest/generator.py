"""Seeded synthetic kirana dataset with planted stories.

    python -m app.ingest.generator            # writes data/sample/*.csv + expected.json

The data is SYNTHETIC. It is shaped like a real single-counter kirana store:
~64 SKUs, 120 days of bills, UPI share rising, a Diwali spike, GST slabs as
indicative post-Sept-2025 rates (verify before relying on them).

Planted stories (checked by tests/test_scenarios.py through expected.json):
 1. Two fast staples (Atta 5kg, Toor Dal) will run out inside their lead time.
 2. Two more staples from the same supplier are "Soon" -> Sharma Traders is the
    supplier to call first (graph story).
 3. Three dead-stock items with real money locked.
 4. Sunflower oil: high revenue, ~3% margin.
 5. Electricity bill jumps in the latest 30 days.
 6. Festive sweets spike for Diwali, then normalise (naive average would over-order).
 7. A new item with only 9 days of history -> insufficient_data, never a guess.
 8. Atta and sunflower oil are bought together on many bills.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from app.analytics.festivals import FestivalCalendar
from app.config import get_settings
from app.core.money import taxable_from_inclusive, to_paise
from app.ingest.customer_sim import CustomerSim

SEED = 20261114
END = date(2026, 11, 13)
DAYS = 120

SUPPLIERS = [
    # id, name, lead_time_days
    ("S01", "Sharma Traders", 2),
    ("S02", "Gupta Oil & Provisions", 3),
    ("S03", "Shree Dairy Distributors", 1),
    ("S04", "Balaji Snacks Agency", 2),
    ("S05", "Cool Drinks Distributors", 3),
    ("S06", "Clean Home Supplies", 4),
    ("S07", "Mithai & Dry Fruit Wholesale", 5),
    ("S08", "Mehta Personal Care", 4),
]
LEAD = {s[0]: s[2] for s in SUPPLIERS}


@dataclass(frozen=True)
class Sku:
    item_id: str
    name: str
    category: str
    mrp: float
    gst: int
    margin: float  # target margin on taxable price
    pack: int
    base: float  # normal units/day
    supplier: str
    hsn: str = ""


ITEMS: list[Sku] = [
    # Staples - Sharma Traders
    Sku("I001", "Chakki Atta 5kg", "Staples", 265, 5, 0.09, 5, 14, "S01", "1101"),
    Sku("I002", "Chakki Atta 10kg", "Staples", 510, 5, 0.08, 2, 4, "S01", "1101"),
    Sku("I003", "Basmati Rice 1kg", "Staples", 140, 5, 0.14, 10, 6, "S01", "1006"),
    Sku("I004", "Sona Masoori Rice 5kg", "Staples", 380, 5, 0.10, 4, 3, "S01", "1006"),
    Sku("I005", "Toor Dal 1kg", "Staples", 175, 5, 0.11, 10, 9, "S01", "0713"),
    Sku("I006", "Moong Dal 1kg", "Staples", 150, 5, 0.12, 10, 3, "S01", "0713"),
    Sku("I007", "Chana Dal 1kg", "Staples", 110, 5, 0.12, 10, 3, "S01", "0713"),
    Sku("I008", "Masoor Dal 1kg", "Staples", 120, 5, 0.12, 10, 2, "S01", "0713"),
    Sku("I009", "Sugar 1kg", "Staples", 48, 5, 0.06, 25, 12, "S01", "1701"),
    Sku("I010", "Iodised Salt 1kg", "Staples", 28, 0, 0.15, 25, 6, "S01", "2501"),
    Sku("I011", "Besan 500g", "Staples", 65, 5, 0.14, 10, 3, "S01", "1106"),
    Sku("I012", "Maida 1kg", "Staples", 55, 5, 0.12, 10, 3, "S01", "1101"),
    Sku("I013", "Poha 500g", "Staples", 45, 5, 0.16, 10, 3, "S01", "1904"),
    Sku("I014", "Sooji 500g", "Staples", 40, 5, 0.15, 10, 2, "S01", "1103"),
    # Edible oil & ghee - Gupta
    Sku("I015", "Refined Sunflower Oil 1L", "Edible Oil & Ghee", 165, 5, 0.03, 12, 11, "S02", "1512"),
    Sku("I016", "Kachi Ghani Mustard Oil 1L", "Edible Oil & Ghee", 185, 5, 0.09, 12, 5, "S02", "1514"),
    Sku("I017", "Desi Ghee 1L", "Edible Oil & Ghee", 650, 5, 0.10, 6, 2, "S02", "0405"),
    Sku("I018", "Groundnut Oil 1L", "Edible Oil & Ghee", 210, 5, 0.09, 12, 2, "S02", "1508"),
    Sku("I019", "Extra Virgin Olive Oil 500ml", "Edible Oil & Ghee", 799, 5, 0.18, 6, 0.3, "S02", "1509"),
    # Spices - Gupta
    Sku("I020", "Turmeric Powder 200g", "Spices", 60, 5, 0.22, 10, 2, "S02", "0910"),
    Sku("I021", "Red Chilli Powder 200g", "Spices", 80, 5, 0.22, 10, 2, "S02", "0904"),
    Sku("I022", "Garam Masala 100g", "Spices", 85, 5, 0.25, 10, 1.5, "S02", "0910"),
    Sku("I023", "Jeera 200g", "Spices", 120, 5, 0.20, 10, 1.2, "S02", "0909"),
    # Dairy & bakery - Shree Dairy
    Sku("I024", "Toned Milk 500ml", "Dairy & Bakery", 30, 0, 0.08, 20, 40, "S03", "0401"),
    Sku("I025", "Full Cream Milk 500ml", "Dairy & Bakery", 36, 0, 0.08, 20, 18, "S03", "0401"),
    Sku("I026", "Dahi 400g", "Dairy & Bakery", 45, 5, 0.12, 12, 8, "S03", "0403"),
    Sku("I027", "Paneer 200g", "Dairy & Bakery", 95, 0, 0.12, 10, 5, "S03", "0406"),
    Sku("I028", "Salted Butter 100g", "Dairy & Bakery", 62, 5, 0.10, 20, 4, "S03", "0405"),
    Sku("I029", "White Bread 400g", "Dairy & Bakery", 45, 0, 0.12, 10, 9, "S03", "1905"),
    Sku("I030", "Brown Bread 400g", "Dairy & Bakery", 55, 0, 0.13, 10, 3, "S03", "1905"),
    Sku("I031", "Eggs (Tray of 6)", "Dairy & Bakery", 48, 0, 0.12, 5, 6, "S03", "0407"),
    # Snacks - Balaji
    Sku("I032", "Glucose Biscuits 200g", "Snacks & Biscuits", 25, 5, 0.12, 24, 14, "S04", "1905"),
    Sku("I033", "Cream Biscuits 150g", "Snacks & Biscuits", 35, 5, 0.14, 24, 8, "S04", "1905"),
    Sku("I034", "Aloo Bhujia 400g", "Snacks & Biscuits", 110, 5, 0.18, 12, 4, "S04", "2106"),
    Sku("I035", "Salted Chips 52g", "Snacks & Biscuits", 20, 5, 0.17, 30, 15, "S04", "2005"),
    Sku("I036", "Instant Noodles 70g", "Snacks & Biscuits", 15, 5, 0.12, 48, 18, "S04", "1902"),
    Sku("I037", "Chocolate Bar 40g", "Snacks & Biscuits", 40, 5, 0.15, 24, 6, "S04", "1806"),
    Sku("I038", "Rusk 300g", "Snacks & Biscuits", 50, 5, 0.15, 12, 4, "S04", "1905"),
    Sku("I039", "Millet Muesli 500g", "Snacks & Biscuits", 299, 5, 0.20, 6, 1.5, "S04", "1904"),
    # Beverages - Cool Drinks
    Sku("I040", "Cola 750ml", "Beverages", 45, 40, 0.15, 24, 8, "S05", "2202"),
    Sku("I041", "Lemon Soda 750ml", "Beverages", 40, 40, 0.15, 24, 4, "S05", "2202"),
    Sku("I042", "Mango Drink 600ml", "Beverages", 40, 5, 0.16, 24, 6, "S05", "2202"),
    Sku("I043", "Packaged Water 1L", "Beverages", 20, 5, 0.20, 12, 14, "S05", "2201"),
    Sku("I044", "Tea Leaves 500g", "Beverages", 260, 5, 0.12, 12, 4, "S05", "0902"),
    Sku("I045", "Instant Coffee 50g", "Beverages", 175, 5, 0.12, 12, 1.5, "S05", "2101"),
    Sku("I046", "Rose Sharbat 750ml", "Beverages", 160, 5, 0.20, 12, 0.4, "S05", "2106"),
    # Household - Clean Home
    Sku("I047", "Detergent Powder 1kg", "Household", 120, 18, 0.14, 12, 4, "S06", "3402"),
    Sku("I048", "Dishwash Bar 200g", "Household", 25, 18, 0.18, 30, 6, "S06", "3401"),
    Sku("I049", "Floor Cleaner 1L", "Household", 199, 18, 0.18, 12, 1.5, "S06", "3402"),
    Sku("I050", "Toilet Cleaner 500ml", "Household", 99, 18, 0.18, 12, 1.5, "S06", "3402"),
    Sku("I051", "Herbal Phenyl 5L", "Household", 450, 18, 0.22, 4, 0.25, "S06", "3808"),
    Sku("I052", "Agarbatti Pack", "Household", 50, 5, 0.25, 20, 3, "S06", "3307"),
    Sku("I053", "Matchbox (10 pcs)", "Household", 20, 5, 0.15, 20, 3, "S06", "3605"),
    # Personal care - Mehta
    Sku("I054", "Bathing Soap 100g", "Personal Care", 40, 5, 0.15, 24, 6, "S08", "3401"),
    Sku("I055", "Toothpaste 150g", "Personal Care", 99, 5, 0.14, 12, 3, "S08", "3306"),
    Sku("I056", "Shampoo Sachets (Strip of 10)", "Personal Care", 30, 5, 0.18, 20, 4, "S08", "3305"),
    Sku("I057", "Coconut Hair Oil 300ml", "Personal Care", 145, 5, 0.15, 12, 2, "S08", "3305"),
    # Festive & sweets - Mithai & Dry Fruit
    Sku("I058", "Soan Papdi 500g", "Festive & Sweets", 180, 5, 0.18, 12, 1.5, "S07", "1704"),
    Sku("I059", "Kaju Katli 250g", "Festive & Sweets", 320, 5, 0.18, 6, 0.8, "S07", "1704"),
    Sku("I060", "Gulab Jamun Tin 1kg", "Festive & Sweets", 260, 5, 0.17, 6, 0.8, "S07", "2106"),
    Sku("I061", "Diya Set (12 pcs)", "Festive & Sweets", 120, 5, 0.30, 10, 0.05, "S07", "6913"),
    # Dry fruits
    Sku("I062", "Cashew 250g", "Dry Fruits", 290, 5, 0.15, 10, 1.5, "S07", "0801"),
    Sku("I063", "Almonds 250g", "Dry Fruits", 260, 5, 0.15, 10, 1.5, "S07", "0802"),
    Sku("I064", "Raisins 250g", "Dry Fruits", 120, 5, 0.18, 10, 1.2, "S07", "0806"),
]

WEEKDAY = [0.95, 0.92, 0.93, 0.95, 1.02, 1.15, 1.25]  # Mon..Sun
CRITICAL = {"I001": 1.3, "I005": 1.3}   # days of cover
SOON = {"I009": 3.0, "I006": 2.5}
DEAD = {"I019": 18, "I046": 24, "I051": 8}  # stock units; no sales in last 45 days
NEW_ITEM = "I039"                        # first sale 8 days before END -> 9 days history
FESTIVE_STOCK_COVER = (10, 16)
PAIRS = [("I001", "I015", 0.6), ("I029", "I031", 0.5)]  # (a, b, prob) bought together


def _cost(sku: Sku) -> float:
    taxable = taxable_from_inclusive(to_paise(sku.mrp), sku.gst) / 100
    return round(taxable * (1 - sku.margin), 2)


def _payment_shares(progress: float) -> tuple[list[str], list[float]]:
    upi = 0.45 + 0.23 * progress
    credit, card = 0.07, 0.03
    return ["UPI", "cash", "credit", "card"], [upi, 1 - upi - credit - card, credit, card]


def generate(out_dir: Path, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    cal = FestivalCalendar.load(get_settings().festivals_file)
    out_dir.mkdir(parents=True, exist_ok=True)
    dates = [END - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
    by_id = {s.item_id: s for s in ITEMS}
    cost = {s.item_id: _cost(s) for s in ITEMS}
    diwali = next(f for f in cal.festivals if f.name == "Diwali")

    # customers use their own random stream, so every earlier planted story stays identical
    sim = CustomerSim(np.random.default_rng(seed + 1), dates, diwali)
    sales_rows: list[list] = []
    daily_qty: dict[str, list[int]] = {s.item_id: [] for s in ITEMS}
    daily_cogs: list[float] = []

    for di, d in enumerate(dates):
        wk = WEEKDAY[d.weekday()]
        trend = 1 + 0.08 * di / (DAYS - 1)
        lines: list[tuple[str, int]] = []
        cogs = 0.0
        for s in ITEMS:
            lam = s.base * wk * trend * cal.factor(s.category, d)
            if s.item_id in DEAD and d > END - timedelta(days=45):
                lam = 0.0
            if s.item_id == NEW_ITEM and d < END - timedelta(days=8):
                lam = 0.0
            if s.item_id == "I061":  # diyas sell only around Diwali
                lam = 4.0 * diwali.factor("Festive & Sweets", d) / 3.5 if diwali.contains(d) else 0.02
            q = int(rng.poisson(lam)) if lam > 0 else 0
            daily_qty[s.item_id].append(q)
            cogs += q * cost[s.item_id]
            while q > 0:
                c = int(min(q, rng.integers(1, 4)))
                lines.append((s.item_id, c))
                q -= c
        daily_cogs.append(cogs)
        if not lines:
            continue
        n_bills = max(1, round(len(lines) / 2.3))
        assign = rng.integers(0, n_bills, size=len(lines))
        for a, b, p in PAIRS:
            ia = [k for k, (iid, _) in enumerate(lines) if iid == a]
            ib = [k for k, (iid, _) in enumerate(lines) if iid == b]
            for x, y in zip(ia, ib):
                if rng.random() < p:
                    assign[y] = assign[x]
        agg: dict[tuple[int, str], int] = defaultdict(int)
        for k, (iid, q) in enumerate(lines):
            agg[(int(assign[k]), iid)] += q
        modes, shares = _payment_shares(di / (DAYS - 1))
        bill_mode = {b: modes[int(rng.choice(len(modes), p=shares))] for b in range(n_bills)}
        sim.start_day()
        bill_cust = {b: sim.assign(bill_mode[b], d) for b in sorted({b for b, _ in agg})}
        bill_total: dict[int, int] = defaultdict(int)
        for (b, iid), q in sorted(agg.items()):
            sku = by_id[iid]
            price = sku.mrp
            if rng.random() < 0.06:  # occasional small discount
                price = round(sku.mrp * 0.97 * 2) / 2
            sales_rows.append([f"B{d:%y%m%d}-{b + 1:03d}", d.isoformat(), iid, q,
                               f"{price:.2f}", bill_mode[b], bill_cust[b]])
            bill_total[b] += q * to_paise(f"{price:.2f}")
        for b, cid in bill_cust.items():
            if cid and bill_mode[b] == "credit":
                sim.add_debit(cid, bill_total[b])
        sim.end_day(d)

    # ---- inventory (planted) -------------------------------------------
    inventory_rows = []
    expected_dead = []
    for s in ITEMS:
        last14 = sum(daily_qty[s.item_id][-14:]) / 14
        if s.item_id in CRITICAL:
            stock = math.floor(last14 * CRITICAL[s.item_id])
        elif s.item_id in SOON:
            stock = math.floor(last14 * SOON[s.item_id])
        elif s.item_id in DEAD:
            stock = DEAD[s.item_id]
            expected_dead.append({
                "item_id": s.item_id, "stock": stock,
                "money_trapped_paise": stock * to_paise(cost[s.item_id]),
            })
        elif s.item_id == NEW_ITEM:
            stock = 9
        elif cal.affects(s.category):
            stock = math.ceil(max(s.base, 0.3) * rng.uniform(*FESTIVE_STOCK_COVER))
        elif last14 > 0:
            stock = math.ceil(last14 * rng.uniform(8, 25))
        else:
            stock = int(rng.integers(5, 20))
        inventory_rows.append([s.item_id, stock, END.isoformat()])

    # ---- expenses -------------------------------------------------------
    expense_rows = []
    month_starts = sorted({date(d.year, d.month, 1) for d in dates})
    for m in month_starts:
        for day, cat, amt, mode in [(1, "rent", 15000, "UPI"), (1, "salary", 12000, "cash")]:
            d = m.replace(day=day)
            if dates[0] <= d <= END:
                expense_rows.append([d.isoformat(), cat, f"{amt:.2f}", mode])
        d = m.replace(day=5)
        if dates[0] <= d <= END:
            amt = 7650.0 if (d.year, d.month) == (END.year, END.month) else 3800 + rng.normal(0, 120)
            expense_rows.append([d.isoformat(), "electricity", f"{amt:.2f}", "UPI"])
        for day in (8, 17, 26):
            d = m.replace(day=day)
            if dates[0] <= d <= END:
                expense_rows.append([d.isoformat(), "other", f"{rng.uniform(350, 650):.2f}", "cash"])
    for i, d in enumerate(dates):
        if d.weekday() == 0 and i >= 7:  # Monday: pay suppliers for last week's goods
            amt = sum(daily_cogs[i - 7:i]) * rng.uniform(0.92, 1.08)
            expense_rows.append([d.isoformat(), "supplier", f"{amt:.2f}",
                                 "UPI" if rng.random() < 0.7 else "cash"])
    expense_rows.sort(key=lambda r: r[0])

    # ---- write ----------------------------------------------------------
    def write(name: str, header: list[str], rows: list[list]) -> None:
        with open(out_dir / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    write("suppliers.csv", ["supplier_id", "name", "whatsapp_placeholder", "lead_time_days"],
          [[sid, name, f"+91-XXXXX-0{i:04d}", lt] for i, (sid, name, lt) in enumerate(SUPPLIERS, 1)])
    write("items.csv", ["item_id", "name", "category", "hsn", "mrp_incl_gst", "cost_ex_gst",
                        "gst_rate", "pack_size", "supplier_id", "lead_time_days"],
          [[s.item_id, s.name, s.category, s.hsn, f"{s.mrp:.2f}", f"{cost[s.item_id]:.2f}",
            s.gst, s.pack, s.supplier, LEAD[s.supplier]] for s in ITEMS])
    write("sales.csv", ["invoice_id", "date", "item_id", "qty", "unit_price_incl_gst",
                        "payment_mode", "customer_id"], sales_rows)
    write("customers.csv", ["customer_id", "name", "phone", "area", "khata"], sim.customer_rows())
    write("repayments.csv", ["date", "customer_id", "amount", "mode"], sim.repayments)
    write("inventory.csv", ["item_id", "stock_qty", "as_of_date"], inventory_rows)
    write("expenses.csv", ["date", "category", "amount", "payment_mode"], expense_rows)

    expected = {
        "synthetic": True,
        "seed": seed,
        "as_of": END.isoformat(),
        "critical_items": sorted(CRITICAL),
        "soon_items": sorted(SOON),
        "top_risk_supplier": "S01",
        "dead_stock": sorted(expected_dead, key=lambda r: r["item_id"]),
        "dead_stock_total_paise": sum(r["money_trapped_paise"] for r in expected_dead),
        "high_revenue_low_margin": "I015",
        "expense_spike_category": "electricity",
        "festive_adjusted_item": "I058",
        "insufficient_data_item": NEW_ITEM,
        "bought_together": ["I001", "I015"],
        "crm": sim.expected(),
    }
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return expected


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else get_settings().data_dir
    exp = generate(out)
    print(f"Sample data written to {out} (as_of {exp['as_of']})")


if __name__ == "__main__":
    main()

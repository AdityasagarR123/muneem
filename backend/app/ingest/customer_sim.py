"""Synthetic shop customers for the sample dataset (CRM layer).

Realism notes:
* Only some bills carry a customer: credit (udhaar/khata) bills always do, UPI often
  (the VPA identifies the payer), cash rarely - so customer metrics have partial coverage.
* Visits are semi-regular: the chance a customer shows up rises with days since the last
  visit, like real habits, instead of pure randomness.
* Phones are masked placeholders ("+91-XXXXX-10023") so no real number is ever generated.

Planted CRM stories (recorded in expected.json):
* 14 regular customers stopped coming 26-42 days ago  -> churn risk
* 5 khata customers stopped repaying 50 days ago     -> overdue udhaar
* ~60 new customers arrived around Diwali             -> bigger recent cohort
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

FIRST = ["Ramesh", "Sunita", "Amit", "Priya", "Rakesh", "Anita", "Vijay", "Kavita", "Suresh", "Neha",
         "Manoj", "Pooja", "Arun", "Rekha", "Sanjay", "Meena", "Deepak", "Geeta", "Ravi", "Asha",
         "Ajay", "Seema", "Vinod", "Nisha", "Rajesh", "Usha", "Anil", "Kiran", "Mahesh", "Lata",
         "Naveen", "Shalini", "Pankaj", "Ritu", "Gopal", "Sarita", "Harish", "Jyoti", "Dinesh", "Mamta"]
LAST = ["Sharma", "Verma", "Gupta", "Singh", "Yadav", "Patel", "Agarwal", "Jain", "Mishra", "Kumar",
        "Chauhan", "Mehta", "Reddy", "Nair", "Iyer", "Das", "Bose", "Khan", "Joshi", "Pandey"]
AREAS = ["Gandhi Nagar", "Shastri Colony", "Station Road", "Old Market", "Civil Lines", "Nehru Park"]

PERSONAS = [  # name, count, visits/day
    ("regular", 70, 0.30), ("weekly", 130, 0.13), ("occasional", 160, 0.035), ("diwali_new", 60, 0.08),
]
LINK_PROB = {"credit": 1.0, "UPI": 0.70, "card": 0.50, "cash": 0.35}
N_CHURN, N_KHATA, N_OVERDUE = 14, 45, 5
OVERDUE_SINCE_DAYS = 50


class CustomerSim:
    def __init__(self, rng: np.random.Generator, dates: list[date], diwali):
        self.rng = rng
        self.end = dates[-1]
        self.customers: list[dict] = []
        i = 0
        for persona, n, rate in PERSONAS:
            for _ in range(n):
                if persona == "diwali_new":
                    span = (diwali.end - diwali.start).days + 6
                    join = diwali.start - timedelta(days=6) + timedelta(days=int(rng.integers(0, span)))
                elif rng.random() < 0.8:
                    join = dates[0]
                else:
                    join = dates[0] + timedelta(days=int(rng.integers(0, int(len(dates) * 0.75))))
                self.customers.append({
                    "id": f"C{i + 1:04d}", "persona": persona, "rate": rate, "join": join,
                    "name": f"{FIRST[int(rng.integers(len(FIRST)))]} {LAST[int(rng.integers(len(LAST)))]}",
                    "phone": f"+91-XXXXX-{10001 + i:05d}", "area": AREAS[int(rng.integers(len(AREAS)))],
                    "khata": False, "churn": None, "overdue": False, "last": None,
                })
                i += 1
        regular = [c for c in self.customers if c["persona"] == "regular" and c["join"] == dates[0]]
        for c in rng.choice(regular, N_CHURN, replace=False):
            c["churn"] = self.end - timedelta(days=int(rng.integers(26, 43)))
        pool = [c for c in self.customers if c["persona"] in ("regular", "weekly") and c["churn"] is None
                and c["join"] == dates[0]]
        khata = list(rng.choice(pool, N_KHATA, replace=False))
        for c in khata:
            c["khata"] = True
        for c in [k for k in khata if k["persona"] == "regular"][:N_OVERDUE]:
            c["overdue"] = True
        self.balance = {c["id"]: 0 for c in self.customers}
        self.repayments: list[list] = []
        self._today: set[str] = set()
        self.campaign: dict | None = None

    def plan_campaign(self, rng: np.random.Generator, sent: date, holdout_share: float = 0.25,
                      boost: float = 3.0, effect_days: int = 21) -> dict:
        """A past win-back message to occasional customers with a hold-out group that got
        nothing. Messaged customers come back more often for a few weeks (the planted effect),
        so the measurement has a real difference to find."""
        pool = [c for c in self.customers if c["persona"] == "occasional" and c["churn"] is None
                and c["join"] < sent and not c["khata"]]
        ids = [c["id"] for c in pool]
        hold = set(rng.choice(ids, int(round(len(ids) * holdout_share)), replace=False))
        for c in pool:
            c["campaign"] = "holdout" if c["id"] in hold else "target"
        self.campaign = {"sent": sent, "boost": boost, "effect_days": effect_days,
                         "members": [{"customer_id": c["id"], "group": c["campaign"]} for c in pool]}
        return self.campaign

    # -- per day ---------------------------------------------------------------
    def start_day(self) -> None:
        self._today = set()

    def assign(self, mode: str, d: date) -> str:
        if self.rng.random() >= LINK_PROB.get(mode, 0.3):
            return ""
        cands = [c for c in self.customers
                 if c["join"] <= d and (c["churn"] is None or d < c["churn"]) and c["id"] not in self._today
                 and (mode != "credit" or c["khata"])]
        if not cands:
            return ""
        w = np.array([c["rate"] * (1.0 if c["last"] is None else
                                   min(3.0, max(0.2, (d - c["last"]).days * c["rate"])))
                      * self._boost(c, d) for c in cands])
        c = cands[int(self.rng.choice(len(cands), p=w / w.sum()))]
        c["last"] = d
        self._today.add(c["id"])
        return c["id"]

    def _boost(self, c: dict, d: date) -> float:
        k = self.campaign
        if k and c.get("campaign") == "target" and k["sent"] < d <= k["sent"] + timedelta(days=k["effect_days"]):
            return k["boost"]
        return 1.0

    def add_debit(self, cid: str, amount_paise: int) -> None:
        self.balance[cid] += amount_paise

    def end_day(self, d: date) -> None:
        for c in self.customers:
            if not c["khata"] or self.balance[c["id"]] <= 0:
                continue
            if c["overdue"] and d > self.end - timedelta(days=OVERDUE_SINCE_DAYS):
                continue
            if self.rng.random() < 0.1:
                pay = int(round(self.balance[c["id"]] * self.rng.uniform(0.8, 1.0) / 1000)) * 1000
                pay = min(max(pay, 1000), self.balance[c["id"]])
                self.balance[c["id"]] -= pay
                mode = "UPI" if self.rng.random() < 0.6 else "cash"
                self.repayments.append([d.isoformat(), c["id"], f"{pay / 100:.2f}", mode])

    # -- output ----------------------------------------------------------------
    def customer_rows(self) -> list[list]:
        return [[c["id"], c["name"], c["phone"], c["area"], "yes" if c["khata"] else "no"]
                for c in self.customers]

    def expected(self) -> dict:
        return {
            "customers": len(self.customers),
            "churned_customers": sorted(c["id"] for c in self.customers if c["churn"] is not None),
            "overdue_customers": sorted(
                [{"customer_id": c["id"], "outstanding_paise": self.balance[c["id"]]}
                 for c in self.customers if c["overdue"]], key=lambda r: r["customer_id"]),
            "khata_customers": sum(c["khata"] for c in self.customers),
            "udhaar_outstanding_paise": sum(self.balance.values()),
            "diwali_new_customers": sorted(c["id"] for c in self.customers if c["persona"] == "diwali_new"),
        }

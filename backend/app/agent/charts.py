"""Charts the agent can ask for - drawn by code, never with numbers from the LLM.

Every data tool result in a chat turn is registered as a dataset (d1, d2, ...). The LLM may
call make_chart(dataset_id, kind, x, y) to pick a better chart for the question; this module
checks the request against the dataset (columns exist, values numeric, kind fits the shape)
and builds:
* an interactive spec rendered by the frontend (Recharts), and
* a PNG drawn by fixed Matplotlib templates (for WhatsApp / reports), served by
  GET /api/charts/{chart_id}.png.
"""

from __future__ import annotations

import io
import textwrap
import threading
from collections import OrderedDict
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.agent.envelope import Visual

KINDS = ("line", "area", "bar", "stacked_bar", "pie", "funnel", "waterfall", "cohort_heatmap", "scatter")
Kind = Literal[KINDS]  # type: ignore[valid-type]

GUIDE = ("trend over time -> line/area; ranking or comparison -> bar; parts of a whole (≤8 parts) "
         "-> pie; parts across groups -> stacked_bar; stages -> funnel; what drove a change -> "
         "waterfall; retention by cohort -> cohort_heatmap; relationship of two numbers -> scatter")


class ChartArgs(BaseModel):
    dataset_id: str = Field(..., description="dataset_id from an earlier tool result in this turn")
    kind: Kind = Field(..., description=GUIDE)
    x: str = Field(..., description="column for the x axis / labels / cohort rows")
    y: list[str] = Field(..., min_length=1, max_length=4,
                         description="numeric column(s); for cohort_heatmap: [column_key, value_key]; "
                                     "for scatter: [y_column]")
    color: Optional[str] = Field(None, description="scatter: column that colours the points")
    title: Optional[str] = Field(None, max_length=80)
    top_n: Optional[int] = Field(None, ge=2, le=50, description="keep the N largest rows")


class ChartError(ValueError):
    pass


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def columns(rows: list[dict]) -> dict[str, str]:
    """column -> 'number' | 'text' (columns that are numbers in every non-empty row)."""
    out: dict[str, str] = {}
    for r in rows[:200]:
        for k, v in r.items():
            if isinstance(v, (dict, list)) or k.endswith("_paise"):
                continue
            if v is None:
                out.setdefault(k, "number")
                continue
            kind = "number" if _is_num(v) else "text"
            out[k] = "text" if out.get(k) == "text" or kind == "text" else "number"
    return out


def unit_for(key: str) -> str:
    return "₹" if key.endswith("_rs") else "%" if key.endswith("_pct") else ""


def build_chart(dataset: dict, a: ChartArgs) -> Visual:
    rows: list[dict] = dataset.get("rows") or []
    if not rows:
        raise ChartError(f"Dataset {a.dataset_id} has no rows to chart.")
    cols = columns(rows)
    for c in [a.x, *a.y] + ([a.color] if a.color else []):
        if c not in cols:
            raise ChartError(f"Column '{c}' is not in dataset {a.dataset_id}. "
                             f"Available: {', '.join(cols)}")
    title = a.title or dataset.get("title", "")
    data = list(rows)

    if a.kind == "cohort_heatmap":
        if len(a.y) != 2:
            raise ChartError("cohort_heatmap needs y=[column_key, value_key], e.g. "
                             "['month_offset', 'retention_pct']")
        col_key, val_key = a.y
        if cols[val_key] != "number":
            raise ChartError(f"'{val_key}' must be numeric")
        return Visual(type="chart", title=title, spec={
            "kind": "cohort_heatmap", "row": a.x, "col": col_key, "value": val_key,
            "unit": unit_for(val_key),
            "data": [{a.x: r[a.x], col_key: r[col_key], val_key: r[val_key]} for r in data]})

    for c in a.y:
        if cols[c] != "number":
            raise ChartError(f"'{c}' is text; y needs numeric columns: "
                             f"{', '.join(k for k, t in cols.items() if t == 'number')}")
    if a.top_n:
        data = sorted(data, key=lambda r: -(r.get(a.y[0]) or 0))[:a.top_n]
    units = {unit_for(c) for c in a.y}
    if len(units) > 1:
        raise ChartError("Don't mix ₹, % and counts in one chart - pick columns with the same unit")
    unit = units.pop()

    if a.kind == "pie":
        if any((r.get(a.y[0]) or 0) < 0 for r in data):
            raise ChartError("A pie cannot show negative values; use bar or waterfall.")
        if len(data) > 8:  # keep the 7 biggest, add the rest up as "Other" (sum done here, in code)
            data = sorted(data, key=lambda r: -(r.get(a.y[0]) or 0))
            rest = data[7:]
            data = data[:7] + [{a.x: f"Other ({len(rest)})",
                                **{c: round(sum(r.get(c) or 0 for r in rest), 2) for c in a.y}}]
    if a.kind == "scatter":
        if cols[a.x] != "number":
            raise ChartError("scatter needs a numeric x column")
    if a.kind == "funnel" and any((data[i][a.y[0]] or 0) < (data[i + 1][a.y[0]] or 0)
                                  for i in range(len(data) - 1)):
        raise ChartError("Values are not decreasing stage by stage - a funnel would mislead; use bar.")

    keep = {a.x, *a.y} | ({a.color} if a.color else set())
    spec = {"kind": a.kind, "x": a.x, "unit": unit,
            "series": [{"key": c, "label": c.replace("_rs", " ₹").replace("_pct", " %").replace("_", " ")}
                       for c in a.y],
            "data": [{k: r.get(k) for k in keep} for r in data]}
    if a.color:
        spec["color"] = a.color
    if a.kind == "waterfall":
        h = dataset.get("headline", {})
        if "before_rs" in h and "now_rs" in h:
            spec["start"] = {"label": h.get("compare_label", "Before").capitalize(), "value": h["before_rs"]}
            spec["end"] = {"label": h.get("period_label", "Now").capitalize(), "value": h["now_rs"]}
    return Visual(type="chart", title=title, spec=spec)


# ------------------------------------------------------------------ PNG store + renderer
class ChartStore:
    def __init__(self, size: int = 300):
        self._d: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self.size = size

    def add(self, v: Visual) -> str:
        cid = uuid4().hex[:12]
        with self._lock:
            self._d[cid] = {"title": v.title, "spec": v.spec}
            while len(self._d) > self.size:
                self._d.popitem(last=False)
        return cid

    def get(self, cid: str) -> dict | None:
        with self._lock:
            return self._d.get(cid)


STORE = ChartStore()
PALETTE = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"]


def _fmt(v, unit: str) -> str:
    if v is None:
        return ""
    if unit == "₹":
        a = abs(v)
        s = f"₹{a / 1e5:.1f}L" if a >= 1e5 else f"₹{a / 1e3:.1f}k" if a >= 1e3 else f"₹{a:.0f}"
        return ("-" if v < 0 else "") + s
    if unit == "%":
        return f"{v:.1f}%"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"


def render_png(title: str, spec: dict) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    kind, data, unit = spec["kind"], spec.get("data", []), spec.get("unit", "")
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=130)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color="#e2e8f0", linewidth=0.8)
    ax.set_axisbelow(True)
    x = spec.get("x")
    labels = [str(r.get(x, "")) for r in data]
    short = [lab[5:] if len(lab) == 10 and lab[4] == "-" else lab[:16] for lab in labels]
    series = spec.get("series", [])

    if kind in ("line", "area"):
        for i, s in enumerate(series):
            ys = [r.get(s["key"]) or 0 for r in data]
            ax.plot(range(len(ys)), ys, color=PALETTE[i], linewidth=2, label=s["label"])
            if kind == "area":
                ax.fill_between(range(len(ys)), ys, color=PALETTE[i], alpha=0.15)
        step = max(1, len(short) // 10)
        ax.set_xticks(range(0, len(short), step), short[::step], rotation=0, fontsize=8)
    elif kind in ("bar", "stacked_bar"):
        n = len(series)
        width = 0.8 / (1 if kind == "stacked_bar" else n)
        bottom = [0.0] * len(data)
        for i, s in enumerate(series):
            ys = [r.get(s["key"]) or 0 for r in data]
            xs = [j + (0 if kind == "stacked_bar" else (i - (n - 1) / 2) * width) for j in range(len(ys))]
            ax.bar(xs, ys, width=width, bottom=bottom if kind == "stacked_bar" else None,
                   color=PALETTE[i], label=s["label"])
            if kind == "stacked_bar":
                bottom = [b + y for b, y in zip(bottom, ys)]
        ax.set_xticks(range(len(short)), short, rotation=30 if len(short) > 5 else 0, ha="right"
                      if len(short) > 5 else "center", fontsize=8)
    elif kind == "pie":
        ys = [r.get(series[0]["key"]) or 0 for r in data]
        ax.pie(ys, labels=short, colors=PALETTE[:len(ys)], autopct="%1.0f%%",
               wedgeprops={"width": 0.45, "edgecolor": "white"}, textprops={"fontsize": 8})
        ax.axis("equal")
        ax.grid(False)
    elif kind == "funnel":
        ys = [r.get(series[0]["key"]) or 0 for r in data]
        top = max(ys) or 1
        for i, (lab, y) in enumerate(zip(labels, ys)):
            ax.barh(len(ys) - 1 - i, y, left=(top - y) / 2, color=PALETTE[0], alpha=1 - i * 0.15, height=0.7)
            ax.text(top / 2, len(ys) - 1 - i, f"{lab}  ·  {_fmt(y, unit)}  ({100 * y / top:.0f}%)",
                    ha="center", va="center", color="white", fontsize=9, fontweight="bold")
        ax.set_yticks([])
        ax.set_xticks([])
        ax.grid(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_visible(False)
    elif kind == "waterfall":
        key = series[0]["key"]
        steps = [(r.get(x), r.get(key) or 0) for r in data]
        start, end = spec.get("start"), spec.get("end")
        cum = start["value"] if start else 0.0
        xs, i = [], 0
        if start:
            ax.bar(i, cum, color="#94a3b8")
            xs.append(start["label"])
            i += 1
        for lab, v in steps:
            # bar runs from the running total to running total + v (negative height draws down)
            ax.bar(i, v, bottom=cum, color="#16a34a" if v >= 0 else "#dc2626")
            ax.text(i, max(cum, cum + v), _fmt(v, unit), ha="center", va="bottom", fontsize=8)
            cum += v
            xs.append(textwrap.fill(str(lab), 16))
            i += 1
        if end:
            ax.bar(i, end["value"], color="#475569")
            xs.append(textwrap.fill(end["label"], 16))
        if start:
            xs[0] = textwrap.fill(xs[0], 16)
        ax.set_xticks(range(len(xs)), xs, fontsize=8)
        if start:
            lo = min(start["value"], end["value"] if end else cum, cum) * 0.85
            ax.set_ylim(bottom=lo)
    elif kind == "cohort_heatmap":
        rk, ck, vk = spec["row"], spec["col"], spec["value"]
        rows_ = sorted({str(r[rk]) for r in data})
        cols_ = sorted({r[ck] for r in data})
        grid = [[float("nan")] * len(cols_) for _ in rows_]
        for r in data:
            grid[rows_.index(str(r[rk]))][cols_.index(r[ck])] = r[vk] if r[vk] is not None else float("nan")
        im = ax.imshow(grid, cmap="Blues", aspect="auto", vmin=0)
        for yi, row in enumerate(grid):
            for xi, v in enumerate(row):
                if v == v:
                    ax.text(xi, yi, _fmt(v, unit), ha="center", va="center", fontsize=8,
                            color="white" if v > 60 else "#0f172a")
        ax.set_xticks(range(len(cols_)), [f"M{c}" for c in cols_], fontsize=8)
        ax.set_yticks(range(len(rows_)), rows_, fontsize=8)
        ax.grid(False)
        fig.colorbar(im, ax=ax, fraction=0.03)
    elif kind == "scatter":
        key = series[0]["key"]
        groups: dict[str, list] = {}
        for r in data:
            groups.setdefault(str(r.get(spec.get("color"), "")) if spec.get("color") else "", []).append(r)
        for i, (g, rs) in enumerate(groups.items()):
            ax.scatter([r.get(x) for r in rs], [r.get(key) for r in rs], s=18, alpha=0.75,
                       color=PALETTE[i % len(PALETTE)], label=g or None)
        ax.set_xlabel(x.replace("_", " "), fontsize=9)
        ax.set_ylabel(series[0]["label"], fontsize=9)
    if kind not in ("pie", "funnel", "cohort_heatmap", "scatter") and unit:
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _fmt(v, unit)))
    if len(series) > 1 or (kind == "scatter" and spec.get("color")):
        ax.legend(fontsize=8, frameon=False)
    ax.set_title(title, fontsize=11, loc="left", color="#0f172a")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()

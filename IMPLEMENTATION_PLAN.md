# VyaparPulse — Implementation Plan (with ArangoDB)

> **Progress (14 Nov 2026):** Phases 0–5 backend done (72 tests passing). Frontend for Phase 0
> deferred until the backend is complete. Design change: when ArangoDB is unreachable, graph
> questions are answered by an in-memory graph engine with the same results (instead of
> returning "graph unavailable"); every response names the engine that answered. Live ArangoDB
> tests run once cloud credentials are in `.env`.
>
> **Phase 7 done:** chat agent on Gemini (`gemini-3.5-flash-lite`, falls over to
> `gemini-3.1-flash-lite` on rate limits, then to rules-only mode). 19 typed tools, Hinglish /
> Hindi / English, number-grounding check on every answer, SSE streaming, eval set 18/18 live
> and 16/16 in rules mode.
>
> **Phase 9 done:** purchase orders per supplier (editable drafts), WhatsApp messages in
> English / Hinglish / Hindi, lifecycle draft → sent → received (receiving adds stock and every
> alert recomputes; partial deliveries supported), CSV export, morning brief, chat tools for
> both. Orders are saved in ArangoDB `purchase_orders` when connected. 101 tests passing.
> Next: Phase 10 hardening (response cache for demo questions, security pass), then frontend.

Step-by-step build plan for the project described in `VyaparPulse — Kirana Inventory & Purchase Intelligence Copilot.md`.
Each phase ends with a **Done when** check. Do not start the next phase until the current one passes.

---

## 0. Key decisions (read first)

| Decision | Choice |
| --- | --- |
| Database | **ArangoDB** (documents + graph) is the saved source of truth |
| How ArangoDB runs | **No Docker.** Either a native install on the laptop, or a managed ArangoDB cloud instance. The app only needs `ARANGO_URL` + credentials in `.env`, so switching between them needs no code change (see Phase 0) |
| Calculations | **Python only** (pandas + `Decimal`/paise). ArangoDB stores and connects data; it never does GST/margin/reorder math |
| How analytics read data | On startup / after upload, load collections from ArangoDB into pandas DataFrames (in-memory cache). Analytics run on DataFrames |
| Graph questions | Fixed, parameterised **AQL** queries written by us (supplier risk, bought-together, supply path, substitutes) |
| LLM | Any provider behind one wrapper `llm.chat(messages, tools)`; chosen via `.env` |
| LLM safety | LLM only picks a tool + arguments. **It never writes AQL, SQL, or Python** |
| Fallback | If ArangoDB is down, load the same CSVs straight into pandas (`CsvStore`). Graph tools return "graph unavailable" |
| Frontend | React (Vite) + Tailwind + Recharts + Streamdown (Mermaid) |
| Backend | FastAPI + Pydantic + pandas + `python-arango` |

### Overall architecture

```
CSV/XLSX ─► Validate ─► ArangoDB (documents + edges) ─► load ─► pandas DataFrames ─► Analytics (pure Python)
                              │                                                          │
                              └──── fixed AQL graph queries ─────────────┐               │
                                                                         ▼               ▼
User (Hi/En/Hinglish) ─► /api/chat (SSE) ─► Agent: LLM picks tool + args ─► Tool runs ─► Envelope
                                                                                          ▼
                    React UI: Dashboard · Chat (markdown + Mermaid + charts) · Alerts · Restock
```

---

## 1. Folder structure (target)

```
muneem/
├── .env.example                # ARANGO_URL, ARANGO_DB, ARANGO_USER, ARANGO_PASSWORD, LLM_PROVIDER, LLM_API_KEY, LLM_MODEL
├── dev.ps1 / dev.sh            # one-command start (backend + frontend; ArangoDB runs separately)
├── README.md                   # setup + attribution of repos we learned from
├── data/
│   └── sample/                 # generated CSVs (items, sales, inventory, expenses, suppliers)
├── backend/
│   ├── pyproject.toml / requirements.txt  (pinned versions)
│   ├── app/
│   │   ├── main.py             # FastAPI app, routers, CORS
│   │   ├── config.py           # settings from env
│   │   ├── db/
│   │   │   ├── arango.py       # connection, create db/collections/graph/indexes
│   │   │   ├── schema.py       # collection + edge definitions
│   │   │   ├── loader.py       # CSV rows -> documents + edges
│   │   │   ├── store.py        # DataStore interface: ArangoStore + CsvStore (fallback)
│   │   │   └── aql.py          # ALL fixed AQL queries live here (nowhere else)
│   │   ├── ingest/
│   │   │   ├── validate.py     # column/type/duplicate/negative/GST checks -> report
│   │   │   └── generator.py    # seeded synthetic data generator
│   │   ├── analytics/
│   │   │   ├── money.py        # paise/Decimal helpers, GST back-out
│   │   │   ├── kpis.py
│   │   │   ├── stock.py        # demand, safety stock, reorder, tiers, dead stock
│   │   │   ├── margin.py
│   │   │   ├── payments.py
│   │   │   └── trends.py
│   │   ├── graph/
│   │   │   └── insights.py     # supplier_risk, bought_together, supply_path, substitutes (calls aql.py)
│   │   ├── agent/
│   │   │   ├── llm.py          # provider-agnostic wrapper
│   │   │   ├── tools.py        # tool registry + Pydantic arg schemas
│   │   │   ├── loop.py         # tool-calling loop
│   │   │   ├── rules_mode.py   # keyword -> tool fallback (no LLM)
│   │   │   ├── mermaid.py      # Mermaid templates filled with computed values
│   │   │   └── envelope.py     # response envelope model
│   │   ├── actions/
│   │   │   └── restock.py      # order list, supplier WhatsApp draft, CSV export
│   │   └── api/                # routers: upload, sample, kpis, alerts, chat, restock, graph
│   └── tests/
│       ├── test_money.py
│       ├── test_stock.py       # golden reorder case
│       ├── test_validate.py
│       ├── test_aql.py         # graph queries on sample data
│       ├── test_scenarios.py   # planted stories found with exact ₹
│       └── eval_questions.yaml # 15 questions + expected tool + expected number
└── frontend/
    ├── package.json (pinned)
    └── src/
        ├── api/                # fetch + SSE client
        ├── pages/Dashboard.tsx
        ├── pages/Chat.tsx
        ├── components/         # KpiCard, ChartView, MermaidView, HtmlCard, EvidencePanel, RestockPanel, UploadBox, AlertTable
        └── types/envelope.ts
```

---

## Phase 0 — Setup (foundation)

**Goal:** everyone can run an empty app connected to ArangoDB, without Docker.

Steps:
1. Create the folder structure above. Add `.gitignore` (`.env`, `node_modules`, `__pycache__`, `.venv`).
2. Get an ArangoDB server running (pick **one**, the whole team uses the same choice):
   - **Option A — Managed cloud (easiest on Windows):** create a free/trial instance on ArangoDB's managed cloud service. Copy its URL, username and password into `.env`. Nothing to install. Downside: needs internet during the demo (the `CsvStore` fallback covers Wi-Fi failure).
   - **Option B — Native local install:** download the official ArangoDB installer/package for your OS from arangodb.com and run it as a normal service on port `8529`. Note: check which recent versions still ship a native Windows build; if yours doesn't, install the Linux package inside WSL2 (Ubuntu) — it is still a normal install, not Docker.
   - Either way: create a database `vyaparpulse` and a non-root user for the app; set a strong password; write the exact version you installed into the README.
3. Backend: create venv, install `fastapi`, `uvicorn`, `pydantic`, `pandas`, `openpyxl`, `python-arango`, `python-multipart`, `sse-starlette`, `pytest`. **Pin versions.**
4. `GET /api/health` returns `{ "api": "ok", "arango": "ok|down" }`.
5. Frontend: `npm create vite@latest` (React + TS), add Tailwind, Recharts, Streamdown. Show a page that calls `/api/health`.
6. `.env.example` with all keys (no real secrets).
7. One-command start script (`dev.ps1` for Windows, `dev.sh` for Linux/macOS): starts backend + frontend. ArangoDB is already running (as a service or in the cloud), so the script only checks it is reachable and prints a clear message if not.

**Done when:** `./dev.ps1` → browser shows "API ok, Arango ok". The ArangoDB web UI opens (at `http://localhost:8529` for a local install, or the cloud console URL).

---

## Phase 1 — Synthetic sample data

**Goal:** realistic Indian kirana data with planted stories, so the demo always has something to find.

Steps:
1. Write `ingest/generator.py` with a fixed random seed.
2. Generate:
   - `suppliers.csv` — 6–8 suppliers (e.g. "Sharma Traders", "Amul Distributor"), `whatsapp_number_placeholder`.
   - `items.csv` — 60–80 SKUs: staples (atta, rice, oil, dal), dairy, snacks, beverages, household, festive (sweets, dry fruits). GST 0/5/12/18, MRP incl. GST, cost ex GST, pack size, supplier, lead time.
   - `sales.csv` — ~120 days, multiple lines per invoice, UPI/cash/credit (UPI share rising over time).
   - `inventory.csv` — current stock per item.
   - `expenses.csv` — rent, electricity, salary, supplier, other.
3. Add a festive fortnight window with category uplift (verify the Diwali dates you pick).
4. Plant the 5 stories:
   1. 2 fast items that will run out inside lead time.
   2. 3 dead-stock items (no sales in 30 days, real ₹ locked).
   3. 1 high-revenue low-margin staple.
   4. 1 expense category that jumps in the latest month.
   5. 1 festive category that spikes then normalises.
5. Also plant a **graph story**: one supplier supplies most of the at-risk items (for the "call this supplier first" demo), and 2 items frequently bought together (e.g. atta + oil).
6. Write the expected ₹ values of each planted story to `data/sample/expected.json` (used by tests later).

**Done when:** running the generator twice produces identical CSVs; `expected.json` exists.

---

## Phase 2 — ArangoDB schema + loader

**Goal:** CSV data lives in ArangoDB as documents and a graph.

### Collections

| Name | Type | `_key` | Main fields |
| --- | --- | --- | --- |
| `items` | document | `item_id` | name, category, hsn, mrp_incl_gst_paise, cost_ex_gst_paise, gst_rate, pack_size, lead_time_days |
| `suppliers` | document | `supplier_id` | name, whatsapp_number_placeholder |
| `categories` | document | category slug | name |
| `invoices` | document | `invoice_id` | date, payment_mode, total_paise |
| `sales` | document | auto | invoice_id, date, item_id, qty, unit_price_incl_gst_paise, payment_mode |
| `inventory` | document | `item_id` | stock_qty, as_of_date |
| `expenses` | document | auto | date, category, amount_paise, payment_mode |
| `uploads` | document | auto | filename, time, validation report |
| `chat_history` | document | auto | question, envelope, time |
| `supplies` | **edge** | auto | `suppliers/* → items/*` (lead_time_days) |
| `belongs_to` | **edge** | auto | `items/* → categories/*` |
| `contains` | **edge** | auto | `invoices/* → items/*` (qty, unit_price_incl_gst_paise) |

**Named graph:** `kirana_graph` with edge definitions `supplies`, `belongs_to`, `contains`.

**Indexes:** persistent index on `sales.date`, `sales.item_id`, `expenses.date`, `invoices.date`.

> Money is stored as **integer paise** everywhere in the DB.

Steps:
1. `db/arango.py`: connect; create database, collections, edges, named graph and indexes if missing (idempotent — safe to run many times).
2. `db/loader.py`: take validated DataFrames → bulk insert documents (`import_bulk`) → create edges. Clear old data first (one shop at a time for the hackathon).
3. `db/store.py`: a `DataStore` interface with `get_frames() -> dict[str, DataFrame]`.
   - `ArangoStore`: reads collections into DataFrames.
   - `CsvStore`: reads the CSVs directly (fallback when Arango is down).
   - Cache the frames in memory; refresh after each upload.
4. `POST /api/sample` → generate/load sample CSVs → validate → load into ArangoDB → refresh cache.

**Done when:** after `POST /api/sample`, the ArangoDB web UI shows all collections filled and `kirana_graph` draws supplier → item → category links. Stopping the ArangoDB service (or pointing `ARANGO_URL` at a wrong address) and restarting the API still serves data through `CsvStore`.

---

## Phase 3 — Upload + validation

**Goal:** the owner can upload their own CSV/XLSX and get a clear, row-level report.

Steps:
1. `POST /api/upload` accepts files per table (`items`, `sales`, `inventory`, `expenses`, optional `suppliers`).
2. Limits: max file size (e.g. 5 MB), max rows (e.g. 200k), extension + MIME check.
3. `ingest/validate.py` checks:
   - required columns present, correct types, parseable dates
   - duplicate keys (item_id, invoice line)
   - negative stock / qty / price
   - GST rate in {0, 5, 12, 18} (warn on 28 / others)
   - sales referencing unknown item_id
   - cleaning: strip control characters, cap text length (prompt-injection safety)
4. Report format: `{ ok, errors: [{table, row, column, message}], warnings: [...], summary }`.
5. If `ok` → load into ArangoDB (Phase 2 loader) and save the report in `uploads`.
6. Frontend `UploadBox`: drag-drop, show the report, plus a **"Load sample data"** button.

**Done when:** a correct file loads; a broken file (missing column, negative stock, GST 7%) shows readable errors with row numbers and nothing is loaded.

---

## Phase 4 — Analytics engine (pure Python) + tests

**Goal:** every number in the product comes from these tested functions.

Steps (each is a pure function: DataFrames in → result + `formula` string + evidence rows out):
1. `money.py`
   - `taxable = price_incl / (1 + rate)`, `gst = price_incl − taxable` (Decimal, round only for display)
2. `margin.py`
   - `unit_margin = taxable_selling − cost_ex_gst`, `margin_% = unit_margin / taxable_selling`
   - flag **high revenue, low profit**: revenue top quartile AND margin % bottom quartile
3. `stock.py`
   - `d = units sold last 14 days / 14`; `σ = std of daily units, last 28 days`
   - `d_adj = d × festive_multiplier(category, window)` (default 1.0)
   - `safety_stock = 1.65 × σ × √L`
   - `reorder_point = d_adj × L + safety_stock`
   - `order_qty = ceil(max(0, d_adj × (L + R) + safety_stock − stock))` → round up to `pack_size` (R = 2)
   - `days_left = stock / d_adj`; tiers: `< L` Critical, `< L + R` Soon, else OK
   - dead stock: stock > 0 and no sales in 30 days; `money_trapped = stock × cost_ex_gst`
   - slow mover: velocity < 20% of category median
   - **< 14 days history → `insufficient_data`** (never guess)
4. `kpis.py`: revenue, taxable revenue, GST collected, gross margin, stock value.
5. `payments.py`: UPI/cash/credit share by value, weekly.
6. `trends.py`: daily/weekly series for an item or category.
7. Tests:
   - GST back-out: ₹105 @ 5% → taxable ₹100, GST ₹5; cost ₹88 → margin ₹12 (12%).
   - **Golden reorder:** d=6, L=2, R=2, σ=2, stock=12 → safety stock 4.67, raw qty 17, pack 6 → **18**.
   - dead stock, sufficiency rule.
   - `test_scenarios.py`: every planted story from `expected.json` is found with exact ₹.

**Done when:** `pytest` is green, including golden and scenario tests.

---

## Phase 5 — Graph insights (ArangoDB AQL)

**Goal:** the features only a graph makes easy. All queries live in `db/aql.py`, take **bind parameters only** (`@item_id`, `@supplier_id`, `@n`), and are never built from LLM text.

### Queries

**1. Items of a supplier (for supplier risk)**
```aql
FOR item IN 1..1 OUTBOUND CONCAT('suppliers/', @supplier_id) supplies
  LET inv = DOCUMENT('inventory', item._key)
  RETURN { item_id: item._key, name: item.name, stock: inv.stock_qty, lead_time: item.lead_time_days }
```
Python then joins with `stock.py` results → "if this supplier is late, these N items go critical".

**2. Bought together (basket analysis)**
```aql
FOR inv IN 1..1 INBOUND CONCAT('items/', @item_id) contains
  FOR other IN 1..1 OUTBOUND inv contains
    FILTER other._key != @item_id
    COLLECT id = other._key, name = other.name WITH COUNT INTO together
    SORT together DESC
    LIMIT @n
    RETURN { item_id: id, name: name, bills_together: together }
```

**3. Supply path of an item (for Mermaid diagram)**
```aql
LET item = DOCUMENT('items', @item_id)
LET supplier = FIRST(FOR s IN 1..1 INBOUND item supplies RETURN s)
LET category = FIRST(FOR c IN 1..1 OUTBOUND item belongs_to RETURN c)
RETURN { item: item, supplier: supplier, category: category, stock: DOCUMENT('inventory', item._key).stock_qty }
```

**4. Substitutes (same category, similar price)**
```aql
LET item = DOCUMENT('items', @item_id)
FOR cat IN 1..1 OUTBOUND item belongs_to
  FOR other IN 1..1 INBOUND cat belongs_to
    FILTER other._key != item._key
    FILTER ABS(other.mrp_incl_gst_paise - item.mrp_incl_gst_paise) <= item.mrp_incl_gst_paise * 0.25
    LIMIT @n
    RETURN { item_id: other._key, name: other.name, mrp_paise: other.mrp_incl_gst_paise }
```

**5. Supplier call priority** — Python: run `stock.py` tiers → group Critical/Soon items by supplier (via query 1 or the `supplies` edges) → sort by ₹ of items at risk.

Steps:
1. Write the queries in `db/aql.py`.
2. `graph/insights.py`: `supplier_risk`, `bought_together`, `supply_path`, `substitutes`, `supplier_priority` — each returns result + formula/explanation + evidence rows.
3. If Arango is down → return `{ confidence: "graph_unavailable" }` (no crash).
4. `test_aql.py`: on sample data, the planted graph story is found (right supplier first; atta + oil together).

**Done when:** graph tests are green and `GET /api/graph/supplier-priority` returns the planted supplier on top.

---

## Phase 6 — Dashboard (API + UI)

**Goal:** one screen with all key numbers in ₹.

Steps:
1. `GET /api/kpis?period=30d` → revenue, margin after GST, GST collected, UPI vs cash, stock value.
2. `GET /api/alerts` → stock tiers (Critical/Soon), dead stock (₹ locked), low-margin flags, supplier priority.
3. UI `Dashboard.tsx`:
   - KPI cards (₹ formatted Indian style: ₹1,23,456)
   - charts: daily revenue, payment mix, top items, category margin
   - alert table with tier colours
   - "Dead stock: ₹X locked in N items" card
   - "Call first: Supplier Y (N items at risk)" card
4. Each card has a "show calculation" toggle showing the formula.

**Done when:** loading sample data shows all cards and charts with numbers matching `expected.json`.

---

## Phase 7 — Agent (chat brain)

**Goal:** questions in Hindi/English/Hinglish → right tool → exact numbers → short answer.

### Tools (Pydantic-validated arguments)

| Tool | Source |
| --- | --- |
| `get_kpis(period)` | analytics |
| `top_items(by, n, period)` | analytics |
| `sales_trend(item?, category?, grain, period)` | analytics |
| `stock_status(item?)` | analytics |
| `reorder_plan(horizon_days, budget?)` | analytics |
| `dead_stock(days)` | analytics |
| `margin_report(group_by)` | analytics |
| `payment_mix(period)` | analytics |
| `supplier_risk(supplier)` | **graph** |
| `supplier_priority()` | **graph** + analytics |
| `bought_together(item, n)` | **graph** |
| `substitutes(item, n)` | **graph** |
| `explain_flow(topic, item?)` | **graph** → Mermaid template |

Steps:
1. `agent/llm.py`: one wrapper, provider/model from `.env`, timeout + retry.
2. `agent/tools.py`: registry `{name: (ArgsModel, function, description)}`. Item/supplier names from the user are matched to IDs with a fuzzy lookup in Python (not by the LLM).
3. `agent/loop.py`:
   1. user message → LLM with tool list
   2. LLM returns tool + JSON args → **Pydantic validates** (bad args → ask user)
   3. Python runs the tool → result rows + formula
   4. result goes back to LLM → it writes a short answer **in the user's language**, using only numbers from the result
   5. no tool fits → say "I can't answer that from your data" (no guessing)
4. System prompt rules: never calculate; only use tool numbers; reply in user's language. **Never put raw CSV cells into the system prompt.**
5. `agent/envelope.py`:
   ```json
   { "answer_md": "...", "evidence": [{ "tool": "...", "args": {}, "rows": [], "formula": "..." }],
     "visuals": [{ "type": "chart|mermaid|html", "spec": {} }],
     "actions": [{ "type": "restock_draft", "payload": {} }],
     "confidence": "ok|insufficient_data|graph_unavailable" }
   ```
6. `agent/rules_mode.py`: keyword → tool mapping (e.g. "order", "kal", "restock" → `reorder_plan`) used when the LLM is offline.
7. `POST /api/chat` with SSE: stream answer tokens, then send the final envelope. Save to `chat_history` in ArangoDB.
8. `agent/mermaid.py`: templates, e.g.
   ```
   flowchart LR
     S[Sharma Traders] -->|lead 2 days| I[Atta 10kg]
     I --> C[Staples]
     I --> K[Stock: 12 · 2 days left · Critical]
   ```
   filled from `supply_path` results.

**Done when:** "Kal kya order karna chahiye?", "Which items haven't sold in 30 days?", "Sharma Traders late ho gaya to kya hoga?", "Atta ke saath kya bikta hai?" each pick the right tool and give correct numbers. With the LLM key removed, rules mode still answers them.

---

## Phase 8 — Chat UI with inline visuals

**Goal:** answers look good and prove themselves.

Steps:
1. `Chat.tsx`: message list, input box, SSE streaming.
2. Render `answer_md` with Streamdown (handles half-finished markdown while streaming).
3. `EvidencePanel`: collapsible "Show calculation" → tool name, args, formula, evidence table.
4. `ChartView`: renders `{type:"chart"}` specs with Recharts (line/bar/pie). No LLM code.
5. `MermaidView`: renders `{type:"mermaid"}`; on parse failure show the source text + a native chart instead. Test that Streamdown's Mermaid plugin renders automatically in our UI.
6. `HtmlCard`: `<iframe sandbox srcdoc=...>` **without** `allow-same-origin`.
7. Suggested-question chips (the scripted demo questions).
8. Escape all data text on render.

**Done when:** "Atta ki sales trend dikhao" draws a chart inline; "Supply flow dikhao" draws a Mermaid diagram from graph data; "show calculation" works.

---

## Phase 9 — Restock action

**Goal:** the money-saving action the owner comes back for.

Steps:
1. `actions/restock.py`:
   - order list from `reorder_plan` grouped **by supplier** (uses `supplies` edges)
   - optional budget limit (Critical first, then Soon)
   - Hindi/English WhatsApp-style draft per supplier, e.g.
     > Namaste Sharma ji, kal ke liye order: Atta 10kg × 18, Toor Dal 1kg × 12. Total approx ₹X. Dhanyavaad.
2. `POST /api/restock/draft` → `{ suppliers: [{ name, items, total_paise, message }] }`.
3. `GET /api/restock/export.csv` → downloadable order list.
4. UI `RestockPanel`: table + "Copy message" + "Open in WhatsApp" (`https://wa.me/?text=...` with the message only, **no real phone numbers**) + "Download CSV".
5. Chat envelopes with `actions: restock_draft` show a "Make restock list" button.
6. The app **never sends or orders by itself**.

**Done when:** one click → per-supplier messages + CSV download with the same quantities as the reorder tool.

---

## Phase 10 — Hardening, safety, evaluation

Steps:
1. `eval_questions.yaml`: 15 questions (English, Hindi, Hinglish) with expected tool + expected number. Include at least 3 graph questions. Script `python -m app.eval` prints pass/fail.
2. Cache LLM responses for the scripted demo questions (local file); use when the API is slow.
3. Security checklist:
   - no LLM-written code / AQL / SQL is executed
   - AQL only from `db/aql.py`, bind parameters only
   - CSV text cleaned, length-capped, escaped, kept out of system prompts
   - upload size/row/type limits
   - sandboxed iframe, strict CSP
   - secrets only in `.env`; local ArangoDB bound to localhost only (cloud instance: IP allowlist if offered); non-root DB user for the app
4. Failure drills:
   - kill LLM → rules mode works
   - stop ArangoDB (or disconnect from the cloud instance) → CsvStore serves dashboard/analytics; graph tools say "unavailable"
   - upload broken CSV → readable error
5. Speed: scripted questions answer in a few seconds.

**Done when:** eval ≥ 14/15, all drills pass.

---

## Phase 11 — Demo prep

Steps:
1. Sample data pre-loaded on start.
2. Rehearse the 4-minute story (Section 6 of the PS doc), adding a graph moment:
   - "Kal kya order karna chahiye?" → list → show calculation
   - "Kis supplier ko pehle call karu?" → graph answer + Mermaid supply flow
   - Restock → WhatsApp drafts → CSV
3. Record a 2-minute backup video.
4. Slides: architecture (show ArangoDB graph), "AI never does maths", commercial slide, "sample data is synthetic".
5. README: setup steps, attribution list of repos we learned from, ArangoDB licence note (check the current Community edition licence terms).
6. Pin all versions; commit lockfiles.

**Done when:** two full rehearsals pass without touching code.

---

## Priority reminder

- **P0 (must):** Phases 0–9 core paths + Phase 10 security + eval.
- **P1 (after P0 is stable):** expense spike flag, festive tuning, matplotlib PNG for WhatsApp, saved question history UI, supplier comparison.
- **P2 (talk only):** voice, scheduled alerts, billing-app integrations, multi-store, vector/GraphRAG search in ArangoDB.

## Suggested team split (4 people)

| Person | Phases |
| --- | --- |
| A — Data/DB | 1, 2, 3, 5 (generator, ArangoDB schema, loader, AQL) |
| B — Analytics | 4, 9 backend (formulas, tests, restock) |
| C — Agent | 7, 10 (LLM loop, tools, rules mode, eval) |
| D — Frontend | 0 frontend, 6, 8, 9 UI |

Agree on the **envelope format** and **tool list** on day one so people can work in parallel.

## Open items before coding

1. Confirm with organisers that existing libraries / reused code are allowed.
2. Pick the LLM provider and check rate limits.
3. Choose cloud vs native ArangoDB install (Phase 0), note the exact version, and check its licence terms.
4. Freeze the P0 list and the 15 eval questions.

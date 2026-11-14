# Muneem v2 — AI-first Super CRM for Merchants

Assessment, target architecture and phased build plan. Written 14 Nov 2026 against the code at
commit `7a6ae70`. Nothing in this document is implemented yet unless marked **(exists)**.

---

## 0. Decisions (confirmed 14 Nov 2026)

| Question | Decision |
| --- | --- |
| Database | **PostgreSQL now as system of record + ArangoDB for the graph** (moves storage from Phase 8 into Phase 1) |
| Business focus | **Kirana-first demo, general merchant data model** |
| Web research | **Tavily + TinyFish keys available** — owner adds `TAVILY_API_KEY` and `TINYFISH_API_KEY` to `.env` |
| Start | **Phases 1–3** |

Storage plan under this decision: SQLAlchemy repository with PostgreSQL in production
(`DATABASE_URL` in `.env`, e.g. Neon/Supabase); tests run against an in-memory SQLite copy of
the same schema so they stay fast and offline. ArangoDB keeps the graph projection.

## Progress

**Phase 1 done (14 Nov 2026):**
- Customers on bills (59% of bills linked; all credit bills), udhaar ledger with FIFO ageing,
  identity resolution (merge by phone, phone-only bills, unknown customers created),
  customer and repayment uploads.
- PostgreSQL store via SQLAlchemy (switches on with `DATABASE_URL`; SQLite in tests);
  ArangoDB gets customers, repayments and `purchased` edges.
- Metrics layer: 15 metrics × 11 dimensions × periods (incl. month-to-date vs same days last
  month).
- CRM analytics: RFM segments, churn risk, udhaar overdue, cohorts, lifecycle funnel, customer
  360.
- 7 new agent tools + rules-mode intents; `/api/metrics/*` and `/api/customers/*` endpoints.
- 121 tests pass; live Gemini eval 26/27 (the miss was fixed and rechecked).

Planted CRM stories found: 14/14 churned regulars, all 5 overdue khata accounts with exact
balances, total udhaar ₹49,475.50 to the paisa.

## 1. Verdict in one paragraph

The current Muneem already has the hardest part of the vision right: **a deterministic engine
that does all the maths, an LLM that only picks tools and writes words, and a check that every
number in an answer exists in the computed data.** What it lacks is the *customer* half of a
CRM (there is no customer entity at all), a general metrics layer the AI can query flexibly, an
insight engine that turns detections into "what / why / what to do", visualisation choice by
question type, ingestion that understands unknown files, web research, and a chat-first UI. All
of these can be added on top of the existing architecture without rewriting it.

---

## 2. What already works — keep it

| Area | What exists today | Why keep it |
| --- | --- | --- |
| Deterministic engine | GST back-out, margins, KPIs, festival-aware reorder maths, dead stock, expenses — pure Python, money in paise **(exists)** | Matches "AI never invents metrics" exactly |
| Tool-calling agent | 21 typed tools, Pydantic-validated args, Gemini with model fallback, rules-only mode **(exists)** | Same pattern scales to CRM tools |
| Grounding check | Every number in an answer is matched against tool results; ungrounded ones flagged **(exists)** | The core trust mechanism; extend to external sources |
| Evidence envelope | answer + evidence (tool, args, formula, rows) + visuals + actions **(exists)** | Already the right response contract |
| Validation | Row-level report, alias headers, Hinglish payment words, strict/partial **(exists)** | Becomes the last stage of the smarter ingestion pipeline |
| Graph | ArangoDB `kirana_graph` + identical in-memory engine; supplier risk, bought-together with significance **(exists)** | Relationship questions (customer ↔ product ↔ supplier) |
| Actions | Purchase-order drafts, WhatsApp messages in 3 languages, sent → received lifecycle that updates stock **(exists)** | Template for campaigns and reminders |
| Eval + tests | 101 tests, 20-question eval live on Gemini **(exists)** | Keeps quality measurable while we expand |

## 3. Gaps against the vision

| Vision requirement | Today | Needed |
| --- | --- | --- |
| Customers, value, churn, segments | No customer entity; bills are anonymous | Customer model, identity on bills (phone / UPI / khata), RFM, churn risk, cohorts, lifetime value |
| "Compare this month with last month", "why did revenue drop" | Fixed reports only | A **metrics layer** (named metrics × dimensions × period × comparison) and a **change-explanation** tool |
| Insights = what / why / do | Alerts exist, but not one consistent shape | **Insight engine**: detectors → Insight objects with impact in ₹ and a one-click action |
| Visualisation chosen per question | Each tool emits a fixed chart | A **visualisation tool** the agent calls with a chart type over a dataset reference; interactive + Matplotlib PNG; funnel, cohort heatmap, waterfall, segment scatter |
| Smart ingestion from many sources | CSV/XLSX with known table names | Profiler, entity classifier, field mapper with confidence, relationship + duplicate detection, saved source profiles; SQL and webhook connectors |
| External intelligence | None | Tavily search + TinyFish extraction, clearly separated, with sources and timestamps |
| Campaigns / outreach | Only supplier orders | Segment → message → send links / export → measured result with a hold-out group |
| Chat-first premium UI | Card dashboard + separate chat page | Orbit-style home: briefing + composer + recent chats; insights feed; customer 360 |
| Database | In-memory pandas + optional ArangoDB | A system of record (see §10) behind a repository interface |

---

## 4. Target architecture

```
 Sources                     Ingestion (deterministic first, LLM only proposes)
 ───────                     ─────────────────────────────────────────────────
 CSV / XLSX ──┐   ┌───────────────────────────────────────────────────────────┐
 SQL (read) ──┼──►│ 1 Land raw   2 Profile columns   3 Classify table→entity   │
 Webhook/API ─┤   │ 4 Map fields (rules + LLM proposal + confidence + confirm) │
 Platforms ───┘   │ 5 Resolve entities (phone/email/name)  6 Detect relations  │
 (later)          │ 7 Validate + quality report   8 Save source profile        │
                  └──────────────────────────┬────────────────────────────────┘
                                             ▼
                          Canonical business model (system of record)
              customers · products · orders · order_lines · payments · ledger (udhaar)
              inventory · suppliers · purchase_orders · expenses · campaigns · events
                                             │
            ┌────────────────────────────────┼─────────────────────────────────┐
            ▼                                ▼                                 ▼
   Metrics layer (semantic)        Insight engine (scheduled)        Graph projection
   named metrics × dimensions      detectors → What/Why/Do/Impact     customer–product–
   × period × compare — pure       + action + evidence                supplier (ArangoDB)
   SQL/pandas, versioned
            └────────────────────────────────┬─────────────────────────────────┘
                                             ▼
                               Agent (LLM = router + writer)
      data tools · insight tools · visualisation tools · web research · action drafts
                  │ grounding check (internal numbers) + citation check (web)
                  ▼
      Envelope: answer · evidence · visuals · actions · sources(web) · warnings
                  ▼
      UI: Home (briefing + chat) · Insights · Customers · Products & stock ·
          Orders & restock · Campaigns · Data sources
```

Principles carried over: the LLM never computes a number, never writes SQL that runs, never
sends a message. Everything numeric comes from the metrics layer or detectors and is traceable.

## 5. Canonical data model

| Entity | Key fields | Kirana reality we must handle |
| --- | --- | --- |
| customers | customer_id, name, phone (E.164), upi_vpa, segment, first_seen, consent_whatsapp | Only part of bills carry a customer (khata, UPI, loyalty phone). Every customer metric states its **coverage** ("based on 58% of bills linked to a customer") |
| orders / order_lines | order_id, customer_id (nullable), channel (counter/WhatsApp/online), status, lines | Existing `sales` becomes order_lines; `invoice_id` becomes order_id |
| payments | order_id, mode, amount, received_at | UPI / cash / card / credit |
| ledger (udhaar) | customer_id, date, debit (credit sale), credit (repayment) | Outstanding and overdue amounts per customer |
| products, inventory, suppliers, purchase_orders, expenses | as today **(exists)** | — |
| campaigns | segment rule snapshot, message, channel, members, hold-out, status, outcome | For measuring whether outreach worked |
| events | type, entity, at, payload | Webhook / platform events; enables funnels where they exist |

## 6. Intelligent ingestion pipeline

1. **Land raw** — keep the original file/rows untouched, with source and timestamp.
2. **Profile** each column: inferred type (integer, decimal, money, date, phone, email, GSTIN,
   UPI VPA, category, identifier, free text), null %, distinct %, examples, numeric outliers
   (median-absolute-deviation).
3. **Classify the table** to an entity by column-signature scoring (e.g. date + qty + item +
   bill number → order_lines).
4. **Map fields** — deterministic first (header synonyms **(exists)** + value patterns), then the
   LLM proposes mappings only for columns still unknown. Every mapping carries a confidence;
   anything below the threshold is shown to the owner to confirm ("'Mob No' looks like customer
   phone — 92%"). The LLM never transforms data.
5. **Resolve entities** — normalise phones (+91), emails, UPI ids; merge duplicate customers;
   fuzzy-match product names (with a review list, never silent merges).
6. **Detect relationships** — inclusion-dependency test: if ≥95% of values in column A appear in
   the key column of table B, propose A → B.
7. **Validate** — existing row-level validator **(exists)**, extended with anomaly checks
   (price far from MRP, qty spikes, date gaps, negative margins).
8. **Save the source profile** — the next upload from the same billing app maps automatically.

Connectors: file upload **(exists)**; SQL read-only (connection string, choose tables, same
pipeline); webhook `POST /api/ingest/{source}/{entity}` with an HMAC secret; platform adapters
(Shopify, Razorpay, Petpooja-style POS exports) later, each just a mapper into the same pipeline.

## 7. Metrics layer (one source of truth)

A registry of named, tested metric definitions used by the dashboard, the insight engine and
the agent alike: revenue, taxable revenue, GST, gross margin, orders, units, AOV, active
customers, new vs returning customers, repeat rate, purchase frequency, udhaar outstanding,
stock value, … Each metric can be split by a dimension (day / week / month / category / product
/ supplier / payment mode / customer segment / channel) and compared with another period.

The agent queries it through one constrained tool instead of free SQL:

```
query_metric(metric, group_by?, period, compare_to?, filters?)   → dataset_id + rows + formula
explain_change(metric, period, compare_to)                       → driver breakdown
```

`explain_change` answers "why did revenue drop this week" deterministically:
- **Bridge**: revenue = active customers × orders per customer × average order value — shows
  which factor moved (log-change shares, so the parts add up).
- **Contribution**: which categories / products / customer segments explain most of the change.
- **Known causes checked automatically**: stock-outs on top items (days with zero stock ×
  normal demand = estimated lost sales), festival calendar, weekday mix, price changes.
- Output: ranked drivers with ₹ effect; anything not explained is reported as "unexplained".

## 8. Insight engine — What happened / Why it matters / What to do

Every insight is one object:

```
{ what, why, impact_rs, confidence, severity, evidence (metric calls + rows),
  action: { type, label, payload }, detected_at, status: new | seen | acted | dismissed }
```

| Detector | Method (deterministic) | Action |
| --- | --- | --- |
| Revenue anomaly | Day revenue vs same-weekday baseline (robust z-score); then `explain_change` | Open explanation |
| Customers at risk of churn | Days since last purchase > 2 × the customer's own usual gap, with ≥3 past orders | Re-engagement campaign for that segment |
| Repeat purchases falling | Returning-customer orders this period vs last, by recency bucket | Campaign to the lapsed bucket |
| High-value segments | RFM scoring (recency, frequency, monetary quintiles) → Champions, Loyal, At risk, Hibernating, New | "Reward champions" message |
| Cross-sell | Basket lift with significance **(exists)** | Bundle / shelf suggestion, message to buyers of A not B |
| Underperforming products | Velocity below category median **(exists)** + falling trend + margin | Discount within break-even **(exists)** |
| Stock risk | Reorder tiers **(exists)** + stock-out lost-sales estimate | Draft purchase order **(exists)** |
| Udhaar overdue | Ledger balance with oldest unpaid debit > N days | Polite reminder drafts per customer |
| Expense spike | **(exists)** | Review prompt |

Insights are ranked by `impact_rs × confidence` and feed the daily briefing, the Insights page
and the chat ("What should I focus on today?" = top insights).

## 9. The agent

**Tool families** (the LLM only chooses and fills arguments):

| Family | Tools |
| --- | --- |
| Data | `query_metric`, `explain_change`, `customer_segments`, `customer_profile`, `cohort_retention`, `lifecycle_funnel`, existing stock / margin / graph tools **(exist)** |
| Insights | `top_insights`, `insight_detail` |
| Visualisation | `make_chart(dataset_id, kind, x, y, series?, title)` — kind ∈ line, bar, stacked bar, pie, funnel, cohort heatmap, waterfall, scatter; returns an interactive spec **and** a Matplotlib PNG (for WhatsApp / reports). `make_diagram(template, params)` for Mermaid **(exists in part)** |
| Web | `web_search(query, recency)` via Tavily, `web_extract(url, fields)` via TinyFish |
| Actions (drafts only) | `draft_campaign(segment, goal, language)`, `draft_reminders(customers)`, `restock_draft` **(exists)** |

**Charts without invented numbers:** the chart tool takes a `dataset_id` produced by an earlier
data tool in the same turn — it cannot accept numbers from the LLM. The LLM decides *which* chart
fits (trend → line, composition → stacked bar/pie, stages → funnel, retention → cohort heatmap,
change drivers → waterfall, segments → scatter); the code draws it. Matplotlib runs fixed
server-side templates — no model-written code is executed.

**Web research rules:**
- Used only for outside context (market trends, competitor prices, news, benchmarks); never to
  compute the merchant's own metrics.
- **Privacy guard:** queries are scanned and stripped of phone numbers, customer names and exact
  revenue before leaving the server.
- Results appear in a separate "From the web" block with source URL, title and retrieval time;
  web numbers are checked against the cited snippet, not against business data.
- Cached per query for a day to save quota.

**Grounding:** the existing number check stays; it is extended so internal numbers must match
tool data and external numbers must match a cited source.

## 10. Storage — evaluation and recommendation

| Option | Strengths | Weaknesses for us |
| --- | --- | --- |
| **PostgreSQL** (Neon / Supabase free tier) | Integrity, SQL, JSONB for raw rows, `pg_trgm` for fuzzy matching, mature drivers | Needs a server/managed instance; analytics fine at merchant scale |
| **DuckDB** (in-process) | Very fast columnar analytics over Parquet / pandas, zero setup | Not a multi-user write store |
| **ArangoDB** **(exists)** | Graph traversals, already integrated and tested | Weaker for heavy tabular aggregation; less common for CRM data |

**Recommendation:** PostgreSQL as the system of record (canonical tables, insights, campaigns,
chats, source profiles); analytics computed in-process (pandas/DuckDB) over data read from it —
right-sized for single-merchant volumes; ArangoDB kept as an optional graph projection since it
already works. All access goes through a repository interface so the store can change without
touching analytics or the agent. For the hackathon, the current in-memory + ArangoDB setup can
stay and Postgres can come in the last phase.

## 11. UI / UX direction (Orbit-style)

- **Home = briefing + chat.** Greeting, then 3–5 ranked insights as compact rows (what · why ·
  ₹ impact · one action button · "evidence"), then the composer with suggested questions.
  Recent chats in the sidebar.
- **Health strip, not a card wall:** revenue, margin, repeat rate, customers at risk, udhaar
  outstanding — each with a small sparkline and change vs last period.
- **Pages:** Insights feed · Customers (segments, at-risk list, customer 360) · Products & stock
  · Orders & restock **(exists)** · Campaigns · Data sources (connections, mappings to confirm,
  quality).
- **Visual language:** white surfaces, one accent colour, typography and spacing doing the work;
  no gradients, glass, or oversized headings; dense, readable tables; every element earns its place.

## 12. Tools, APIs and keys

| Need | Choice | Key / account |
| --- | --- | --- |
| LLM | Gemini 3.5 / 3.1 Flash-Lite **(exists)** | have |
| Web search | Tavily Search API | **needed** |
| Web extraction | TinyFish (API details to verify when integrating) | **needed** |
| Charts | Recharts (interactive) **(exists)**, Matplotlib (server PNG) | none |
| Diagrams | Mermaid **(exists)** | none |
| Database | PostgreSQL (Neon/Supabase), ArangoDB cloud **(exists, not yet connected)** | needed when we reach that phase |
| Outreach | WhatsApp click-to-send links + CSV for broadcast tools **(exists for suppliers)**; WhatsApp Business API later | later |

## 13. Phased build plan

Each phase ends usable and tested; earlier phases give the most demo value.

| Phase | Builds | Done when |
| --- | --- | --- |
| **1. Customer & ledger foundation** | PostgreSQL repository (canonical tables, migrations, SQLite for tests); customers on bills (partial coverage, realistic), udhaar ledger with repayments, generator with planted CRM stories (churning regulars, champions, overdue udhaar, a cross-sell pair); metrics layer with `query_metric`; RFM, churn risk, cohorts, lifecycle funnel, receivables | Planted stories found with exact ₹ in tests |
| **2. Insight engine** | Detectors → What/Why/Do/Impact objects; `explain_change` (bridge + contribution + stock-out/festival causes); period comparison; briefing built from insights | "Why did revenue drop this week?" answered with ranked ₹ drivers |
| **3. Agent upgrade** | New data + insight tools, `make_chart` with dataset references (interactive + Matplotlib), funnel / cohort / waterfall / scatter; eval set extended to CRM questions | Eval ≥ 90% live; zero ungrounded numbers |
| **4. Campaigns & reminders** | Segment → message (3 languages) → click-to-send / CSV → mark sent → result vs 10% hold-out after N days; udhaar reminders | One full campaign loop with a measured outcome |
| **5. UI redesign** | Orbit-style Home (briefing + chat + recent chats), Insights, Customers + customer 360, Campaigns, Data sources | Owner can go from insight → action in two clicks |
| **6. Intelligent ingestion** | Profiler, table classifier, field mapping with confidence + confirm UI, entity resolution, relationship detection, saved source profiles; SQL + webhook connectors | An unseen billing-app export maps with only low-confidence fields asked |
| **7. Web research** | Tavily + TinyFish tools, privacy guard, cited "From the web" block, caching | Market question answered with dated sources, clearly separated |
| ~~8. PostgreSQL~~ | Moved into Phase 1 by decision | — |

## 14. Honest limits

- **Funnels:** a counter-only kirana has no visit → cart → order data. We show a *customer
  lifecycle* funnel (first purchase → repeat → regular → active) unless order-status or web
  event data is ingested.
- **Customer coverage:** customer insights only cover bills linked to a customer; the coverage
  is always shown with the number.
- **Web benchmarks** are shown only with a source; no unsourced "industry average".
- **Sending:** Muneem drafts; the owner sends. Bulk WhatsApp sending needs the WhatsApp Business
  API and customer consent.
- **Scope vs time:** the full plan is several days of work. Phases 1–3 deliver most of the
  "AI-first CRM" story on top of what already exists.

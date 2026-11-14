# VyaparPulse — Kirana Inventory & Purchase Intelligence Copilot

Infra Shift Hackathon 2026 · Problem Statement 2 · Project document + Technical document · Prepared 14 Nov 2026

> Reading guide: Part A is the project/business document (what and why). Part B is the technical document (how). Sources are cited as \[R1\], \[R2\] … and listed in Section 20 with links. Everything marked **Assumption** or **Unverified** must be checked before it goes on a slide.

## Part A — Project Document

## 1. One-paragraph summary

VyaparPulse turns a kirana store's existing sales, stock and expense records into decisions: which items will run out, which are dead stock, which earn little margin after GST, and exactly what to order tomorrow. The owner uploads CSV/Excel exports, sees a rupee-denominated dashboard, asks questions in Hindi, English or Hinglish, and gets answers where every number is computed by code and shown with its formula. The chat can draw charts and diagrams inline, and the main action is a restock list with a ready-to-send supplier message on WhatsApp. The LLM never does arithmetic and never executes code it wrote.

## 2. PS2 requirement → our proof

The official statement asks for the four MVP items below, plus realistic Indian data (rupees, GST rates, UPI/cash, festive demand), and says a chatbot alone without validated calculations is insufficient \[R1\].

| PS2 requirement \[R1\] | What we build | What the judge sees |
| --- | --- | --- |
| Import a realistic dataset, show key metrics on one dashboard | CSV/XLSX upload with validation report; KPI dashboard | Upload → revenue, gross margin, GST collected, UPI vs cash, stock value in ₹ |
| Answer ≥1 NL question grounded in the data | Tool-calling agent over fixed analytics functions | Ask "Kal kya order karna hai?" → answer + formula + source rows |
| Identify a pattern/exception/opportunity with evidence | Stockout risk, dead stock, low-margin high-revenue items | Card: "₹X trapped in N dead-stock items" with item list and rule used |
| Demonstrate one useful action | Restock plan + supplier WhatsApp draft + CSV export | Click → draft message and downloadable order list |
| Indian realism | GST slabs (0/5/12/18), MRP-inclusive pricing, UPI/cash/credit, festive uplift | Visible in data, metrics and reorder logic |

Optional items in the brief (Hindi/voice, scheduled alerts, forecasting, outreach drafts) are stretch goals only \[R1\].

## 3. Target user and problem

**User:** the owner of a single-counter kirana (grocery) store who already has some digital record: a billing-app export, a spreadsheet, or a CSV a helper maintains. **Assumption:** turnover roughly ₹5–50 lakh/month; validate with any shopkeeper or distributor you can reach.

**Problem:** records exist but do not tell the owner what to buy, what is stuck, or what really earns money. Existing Indian billing/khata products cover billing, GST, stock and udhaar \[R16\]\[R17\]; our wedge is decision support on top of that data, not another billing tool.

**Honest limit to state in the demo:** item-level digital records are not universal. Our first version imports exports from tools shops already use; it does not create the data.

## 4. Scope — priority tiers (not a clock plan)

**P0 — must ship (this is the whole demo):**

1. Upload + validation (required columns, types, duplicates, negative stock, GST-rate sanity).
2. KPI dashboard with ₹ metrics and 3–4 charts.
3. Chat agent with fixed analytics tools; answers show formula and evidence rows.
4. Stockout risk, reorder plan, dead-stock detection.
5. GST-aware margin report (MRP is GST-inclusive).
6. Restock action: order list (CSV) + WhatsApp-style supplier message draft.
7. Inline visuals in chat: native charts, Mermaid flow, sandboxed HTML card.
8. Hindi/Hinglish questions (LLM handles language; numbers come from tools).

**P1 — only after P0 is demo-stable:** expense-spike flag, festive uplift tuning, matplotlib PNG export for reports/WhatsApp attachment, supplier comparison (needs quote data), saved question history.

**P2 — talk about, do not build:** voice input, scheduled alerts, billing-app integrations, forecasting beyond moving average, multi-store.

**Explicitly out:** placing orders automatically (the agent drafts; the owner sends), collecting Aadhaar/PAN/bank data, free-form LLM-written SQL or Python executed against the data.

## 5. Competitive landscape and differentiation

- **Chat2Cash** (Build India 2026 winner): turns WhatsApp chats into structured orders, GST invoices, payment tracking and follow-ups \[R2\]. It is an *input/billing layer*; we are an *insight layer*. The winner reports 7 hours of build time \[R3\]; its accuracy and leakage numbers are self-reported, so do not cite them as fact. Its GitHub repo could not be opened during research (Unverified) \[R2\].
- **SUTRA Core:** FastAPI WhatsApp ERP with inventory, udhaar ledger, GST invoicing and a multi-agent order pipeline; lists ₹499/₹1,499/₹4,999 tiers \[R9\].
- **code-vyapar (Vyapar Vaani):** the closest overlap — its agent works out a reorder quantity and drafts the supplier message without placing the order \[R10\]. Our difference: data-grounded analytics across the whole catalogue (dead stock, margin, trends), visible formulas, and charts/diagrams in chat.
- **StockMitra** (commercial): GST billing, inventory, khata, low-stock and reorder management; starter pricing listed from ₹249 \[R16\].

**Positioning sentence:** "Billing apps record what happened. VyaparPulse tells the owner what to do tomorrow, and shows the arithmetic."

## 6. Demo story (about 4 minutes)

1. **Hook (20s):** "A kirana owner has the data but not the time." Upload the sample CSV.
2. **Dashboard (30s):** revenue, margin after GST, UPI vs cash, stock value.
3. **Ask (60s):** "Kal kya order karna chahiye?" → ranked list; expand "show calculation" for one item.
4. **Pattern (45s):** dead-stock card: "₹X locked in N items, no sale in 30 days."
5. **Visual (30s):** "Atta ki sales trend dikhao" → chart appears inline; "supply flow dikhao" → Mermaid diagram.
6. **Action (30s):** click Restock → order list + supplier message; download CSV.
7. **Business case (45s):** buyer, price, first 100 shops, renewal feature (Section 7).

**Likely judge questions:** (a) *How is this different from pasting a CSV into a chatbot?* — numbers come from tested code, formulas are shown, unknowns are declared. (b) *What if the data is thin?* — items with under 14 days of history get "insufficient data", not a guess. (c) *Can the model make up numbers?* — it never computes; it selects tools and words the result. (d) *Where does the data come from?* — billing-app exports; we do not claim to create records.

## 7. Commercial intent (required closing slide \[R1\])

- **Buyer:** independent kirana owners (and later pharmacies/cafés). **Assumption.**
- **Price (hypothesis):** ₹299/month starter, ₹499/month advanced. Anchors: StockMitra starter from ₹249 \[R16\]; SUTRA's lowest tier ₹499 \[R9\]. Neither anchor validates willingness to pay; say "hypothesis to test in pilots".
- **Cheapest route to first 10:** free 30-day pilot through one or two distributor salesmen or a local accountant who already visits 20–50 shops.
- **First 100:** distributors/wholesalers (they gain from cleaner reorders), CA/accountant referrals, WhatsApp demos. These channels are the ones the brief itself suggests \[R1\].
- **Renewal feature:** the morning order list with a ready supplier message. It saves money every week without the owner opening a dashboard.

## 8. Risk register and fallbacks

| Risk | Likelihood | Mitigation / fallback |
| --- | --- | --- |
| LLM API slow, rate-limited or offline during demo | Medium | Provider-agnostic wrapper; cached responses for the 6 scripted questions; "rules-only mode" that routes keywords to tools without an LLM |
| Wrong tool chosen or bad arguments | Medium | Pydantic-validated tool schemas; refuse/ask when confidence low; show the tool + args used |
| Mermaid fails to render | Medium | Client parse check; fall back to showing the source and a native chart |
| Reused repo has unclear licence or hidden bugs | Medium | Copy ideas/formulas, write our own code; attribute in README (Section 20) |
| Malformed upload on stage | Low | Validation report with row-level errors; one-click "load sample data" |
| Prompt injection through CSV cells (item names etc.) | Low–Med | Treat all data as untrusted text; never put raw cells in system prompts; escape output (Section 16) |
| Scope creep | High | P0 freeze; P1 only after demo rehearsal passes |
| Wi-Fi failure | Medium | Local run + screen-recorded backup demo |

## Part B — Technical Document

## 9. Architecture

```
CSV/XLSX ──► Ingest & Validate ──► DataFrames (pandas) ──► Analytics Engine (pure functions)
                                                              │ stock · reorder · dead stock · margin · payments · expenses
                                                              ▼
User (Hi/En/Hinglish) ──► Chat API (SSE) ──► Agent: LLM picks a tool + args (validated)
                                                              │ tool runs in code → result rows + formula
                                                              ▼
                                   Response envelope {answer, evidence, visuals, actions}
                                                              ▼
        React UI: Dashboard · Chat (markdown + Mermaid) · Chart panel · Sandboxed HTML card · Restock action
```

**Principle:** deterministic code for every number; the LLM for language understanding, tool selection and explanation only. This mirrors the "zero LLM math" approach used by BahiPilot, which computes totals server-side with Python Decimal \[R12\].

## 10. Stack and rationale

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | FastAPI + pandas + Pydantic | Fast to write, typed tool schemas, SSE streaming |
| Storage | In-memory DataFrames (+ optional SQLite for saved history) | Avoids duplicate engines; hackathon-sized data |
| LLM | Any provider behind one `llm.chat(tools=…)` wrapper | Swap if rate-limited; configured via env |
| Frontend | React (Vite) + Tailwind + Recharts | Native charts from JSON specs |
| Chat rendering | Streamdown with its Mermaid plugin | Built for streaming AI markdown; handles incomplete blocks; Mermaid plugin available \[R6\] |
| Server charts | matplotlib → PNG (P1) | Images for reports and WhatsApp drafts |
| Tests | pytest (formulas, validators) | Golden-value tests protect the demo |

**Fallback UI:** Streamlit, if React slows the team down. Streamlit gives dashboards and charts quickly but has no native Mermaid, so chat diagrams would degrade to images.

## 11. Data model and synthetic dataset

**Tables (CSV columns):**

- `items`: item\_id, name, category, hsn (optional), mrp\_incl\_gst, cost\_ex\_gst, gst\_rate (0/5/12/18), pack\_size, supplier\_id, lead\_time\_days
- `sales`: invoice\_id, date, item\_id, qty, unit\_price\_incl\_gst, payment\_mode (UPI/cash/credit)
- `inventory`: item\_id, stock\_qty, as\_of\_date
- `expenses`: date, category (rent/electricity/salary/supplier/other), amount, payment\_mode
- `suppliers` (P1): supplier\_id, name, whatsapp\_number\_placeholder

**Generator spec (seeded, reproducible):** 60–80 SKUs across staples (atta, rice, oil, dal), dairy, snacks, beverages, household and festive items (sweets, dry fruits); about 120 days of sales; a festive fortnight window with category uplift; payment mix trending toward UPI. Verify the exact Diwali dates for the window you choose.

**Planted stories (so the demo has something real to find):**

1. Two fast items that will stock out inside their lead time.
2. Three dead-stock items with meaningful rupees locked.
3. One high-revenue, low-margin staple.
4. One expense category that jumps in the latest month.
5. A festive category whose demand spikes then normalises.

The sample data is synthetic; say so on the slide.

## 12. Calculation engine (formulas and rules)

All money is stored in paise (integers) or `Decimal`; rounding happens only at display.

**GST (MRP is GST-inclusive):** `taxable = price_incl / (1 + rate)`; `gst = price_incl − taxable`. Backing GST out of an inclusive price, instead of adding it on top, avoids overstating takings and tax; code-vyapar documents the same rule \[R10\].

**Margin:** `unit_margin = taxable_selling − cost_ex_gst`; `margin_% = unit_margin / taxable_selling`. Example: ₹105 incl. 5% GST → taxable ₹100; cost ₹88 → margin ₹12 (12%).

**Demand:** `d = units sold in last 14 days / 14`; `σ = std of daily units, last 28 days`. **Festive adjustment:** `d_adj = d × festive_multiplier(category, window)`, default 1.0; derived from earlier-period data or set by config.

**Reorder maths** (standard inventory formulas as implemented in public repos such as StockPulse and Sales-Data-Analysis \[R4\]\[R5\]):

- `safety_stock = Z × σ × √L` (Z = 1.65 for \~95% service level)
- `reorder_point = d_adj × L + safety_stock`
- `order_qty = ceil(max(0, d_adj × (L + R) + safety_stock − stock))`, rounded up to `pack_size` (R = review period, default 2 days)
- `days_of_stock_left = stock / d_adj`

**Golden test:** d = 6/day, L = 2, R = 2, σ = 2, stock = 12 → safety stock 4.67; order qty = ceil(6×4 + 4.67 − 12) = 17 → pack of 6 → **18**.

**Alert tiers:** `days_left < L` → Critical (order now); `< L + R` → Soon; else OK. (Tier idea from the stock-alert dashboards in \[R8\].)

**Dead stock:** `stock > 0` and zero sales in last 30 days; `money_trapped = stock × cost_ex_gst`. **Slow mover:** velocity below 20% of its category median. **High revenue, low profit:** revenue in top quartile and margin\_% in bottom quartile. **Expense flag (P1):** category spend above prior-months mean + 2σ. **Payments:** UPI/cash/credit share by value, weekly.

**Data-sufficiency rule:** fewer than 14 days of history for an item → return `insufficient_data` and exclude it from forecasts. This is the honest-failure behaviour the brief rewards.

## 13. Agent design

**Tools (each pure, typed, unit-tested):**

| Tool | Returns |
| --- | --- |
| `get_kpis(period)` | revenue, taxable revenue, GST, margin, UPI/cash split, stock value |
| `top_items(by, n, period)` | ranked items |
| `sales_trend(item?, category?, grain, period)` | time series |
| `stock_status(item?)` | days left, tier |
| `reorder_plan(horizon_days, budget?)` | items, qty, ₹ cost, formula inputs |
| `dead_stock(days)` | items, ₹ locked |
| `margin_report(group_by)` | margin %, flags |
| `payment_mix(period)` | shares |
| `explain_flow(topic)` | Mermaid text from templates filled with computed values |

**Loop:** user message → LLM selects tool + JSON args → Pydantic validates → code executes → result goes back to the LLM → LLM writes a short answer in the user's language. If no tool fits, the agent says so instead of guessing. Never pass raw data cells into the system prompt.

**Response envelope:**

```json
{
  "answer_md": "...",
  "evidence": [{"tool": "reorder_plan", "args": {}, "rows": [], "formula": "..."}],
  "visuals": [{"type": "chart|mermaid|html", "spec": {}}],
  "actions": [{"type": "restock_draft", "payload": {}}],
  "confidence": "ok|insufficient_data"
}
```

**Evaluation set:** 15 fixed questions (English, Hindi, Hinglish) with expected tool + expected number; run before every demo rehearsal. Examples: "Kal kya order karna chahiye?", "Which items haven't sold in 30 days?", "UPI vs cash split dikhao", "Atta ka margin kitna hai?".

## 14. Inline visuals in chat

| Visual | How | Safety |
| --- | --- | --- |
| **Native charts** (P0) | Tool returns a JSON chart spec (type, series, labels); React renders with Recharts | No code from the LLM |
| **Mermaid** (P0) | Template diagrams filled with computed values; optional LLM-authored Mermaid validated with a parse check; render in chat via Streamdown's Mermaid plugin \[R6\] or assistant-ui's mermaid component, which shows a skeleton while streaming and renders when complete \[R7\] | On parse failure show source + native chart |
| **HTML card** (P0-lite) | Rendered in an `<iframe sandbox srcdoc=…>` without `allow-same-origin` | Isolated from the app |
| **matplotlib PNG** (P1) | Server-side function from fixed chart templates → base64 PNG | Not LLM-written code |

Streaming-markdown libraries exist for exactly this case: they handle half-finished fences while tokens arrive \[R6\]\[R7\]\[R14\]. One description of Streamdown says Mermaid appears as a code block with a render button, so test the behaviour in our UI before relying on it.

Chat-with-CSV projects that let the model write matplotlib or pandas code exist (PandasAI \[R18\], easonlai's Streamlit demo \[R19\]); they are useful references, but executing generated code is a security risk, so we use fixed tools instead.

## 15. API surface

| Endpoint | Purpose |
| --- | --- |
| `POST /api/upload` | accept CSV/XLSX, return validation report |
| `POST /api/sample` | load seeded sample data |
| `GET /api/kpis` | dashboard metrics |
| `GET /api/alerts` | stock tiers, dead stock, margin flags |
| `POST /api/chat` | SSE stream: tokens, then the final envelope |
| `POST /api/restock/draft` | order list + supplier message |
| `GET /api/restock/export.csv` | downloadable order list |

## 16. Security and safety

- No LLM-generated code or SQL is executed; only whitelisted tools run.
- CSV content is untrusted input: strip control characters, cap length, escape on render, and keep it out of system prompts (item names could carry instructions).
- Upload limits: file size and row caps, extension and MIME checks.
- Sandboxed iframe for HTML; strict CSP on the app shell.
- Secrets in environment variables; no keys in the repo.
- Collect no sensitive personal data (aligned with the brief's safety framing for open problems \[R1\]); demo data is synthetic.
- The agent drafts messages but never sends or orders on the owner's behalf (same boundary as \[R10\]).

## 17. Testing and verification

1. **Unit tests:** GST back-out, margin, reorder golden case (Section 12), dead-stock and sufficiency rules.
2. **Scenario tests:** each planted story must appear in the output with the exact ₹ values.
3. **Question eval:** the 15-question set (Section 13).
4. **Failure drills:** kill the LLM → rules-only mode answers the scripted questions; upload a broken CSV → readable error.
5. **Pre-demo checklist:** sample data loads; all scripted questions return in under a few seconds; restock CSV downloads; backup video recorded.

## 18. Deployment and demo resilience

- One-command local start (`make dev` or a single script) with the seeded sample pre-loaded.
- Optional public deploy (any free tier) but the demo runs locally.
- Cache the LLM responses for scripted questions; keep a 2-minute screen recording as insurance.
- Pin dependency versions; commit the lockfile.

## 19. Reuse map — what to take, what to leave

Research covered READMEs and descriptions only. Code quality, runnability and **licences were not verified**; read each LICENSE file before copying anything, and prefer re-implementing small pieces.

| Area | Source | Take | Leave |
| --- | --- | --- | --- |
| Reorder formulas | StockPulse \[R4\] | Moving-average, safety stock, reorder point, EOQ definitions | Its dependency-free local dashboard |
| Safety stock/ROP UI | Sales-Data-Analysis \[R5\] | Parameter tooltips and ROP/SS presentation idea | Streamlit app structure |
| Dashboard layout | adfelt/inventory-dashboard \[R20\], StockSight \[R21\] | KPI card and chart layout; export button; basic chat commands idea | Rule-based JSON chatbot |
| Alert tiers | Snowflake\_Hackathon \[R8\] | Priority/alert-table concept | Snowflake-specific stack |
| GST handling | code-vyapar \[R10\] | Back-out-of-MRP rule; draft-don't-order boundary | Next.js/Supabase/Sarvam stack |
| Money integrity | BahiPilot \[R12\] | Decimal/paise discipline; LLM never does maths | Ollama extraction path, SQLite schema |
| GSTIN/GSTR-1 | OrderMitra \[R13\] | Check-digit validation idea (P2) | TypeScript WhatsApp pharma flow |
| Hindi/regional bills | BillBot \[R11\] | Multilingual extraction idea only; it supports 18% GST only | Whole app |
| Indian ERP structure | SUTRA Core \[R9\] | Entity ideas (inventory, udhaar, GST) | Multi-agent WhatsApp pipeline |
| Order capture | Chat2Cash \[R2\] | Few-shot Hinglish prompting and human-verification pattern (concept) | Everything else; repo unverified |
| Chat rendering | Streamdown \[R6\], assistant-ui \[R7\], markstream-react \[R14\] | Streaming markdown + Mermaid | — |
| NL-to-chart reference | PandasAI \[R18\], easonlai demo \[R19\] | Idea of chart-from-question UX | Executing model-written code |
| POS reference | KiranaMitra \[R15\] | Domain vocabulary (offline-first POS, udhaar) | React Native app |

**Attribution rule:** list every repo we learned from in the project README, even where no code was copied. Anything copied must carry its licence notice.

## 20. References and verification status

- **\[R1\]** Infra Shift Hackathon 2026 official problem statements (PDF supplied by the team) — PS2 requirements, MVP list, commercial-intent prompt.
- **\[R2\]** Chat2Cash, Devfolio project page — https://devfolio.co/projects/chatcash-9791 (page read; the linked GitHub repo `swayam-mishra/Chat2Cash` could not be opened).
- **\[R3\]** Winner's LinkedIn profile (7-hour, solo build claim) — https://www.linkedin.com/in/swayam-mishra15/
- **\[R4\]** StockPulse — https://github.com/ayush03ch/StockPulse
- **\[R5\]** Sales-Data-Analysis — https://github.com/SirLJM/Sales-Data-Analysis
- **\[R6\]** Streamdown (via assistant-ui guide) — https://www.assistant-ui.com/docs/guides/streamdown.md
- **\[R7\]** assistant-ui Mermaid component — https://www.assistant-ui.com/elements/mermaid-diagram (source file: https://github.com/assistant-ui/assistant-ui/blob/main/packages/ui/src/components/assistant-ui/mermaid-diagram.tsx)
- **\[R8\]** Snowflake\_Hackathon stock management — https://github.com/ANISHTWAGLE/Snowflake\_Hackathon
- **\[R9\]** SUTRA Core — https://github.com/ravikumarve/sutra
- **\[R10\]** code-vyapar (Vyapar Vaani) — https://github.com/amaancoderx/code-vyapar
- **\[R11\]** BillBot — https://github.com/prasadgt007/BillBot
- **\[R12\]** BahiPilot write-up — https://dev.to/srijan07/from-messy-whatsapp-chits-to-gst-invoices-in-15s-local-ai-with-ollama-qwen-25-p79 (repo: https://github.com/srijan-0-7/hacktoberfest-dev-2)
- **\[R13\]** OrderMitra — https://github.com/shreesha345/ordermitra
- **\[R14\]** markstream-react docs — https://markstream-vue-docs.simonhe.me/frameworks/react.html
- **\[R15\]** KiranaMitra — https://github.com/hardikkanajariya-in/kiranamitra (write-up: https://dev.to/hardikkanajariya/building-kiranamitra-an-offline-first-pos-app-for-indian-kirana-stores-with-react-native-8fp)
- **\[R16\]** StockMitra listing (pricing, features) — https://www.techjockey.com/detail/stockmitra
- **\[R17\]** BUSY kirana software page (feature scope of incumbents) — https://busy.in/accounting-software/retail-shop/kirana-store/
- **\[R18\]** PandasAI — https://github.com/sinaptik-ai/pandas-ai
- **\[R19\]** chat\_with\_csv\_streamlit\_with\_chart — https://github.com/easonlai/chat\_with\_csv\_streamlit\_with\_chart
- **\[R20\]** adfelt/inventory-dashboard — https://github.com/adfelt/inventory-dashboard
- **\[R21\]** DaviesCodez/inventory-dashboard (StockSight) — https://github.com/DaviesCodez/inventory-dashboard
- **\[R22\]** RackPilot (comparison table that lists Chat2Cash as "now Snapdo") — https://github.com/monickverma/rackpilot

**Not verified:** licences of all repos; whether any repo runs as documented; Chat2Cash's current status (a third-party table calls it "now Snapdo" \[R22\]); all pricing and willingness-to-pay figures; the exact Diwali dates for the sample window.

## 21. Open items before building

1. Ask organisers whether pre-existing code or libraries are permitted and how reuse should be declared \[R1 does not say\].
2. Choose the LLM provider and confirm API limits.
3. Decide React vs Streamlit (default: React + Streamdown).
4. Freeze the P0 list and the 15-question evaluation set.

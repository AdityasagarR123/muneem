# Muneem — Kirana Inventory & Purchase Intelligence

*Aapka digital muneem: hisaab bhi, salaah bhi.*

Muneem turns a kirana store's sales, stock and expense exports into decisions: what will run
out, what to order (and from whom first), what money is stuck in dead stock, and which items
earn little after GST. **Every number is computed in code and returned with its formula.**

> Sample data in `data/sample/` is **synthetic**.

## Run it (Windows)

```powershell
./dev.ps1            # backend :8000 (new window) + frontend :5173 - open http://localhost:5173
./dev.ps1 -Backend   # API only - interactive docs at http://localhost:8000/docs
./dev.ps1 -Test      # backend test suite
```

First run creates `backend/.venv` and installs `frontend/node_modules`. The sample dataset loads
automatically. Needs Python 3.11+ and Node 18+.

**Frontend** (`frontend/`, React + Vite + Tailwind + Recharts + Mermaid): Dashboard, Ask Muneem
(chat with live tool steps, charts, diagrams, "show calculation", number-verification badge),
Restock (editable purchase orders, WhatsApp, sent/received), Data (upload + validation report).

## ArangoDB (cloud)

1. Copy `.env.example` to `.env`.
2. Fill `ARANGO_URL`, `ARANGO_DB`, `ARANGO_USER` and type `ARANGO_PASSWORD` yourself.
3. Restart the API (or `POST /api/arango/reconnect`). The current dataset is synced to ArangoDB
   in the background; `GET /api/status` shows `sync: synced` when done.
4. Optional live checks (use a throwaway database — data collections are replaced):
   `RUN_ARANGO_TESTS=1` then `./dev.ps1 -Test`.

Without ArangoDB everything still works: data stays in memory and graph questions are answered
by the in-memory graph engine (responses say which engine answered in `engine`).

### What lives in ArangoDB

| Collection | Type | Purpose |
| --- | --- | --- |
| items, suppliers, categories, invoices | documents | graph vertices |
| supplies (supplier→item), belongs_to (item→category), contains (bill→item) | edges | `kirana_graph` |
| sales, inventory, expenses | documents | raw records |
| uploads, chat_history, meta | documents | audit + dataset status |

All AQL is in `backend/app/db/aql.py`, fixed and parameterised (no LLM-written queries).

## Chat (Gemini)

Set `LLM_API_KEY` in `.env` (see `.env.example`). Models are tried in the order of `LLM_MODEL`;
a rate-limited model is skipped for a minute. With no key, or if every model is unavailable, the
chat answers in rules-only mode. Every answer's numbers are checked against the tool results
(`grounding` in the response).

```powershell
cd backend; .venv/Scripts/python -m app.agent.eval              # eval set, rules mode
cd backend; .venv/Scripts/python -m app.agent.eval --mode llm   # eval set, live Gemini (uses quota)
```

Muneem never sends messages or places orders: it drafts, the owner sends.

## API overview

| Endpoint | What it answers |
| --- | --- |
| `POST /api/upload` | CSV/XLSX upload (per table or one workbook) → row-level validation report |
| `POST /api/sample` | (re)load the synthetic sample |
| `GET /api/kpis` | revenue, GST collected, margin after GST, bills, payment split, net estimate, vs previous period |
| `GET /api/alerts` | everything needing attention today |
| `GET /api/stock`, `/api/reorder?budget_rs=` | tiers, days left, order quantities (budget-aware) |
| `GET /api/dead-stock`, `/api/slow-movers` | money trapped, clearance break-even price |
| `GET /api/margin?group_by=item\|category\|supplier` | GST-aware margins + flags |
| `GET /api/payments`, `/api/trends`, `/api/top-items`, `/api/expenses`, `/api/money-flow` | |
| `GET /api/graph/supplier-priority` | which supplier to call first |
| `GET /api/graph/supplier-risk/{supplier}?delay_days=` | what runs out if a supplier is late |
| `GET /api/graph/bought-together/{item}` | basket pairs with lift and significance |
| `GET /api/graph/substitutes/{item}`, `/supply-path/{item}`, `/supply-flow`, `/supplier-dependency` | |
| `GET /api/items/{item}` | item 360° view |
| `POST /api/chat` | chat (English / Hinglish / Hindi) → answer + evidence + charts + actions; `"stream": true` for SSE |
| `POST /api/restock/draft` | purchase orders per supplier + WhatsApp-ready message (en / hinglish / hi) |
| `PATCH /api/restock/orders/{po}` | edit quantities / language of a draft |
| `POST /api/restock/orders/{po}/status` | `sent` → `received` (adds stock; alerts recompute) or `cancelled` |
| `GET /api/restock/export.csv` | order list as CSV (`po_id`, `draft_id`, or a fresh plan) |
| `GET /api/brief?lang=` | morning brief - 5 lines, WhatsApp share link, no AI needed |
| `GET /api/resolve?q=cheeni` | Hindi/Hinglish → item / category / supplier |

Items and suppliers can be referenced by id or by name, including Hinglish (`aata 5kg`, `sharma`).

## Attribution

Formulas and ideas learned from public projects are listed in `MUNEEM_PLAN.md` (reuse map).
No code was copied.

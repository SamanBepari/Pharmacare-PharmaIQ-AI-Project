# PharmaCare + PharmaIQ — Technical Guide

**Complete project explanation: the original pharmacy system, the new AI layer, and how they fit together.**

Version 1.0 · Python 3.9+ · Flask · SQLite · pandas · scikit-learn

---

## Contents

1. [Overview — what was built](#1-overview--what-was-built)
2. [Architecture](#2-architecture)
3. [Repository map](#3-repository-map)
4. [The original system (PharmaCare)](#4-the-original-system-pharmacare)
5. [Data model](#5-data-model)
6. [Life of a question — end-to-end walkthrough](#6-life-of-a-question--end-to-end-walkthrough)
7. [PharmaIQ core modules](#7-pharmaiq-core-modules)
8. [RAG — medicine knowledge assistant](#8-rag--medicine-knowledge-assistant)
9. [Analytics — forecasting and anomaly detection](#9-analytics--forecasting-and-anomaly-detection)
10. [Security and safety model](#10-security-and-safety-model)
11. [Configuration reference](#11-configuration-reference)
12. [REST API reference](#12-rest-api-reference)
13. [Frontend](#13-frontend)
14. [Evaluation and testing](#14-evaluation-and-testing)
15. [Extending PharmaIQ](#15-extending-pharmaiq)
16. [Changes made to the original files](#16-changes-made-to-the-original-files)
17. [Known limitations (honest list)](#17-known-limitations-honest-list)
18. [How to explain this project](#18-how-to-explain-this-project)

---

## 1. Overview — what was built

PharmaCare already managed medicines, barcodes, sales, alerts, roles, Excel exports and a REST API. **PharmaIQ** adds an AI layer that lets people *ask* instead of *click*.

The central design idea is that **different questions need different tools**:

| Question type | Wrong tool | Right tool | Why |
|---|---|---|---|
| “What is the return policy?” | database | **RAG** over SOP documents | the answer lives in text |
| “Which medicines expire in 30 days?” | RAG / LLM memory | **validated SQL on the live DB** | must be exact and current |
| “How much will we sell next week?” | LLM guess | **statistical model** with an interval | must show uncertainty |
| “How much paracetamol for my son?” | anything | **refuse** | personal medical advice |

PharmaIQ routes every question to the right tool, and the answer *always shows where it came from*.

### Feature → module map (the 5 core + 6 advanced features from the brief)

| Feature | Implemented in |
|---|---|
| 1 Medicine knowledge assistant (upload docs, cited answers) | `pharmaiq/rag/*`, `routes.py` (upload), `ai_knowledge.html` |
| 2 Natural-language inventory analytics (tables + charts) | `planner.py`, `nl2sql.py`, `tools.py`, `ai.js` |
| 3 Expiry & stock intelligence, transparent rules, facts vs explanation | `tools.py` (`assumptions`, `facts`), `agent.py` (`explanation`) |
| 4 Conversational reporting, follow-ups, downloads | `planner.py` (follow-ups), `agent.py` (state), `exports.py` |
| 5 Role-aware assistant | `permissions.py`, `sql_guard.py`, `tools.py`, `routes.py` |
| Hybrid retrieval (embeddings + keyword) | `rag/retriever.py` |
| Text-to-SQL with validation | `sql_guard.py`, `nl2sql.py`, `llm.py` |
| Sales trend / forecasting with uncertainty | `analytics.py`, `tools.forecast_demand` |
| Inventory anomaly detection | `analytics.py`, `tools.detect_anomalies` |
| Tool-calling workflow | `tools.py` (13 tools), `planner.py` |
| Audit trail | `agent._finish`, table `ai_audit_log`, `ai_audit.html` |
| Testing: pytest + custom RAG & SQL evaluation sets | `tests/`, `evaluation/`, `evaluate.py` |

### Deliberate deviations from the brief’s suggested stack

| Brief suggested | What this project does | Reason |
|---|---|---|
| Sentence Transformers + FAISS/Qdrant | TF-IDF vectors by default; sentence-transformers + FAISS **optional** | works with `pip install` only, no 90 MB model download, no GPU; one env var upgrades it |
| Plotly | Chart.js | already loaded by the original dashboard; no new frontend dependency |
| Hugging Face model or hosted API | **Offline rule engine by default**, Anthropic / OpenAI-compatible optional | the app is fully demonstrable and testable with no API key; LLM is an upgrade, not a requirement |
| PyMuPDF | PyMuPDF if installed, else `pypdf` | fewer hard dependencies |

---

## 2. Architecture

```
                         ┌──────────────────────────── Browser ─────────────────────────────┐
                         │  Ask PharmaIQ  ·  Knowledge Base  ·  AI Insights  ·  Audit · Eval │
                         └───────────────┬───────────────────────────────────────────────────┘
                                         │ fetch /api/ai/*  (session cookie + CSRF header)
┌────────────────────────────────────────▼───────────────────────────────────────────────────┐
│  Flask app (app.py)  ──registers──►  pharmaiq.routes  (Blueprint)                          │
│                                                │                                           │
│                                      ┌─────────▼──────────┐                                │
│                                      │  PharmaIQ agent     │  agent.py                      │
│                                      └─────────┬──────────┘                                │
│   1. safety.check_question()  ← deterministic pre-filter, BEFORE any model sees the text   │
│   2. planner  (RulePlanner | LLMPlanner → falls back to rules on any failure)              │
│   3. execute  ┌──────────────┬─────────────────┬───────────────────┬────────────┐          │
│               │ tool route   │ SQL route       │ RAG route         │ help/clarify│         │
│               │ tools.py     │ nl2sql / LLM SQL│ rag/retriever.py  │            │          │
│               └──────┬───────┴────────┬────────┴─────────┬─────────┴────────────┘          │
│                      │ ONLY path to the database          │ kb_chunks                       │
│               ┌──────▼────────────────▼──────┐     ┌──────▼─────────┐                      │
│               │ sql_guard.ReadOnlySession    │     │ BM25 + vectors │                      │
│               │ 5 layers (see §7.2)          │     │ + RRF fusion   │                      │
│               └──────┬───────────────────────┘     └────────────────┘                      │
│   4. compose  facts (code) │ evidence (rows + SQL) │ explanation (rules|LLM) │ sources      │
│   5. audit    ai_audit_log  (redacted question, route, tool, SQL, row count, evidence)      │
└────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                     ▼
                  instance/pharmacy.db  (original 4 tables  +  5 PharmaIQ tables)
```

**Key invariants**

1. **Facts come from code, never from a model.** Every number shown is computed from rows returned by a guarded query.
2. **The LLM never touches the database.** It can only *propose* a plan or SQL string; the guard executes (or rejects) it.
3. **Failure is boring.** Invalid LLM output → rule planner. No documents match → “I won’t guess”. Unexpected exception → generic message + audit entry.
4. **Offline-first.** Every feature works with no network and no API key.

---

## 3. Repository map

```
app.py                       original Flask app; 4 small PharmaIQ hooks (see §16)
seed_data.py                 original demo medicines/sales (orphan-sales bug fixed)
seed_ai_demo.py              NEW  fresh expiry dates + 120 days sales + injected anomalies + sample docs
requirements*.txt  .env.example  .gitignore  README.md

pharmaiq/
  __init__.py                register_pharmaiq(app, db_path, low_stock, expiry_days)
  config.py                  Settings dataclass from env vars; today() with test override
  db.py                      5 additive tables; connect_rw(); prune_old()
  permissions.py             role → tables/columns, row filters, page access, schema prompt
  sql_guard.py               tokenizer, validate_sql(), ReadOnlySession (authorizer, temp views, timeout)
  tools.py                   13 controlled tools, ToolResult/Table containers, argument validation
  nlp.py                     Vocab (categories/companies/medicines), period/number/medicine matching
  nl2sql.py                  spec parser + SQL renderer (offline text-to-SQL)
  planner.py                 RulePlanner, LLMPlanner, Plan, follow-up logic
  agent.py                   PharmaIQ orchestrator, grounding checks, auto charts, audit
  safety.py                  clinical/credential refusals, DISCLAIMER, redact()
  llm.py                     Provider / Anthropic / OpenAI-compatible, extract_json()
  analytics.py               forecast_series(), spike_anomalies()
  exports.py                 to_xlsx(), to_csv() with formula-injection protection
  evaluate.py                run_all() + CLI
  routes.py                  Blueprint: pages, API, CSRF, uploads
  rag/
    text.py                  tokeniser, stemmer, stop-words, synonyms
    ingest.py                PDF / DOCX / TXT / MD extraction
    chunker.py               page- and section-aware overlapping chunks
    store.py                 kb_documents / kb_chunks CRUD, role-filtered loading
    retriever.py             BM25, TF-IDF / sentence-transformer backends, RRF, relevance gate
    answer.py                extractive, cited answer builder

knowledge_base/              6 sample leaflets + 6 sample SOPs (demo content)
evaluation/                  sql_eval.json  rag_eval.json  safety_eval.json
tests/                       helpers.py + 6 test modules (89 tests)
templates/                   12 original + ai_chat, ai_knowledge, ai_insights, ai_audit, ai_evaluation
static/css/ai.css  static/js/ai.js
docs/                        this guide, user guide (md + pdf), legacy/ original PDFs
```

---

## 4. The original system (PharmaCare)

### 4.1 Stack

| Layer | Technology | Role |
|---|---|---|
| Backend | Python, Flask ≥ 3, Werkzeug | routes, sessions, password hashing, secure filenames |
| Database | SQLite (WAL mode) | single-file database in `instance/pharmacy.db` |
| Templates | Jinja2 + Bootstrap 5.3 + Bootstrap Icons | server-rendered pages, modal forms |
| Charts | Chart.js 4.4 | dashboard + (new) AI charts |
| Barcode | QuaggaJS | camera-based barcode scanning |
| Export | openpyxl | colour-coded Excel export |

### 4.2 `app.py` in brief

| Area | Routes | Notes |
|---|---|---|
| Auth | `/login`, `/logout` | session keys: `user_id`, `username`, `role`, `full_name`; passwords hashed (pbkdf2) |
| Decorators | `login_required`, `roles_required(*roles)` | redirect with flash message |
| Dashboard | `/` | counts, stock value, 7-day sales chart, alerts |
| Medicines | `/medicines`, `/add_medicine`, `/update_medicine/<id>`, `/delete_medicine/<id>`, `/medicine/<id>` | search over name/company/type/ref/barcode; image upload; pagination (10/page) |
| Alerts | `/alerts` | expiring ≤ 30 days, expired, low stock ≤ 10 (`get_alerts()`) |
| Reports | `/reports` (admin, pharmacist) | by-type totals, low-stock top-10, expiry list, 30-day sales summary |
| Sales | `/sales`, `/add_sale` | validates stock, decrements `product_qty`, logs action |
| Users / Logs | `/users`, `/add_user`, `/delete_user/<id>`, `/logs` | admin only |
| Export | `/export_excel` | red rows = expired, yellow = low stock |
| API | `/api/medicines`, `/api/stats`, `/api/barcode/<code>` … | session **or** `X-API-Key` header |
| Barcode / Profile | `/barcode`, `/profile` | |

**Business rules PharmaIQ reuses (single source of truth):** `LOW_STOCK_THRESHOLD = 10` (low stock means `quantity ≤ 10`) and `EXPIRY_WARN_DAYS = 30`. They are passed into `register_pharmaiq()` so the AI and the Alerts page never disagree.

### 4.3 Roles

| Role | Can |
|---|---|
| admin | everything |
| pharmacist | view, add/edit medicines, record sales, reports, export |
| staff | view, record sales |

PharmaIQ maps these onto AI permissions in `permissions.py` (§7.1).

---

## 5. Data model

### 5.1 Original tables (unchanged)

```
users(id, username UNIQUE, password, role, full_name, email, created_at)
medicines(id, ref_no, company_name, medicine_type, medicine_name, lot_no, issue_date, exp_date,
          uses, side_effect, precautions, dosage, price, product_qty, image_filename, barcode,
          created_at, updated_at)
sales(id, medicine_id, medicine_name, quantity, unit_price, total_price, sold_by, sale_date)
activity_logs(id, user_id, username, action, details, ip_address, created_at)
```

“Category” in questions means `medicines.medicine_type`. Dates are ISO strings (`YYYY-MM-DD`), `sale_date` is `YYYY-MM-DD HH:MM:SS`.

### 5.2 Tables added by PharmaIQ (`db.py`, all `CREATE TABLE IF NOT EXISTS`)

| Table | Purpose | Key columns |
|---|---|---|
| `kb_documents` | one row per uploaded leaflet / SOP | `title`, `filename`, `doc_type` (leaflet/sop/reference), `min_role`, `source_hash` (UNIQUE → dedupe), `uploaded_by`, `chunk_count` |
| `kb_chunks` | searchable passages | `doc_id`, `chunk_index`, `page`, `section`, `text`, `embedding` BLOB, `embed_model` |
| `ai_audit_log` | one row per request | `username`, `role`, `question` (redacted), `route`, `tool`, `sql_text`, `sql_params`, `row_count`, `evidence` (JSON summary), `llm_used`, `status`, `error`, `latency_ms` |
| `ai_results` | result sets for download | `id`, `user_id`, `payload` JSON (tables, SQL, facts, question), auto-pruned after 48 h |
| `ai_context` | follow-up memory | `conv_id`, `user_id`, `state` JSON (last route/tool/args/spec/top row), auto-pruned |

Nothing in the original tables is altered, so PharmaIQ can be removed by deleting the five tables.

---

## 6. Life of a question — end-to-end walkthrough

Example: a pharmacy manager types **“Which medicines are expiring within the next 30 days, and what is their current stock?”**

| Step | Code | What happens |
|---|---|---|
| 1 | `ai.js → POST /api/ai/ask` | JSON body + `X-CSRF-Token` header + session cookie |
| 2 | `routes.api_access` | logged in? role allowed? CSRF valid? |
| 3 | `PharmaIQ.ask()` | trims, length-checks (≤ 600 chars), makes/keeps `conv_id` |
| 4 | `safety.check_question()` | no clinical / credential pattern → continue |
| 5 | `ReadOnlySession(...)` opened for this role + username | read-only connection, authorizer, row filters, timeout |
| 6 | `nlp.load_vocab()` | reads categories, companies, medicine names for matching |
| 7 | `RulePlanner.plan()` | “expiring” keyword, `extract_days()` → 30 → `Plan(tool="get_expiring_medicines", args={days:30})` |
| 8 | `tools.run_tool()` | validates args, binds `:today` (the **actual** current date) and `:limit_date`, runs fixed SQL through the guard |
| 9 | `_compose_data_response()` | stores the result set in `ai_results` (for Excel/CSV), builds facts, assumptions, chart, rule-based explanation |
| 10 | `_save_state()` | remembers tool + args + top row for follow-ups |
| 11 | `_finish()` | writes `ai_audit_log` (redacted question, tool, SQL, row count, evidence summary — **not** the rows) |
| 12 | `ai.js addBot()` | renders *Database facts* → chart → table → *Show query* → *Explanation* → follow-up chips → Excel/CSV buttons |

Simplified response object (what the API returns):

```json
{
  "route": "tool", "tool": "get_expiring_medicines", "conv_id": "80db40fd02a74dc8",
  "title": "Medicines expiring within 30 days",
  "facts": ["4 medicine batch(es) expire within 30 days.",
            "Units affected: 550; stock value at risk: 10090.00.",
            "Soonest expiry: Cetirizine 5mg/5ml on 2026-10-18 (6 days left)."],
  "assumptions": ["As-of date: 2026-10-12. Window: 2026-10-12 → 2026-11-11 (30 days)."],
  "evidence": {
    "as_of": "2026-10-12", "result_id": "42aa8259dcdb",
    "tables": [{"title": "...", "columns": ["medicine_name","category","batch","exp_date","quantity","days_left","stock_value"],
                "rows": [["Cetirizine 5mg/5ml","Syrup","LOT-2024-03","2026-10-18",80,6,700.0]],
                "sql": "SELECT medicine_name, ... FROM medicines WHERE ...", "params": {"today":"2026-10-12","limit_date":"2026-11-11"}}]
  },
  "chart": {"type":"bar","horizontal":true,"labels":["..."],"datasets":[{"label":"Units in stock","data":[80]}]},
  "explanation": {"generated_by": "rules", "text": "Sell, transfer or return the earliest-expiring batches first (FEFO)…"},
  "sources": [], "followups": ["Which of these have the highest stock value?", "..."],
  "latency_ms": 14, "mode": "offline (rule-based)"
}
```

The UI deliberately renders **four visually separate zones**: green *Database facts*, blue *From documents*, purple *Explanation*, amber *Warnings*. A reader can always tell computed data from commentary.

---

## 7. PharmaIQ core modules

### 7.1 `permissions.py` — who can see what

```python
ROLE_SCHEMA = {
  "admin":      {"medicines": ALL_COLS, "sales": ALL_COLS, "activity_logs": {id, username, action, details, created_at}},
  "pharmacist": {"medicines": ALL_COLS, "sales": ALL_COLS},
  "staff":      {"medicines": ALL_COLS, "sales": ALL_COLS},      # but ROW_FILTERS restricts sales rows
}
ROW_FILTERS = {"staff": {"sales": "sold_by"}}                      # staff → only rows where sold_by = their username
```

* The **`users` table is in nobody’s allow-list**: password hashes can never be read via AI.
* `activity_logs.ip_address` and `user_id` are excluded even for admins.
* `PAGE_ROLES` controls which AI pages/endpoints each role may open.
* `schema_prompt(role)` generates the schema text given to an LLM: it describes **only** what that role may query, so the model cannot even learn that other tables exist.

### 7.2 `sql_guard.py` — five independent safety layers

Nothing generated by a model or rule engine is executed directly. A statement must pass **all five** layers:

| # | Layer | Mechanism | Stops |
|---|---|---|---|
| 1 | **Lexical validation** | A small tokenizer (aware of strings / quoted identifiers) enforces: ≤ 2500 chars; exactly **one** statement; first word `SELECT`/`WITH`; no comments; forbidden keywords (`INSERT UPDATE DELETE DROP ALTER CREATE REPLACE ATTACH DETACH PRAGMA VACUUM … RECURSIVE`); no `sqlite_*` names; no `main.` / `temp.` prefixes; every function call must be in an allow-list (`count sum avg min max round coalesce lower upper date julianday strftime …`); only `:named` parameters | DML/DDL, stacked queries, hidden comments, `load_extension`, `randomblob` bombs, schema tricks |
| 2 | **SQLite authorizer** | `conn.set_authorizer()` is called by SQLite itself while compiling the query, for **every table, column and function** actually used (including inside sub-queries, unions, CTEs, views). Anything outside the role’s allow-list → `DENY` | `UNION SELECT password FROM users`, column smuggling, aliasing tricks the tokenizer cannot see |
| 3 | **Row-level filter** | For staff, a `TEMP VIEW sales` shadows the real table: `SELECT * FROM main.sales WHERE sold_by = '<username>'`. Temp objects win name resolution; layer 1 forbids the `main.` prefix | staff reading other people’s sales |
| 4 | **Read-only connection** | opened `mode=ro` (fallback `PRAGMA query_only=ON`) | any write that slipped through |
| 5 | **Resource limits** | `fetchmany(max_rows+1)` (default 200, flags `truncated`); progress-handler **timeout** (default 3 s) | runaway cross-joins, huge dumps |

Attack catalogue (all covered by `tests/test_sql_guard.py` and `evaluation/safety_eval.json`):

| Input | Blocked by | Error code |
|---|---|---|
| `DELETE FROM medicines` | 1 | `not_select` |
| `SELECT 1; DROP TABLE medicines` | 1 | `multi_statement` |
| `SELECT 1 -- x` / `/* x */` | 1 | `comment` |
| `SELECT name FROM sqlite_master` | 1 | `internal_table` |
| `SELECT * FROM main.sales` (staff bypass attempt) | 1 | `schema_prefix` |
| `SELECT load_extension('x')` / `randomblob(…)` | 1 + 2 | `forbidden_keyword` / `function` |
| `SELECT password FROM users` | 2 | `not_authorized` |
| `SELECT … UNION SELECT password FROM users` | 2 | `not_authorized` |
| `SELECT * FROM activity_logs` (admin; contains `ip_address`) | 2 | `not_authorized` |
| `SELECT username FROM activity_logs` as pharmacist/staff | 2 | `not_authorized` |
| Cartesian product of `sales` ×4 | 5 | `timeout` |
| raw `conn.execute("DELETE …")` even bypassing validation | 2 + 4 | exception |

`ReadOnlySession.execute(sql, params)` returns a `QueryResult` (columns, rows, truncated, SQL, params, tables used, columns used, elapsed ms) — the same data the audit trail and “Show query” button use.

### 7.3 `tools.py` — controlled tool functions

Each tool has **hand-written, parameterised SQL**, clamps and validates its arguments (`_int`, `_enum`, `_str`; bad input → `ToolError`), and runs through the guard (so role limits apply even to tools). Tools return `ToolResult(tables, facts, assumptions, notes, chart, followups)`.

| Tool | Roles | Arguments | Returns |
|---|---|---|---|
| `get_expiring_medicines` | all | `days`, `include_expired`, `category`, `company`, `name` | name, category, batch, exp_date, quantity, days_left, stock_value |
| `get_expired_medicines` | all | filters | + days_overdue |
| `get_low_stock_items` | all | `threshold` (default 10), filters, `limit` | rule shown: quantity ≤ threshold |
| `get_stock_level` | all | `name` | per batch + status (Expired / Expiring soon / Low stock / OK) |
| `search_medicines` | all | category, company, name, price/qty ranges, `sort_by`, `limit` | |
| `get_inventory_value` | all | `group_by` none/category/company | products, units, stock_value |
| `get_sales_summary` | all | `period`, `group_by` none/day/medicine/category/seller, `category` | transactions, units, revenue |
| `get_category_sales` | all | `period`, `category` | revenue by category |
| `get_top_selling` | all | `limit`, `period`, `metric`, `category` | |
| `get_management_summary` | all | `period` | 5 tables: KPIs, low stock, expiring, sales by category, top sellers |
| `get_reorder_suggestions` | admin, pharmacist | `target_days` | days of cover from 30-day sales velocity + suggested order qty (rule printed) |
| `forecast_demand` | admin, pharmacist | `name`, `horizon_days` ≤ 60 | forecast, lower_80, upper_80 (§9) |
| `detect_anomalies` | admin, pharmacist | `lookback_days` | spikes/drops + 7 data checks (§9) |

Periods: `today, yesterday, last_7_days, this_month, last_month, last_30_days, last_90_days, this_year, all_time`. Every tool **states its assumptions** (as-of date, window, rule) in `assumptions`, e.g. *“Rule: low stock means quantity ≤ 10 units (same rule as the Alerts page).”*

### 7.4 `nlp.py` and `nl2sql.py` — offline text-to-SQL

`nlp.py` extracts structure from a question using vocabulary read from the live DB: category (`tablets` → `Tablet`), company, medicine (first word, with `difflib` typo tolerance: `paracetmol` → `paracetamol`), period, day-counts, `top N`.

`nl2sql.parse_spec()` turns a question into a **spec dict**, e.g.

```python
"Show all products with fewer than 10 units"
→ {"table": "medicines", "cmp": {"field": "product_qty", "op": "<", "value": 10.0}}
```

`nl2sql.build_sql(spec)` renders parameterised SQL. Because the spec is structured, **follow-ups are just spec merges**: “Now only tablets” adds `{"category": "Tablet"}` and re-renders. Supported shapes: comparisons on quantity/price (`fewer/less/below/under`, `more/above/over`, `at least/at most`), expiry before/after a date, `how many`, totals, average price, `by category/company/medicine/seller/day`, ordering (`most expensive`, `cheapest`, `highest stock`), and sales of one medicine/category.

Note the documented subtlety: “*fewer than* 10 units” is a strict `< 10`, whereas the Alerts-page “low stock” rule is `≤ 10`; the answer prints that assumption.

### 7.5 `planner.py` — deciding how to answer

`Plan(route, tool, args, sql, params, spec, query, subject, message, planner, is_followup)` where `route ∈ {tool, sql, rag, help, clarify}`.

**RulePlanner priority order** (first match wins):

1. help (`hi`, `what can you do`)
2. follow-up (starts with *now / and / only / by / for …* or mentions *those / this category …*) **and** conversation state exists
3. knowledge cues (*policy, SOP, leaflet, side effects, storage, stored, handled …*) → RAG — checked **before** inventory words, so “procedure for **expired** stock” goes to documents, not the expiry tool
4. anomaly → forecast → reorder → summary → inventory value
5. sales (top sellers / by category / by day / by seller / one medicine → SQL)
6. expiry (expiring vs already expired)
7. low stock (tool) — unless a numeric comparison is present (then text-to-SQL)
8. structured filters / aggregations → SQL
9. named medicine → stock lookup (or RAG if asking for information)
10. fallback → RAG (which declines if nothing matches)

**Follow-up examples** (all tested): *“Which categories generated the highest sales this month?” → “Now show only the products in this category”* resolves “this category” to the **top row of the previous result** and switches to `search_medicines(category=…)`; *“Show products with fewer than 50 units” → “Now only tablets”* merges into the SQL spec; RAG follow-ups prepend the remembered medicine (“and its storage?”).

**LLMPlanner** sends: today’s date, role, the tool catalogue **for that role**, the role-filtered schema, known categories, the previous plan and the question; expects one JSON object. `_validate()` rejects unknown/forbidden tools, malformed args and unknown routes. On *any* `LLMError`/`ValueError` it silently falls back to `RulePlanner` (visible as `planner: "rules (llm fallback)"`). For an `sql` plan the string still goes through the guard; on a non-authorization error the agent asks the model **once** to repair it, with the error text.

### 7.6 `agent.py` — the orchestrator

* `ask(question, conv_id)` — public entry; never raises (generic error + audit row instead).
* `_answer()` — safety → session → plan → execute → compose.
* `tool_response(name, args)` — direct tool call used by the Insights page (same guard, same audit).
* **RAG route** — retrieve → extractive answer; if an LLM is configured, ask it to answer *only* from numbered passages with `[n]` citations; the answer is **kept only if every citation index is valid**, otherwise the extractive answer is used. If no passage is relevant and the question names a medicine, a clearly labelled fallback shows the *internal medicine record* (“NOT an approved leaflet”).
* **Grounding check** — `numbers_grounded(text, result, question)`: every number in an LLM explanation (except 0 and 1) must appear in the evidence (facts, assumptions, row cells, row counts, the question) within ±0.5; otherwise the explanation is dropped and the rule-based one is shown. This is a cheap, effective guard against invented percentages.
* **Privacy knob** — `PHARMAIQ_LLM_MAX_ROWS` (default 15) limits how many rows an LLM may see; `0` sends aggregates only.
* **State** — `ai_context` stores the last route/tool/args/spec/subject/top-row, keyed by `(conv_id, user_id)` so one user can never continue another user’s conversation.

### 7.7 `safety.py` — the clinical boundary

A deterministic filter that runs **before** any planner or LLM:

| Refused | Detection (rule-based regexes) |
|---|---|
| Patient-specific dosing | person word (*my son/daughter/baby/patient…*) + dose/treatment cue |
| “Can I take X with Y?” | `can/should I take|give|use|mix|combine…` **and** a medicine cue (drug-like suffixes, *tablet, mg, with…*) — so “Can I take a look at expiring stock?” is **allowed** |
| Pregnancy / breastfeeding safety | pregnancy words + safe/take/use |
| Recommending or diagnosing | *which medicine should I take*, *prescribe*, *diagnose*, *what’s wrong with me* |
| Weight/age-based dosing | `\d+ kg|years old` + dose cue |
| Credentials | *password, api key, secret key, token, users table…* |

Refusals explain what PharmaIQ *can* do instead. A false-positive suite (`allowed_cases`) keeps the filter from over-blocking. Unrelated to refusal: `DISCLAIMER` is attached to documentation answers, and `redact()` strips emails and phone-like numbers from questions before they hit the audit log.

### 7.8 `llm.py` — optional providers

`Provider` (offline marker), `AnthropicProvider` (`POST https://api.anthropic.com/v1/messages`, `x-api-key`, `anthropic-version: 2023-06-01`), `OpenAICompatProvider` (`POST {base}/chat/completions`). Pure `urllib` — no SDK dependency, 30 s timeout, temperature 0. `extract_json()` tolerates markdown fences and chatter by scanning for the first balanced `{…}`. The LLM does exactly three jobs, all behind checks: **plan**, **repair SQL** (once), **explain/answer from supplied data**.

### 7.9 `exports.py`

Builds `.xlsx` (About sheet with question, as-of date, assumptions, facts and **the SQL**, plus one styled sheet per table) and `.csv` (UTF-8 BOM so Excel opens it). Cells beginning with `= + - @` are prefixed with `'` to prevent spreadsheet formula injection. Downloads are owner-only (`ai_results.user_id`) and return 404 otherwise.

---

## 8. RAG — medicine knowledge assistant

**Pipeline:** upload → extract → chunk → store → (index on first search) → retrieve → cite.

### 8.1 Ingestion (`rag/ingest.py`, `chunker.py`, `store.py`)

| Format | Extractor | Section / page info |
|---|---|---|
| `.pdf` | PyMuPDF if installed, else `pypdf` | page numbers |
| `.docx` | python-docx; headings become sections; tables flattened to `a | b` rows | sections |
| `.md` | heading-aware | sections |
| `.txt` | plain | — |

Scanned PDFs are rejected with an explicit “needs OCR” message. Files are de-duplicated by SHA-256. **Chunker**: ~140 words with 30-word overlap; chunks never cross a page or section boundary, so every citation is precise.

### 8.2 Hybrid retrieval (`rag/retriever.py`)

1. **Keyword**: own **BM25** implementation (k1 = 1.5, b = 0.75) over stemmed tokens; title is indexed twice so “metformin” strongly favours the metformin leaflet.
2. **Vector**: default **char n-gram TF-IDF** (3–5 grams, scikit-learn) — tolerant of typos and word forms. Optional `sentence-transformers` embeddings (cached in `kb_chunks.embedding`) with FAISS `IndexFlatIP` when installed. If the optional backend fails to load the retriever falls back to TF-IDF and shows the reason on the Knowledge Base page.
3. **Fusion**: **Reciprocal Rank Fusion**, `score = 1/(60+rank_bm25) + 0.8/(60+rank_vector)`.
4. **Relevance gate** (prevents confident nonsense): a hit is “relevant” only if query-term *coverage* ≥ 0.5 with BM25 > 0 (looser for longer queries). “Who won the football world cup?” therefore returns **no sources** and PharmaIQ says it won’t guess.
5. **Named-document focus**: if the question names a document subject (“metformin”) and that document matches at least as well as the others, results from other documents are demoted (so a side-effects question about metformin never cites amoxicillin).
6. **Domain synonyms** before stemming (`fridge → refrigerator`, `meds → medicine`, …). This was added after the evaluation suite caught a real miss (see §14).
7. **Role filtering happens before indexing**: the index for `staff` literally does not contain admin-only chunks (per-role cache, invalidated by a count/max-id fingerprint when documents change).

### 8.3 Answer building (`rag/answer.py`)

Offline mode is **extractive**: top-3 relevant chunks; within each, up to 3 sentences with the best query-term overlap (boilerplate banners like “SAMPLE document…” are skipped), shown as `**Title › Section · page** [n]: sentence…` with a Sources panel. No generated text, so no hallucination. With an LLM, the answer must cite `[n]` and citations are verified (§7.6).

### 8.4 Medicine info as reference, not advice

Leaflet answers carry the disclaimer *“Reference information only — not individualised medical advice.”*; personal questions are refused before retrieval (§7.7). Sample leaflets deliberately contain **no numeric dosing**.

---

## 9. Analytics — forecasting and anomaly detection

### 9.1 Demand forecast (`analytics.forecast_series`)

Daily units sold (zero-filled) for up to 120 days → model:

1. **Weekday seasonality** (only when ≥ 28 days): factor per weekday = mean(weekday)/mean(all), **shrunk 50 % toward 1** to avoid over-fitting, normalised to mean 1.
2. De-seasonalise, fit **simple exponential smoothing**; α chosen by grid search (0.05–0.95) minimising one-step squared error.
3. **Damped trend**: slope of the last ≤ 28 de-seasonalised days, damped by φ = 0.9 per step.
4. Forecast = (level + damped trend) × weekday factor, floored at 0.
5. **80 % prediction interval**: `±1.2816·σ·√(1+(h−1)α²)` (σ = std of one-step errors), widening with horizon; the total-period interval is the midpoint between “independent errors” and “perfectly correlated errors”.
6. **Back-test**: retrain on all-but-last-7 days, forecast those 7, report MAE **vs a naive average** — and warn if the model did *worse* than naive.
7. **Confidence label**: *low* (< 28 days or CV > 1.0), *high* (≥ 56 days and CV < 0.6), else *medium*, with the reason printed.
8. **Refuses** if there are < 14 days or no sales, rather than inventing a number.

The UI shows actual history, forecast, and dashed lower/upper bounds, plus *days of cover* when a single medicine is chosen. Notes always say forecasts are estimates and don’t model promotions or outbreaks.

### 9.2 Anomaly detection

**Sales spikes/drops** (`spike_anomalies`): for each day, baseline = **median of the previous 28 days**, scale = `1.4826·MAD` (floored so flat history doesn’t explode), flag `z ≥ 3.5` when also ≥ 5 units and ≥ 1.5× baseline (drops need a decent baseline). Run on total sales and on each medicine with ≥ 10 selling days.

**Data-quality checks** (SQL, via the guard): negative stock; expiry before issue date; missing expiry; duplicate barcodes; `total_price ≠ quantity × unit_price`; sales referencing a deleted medicine; sales dated after the batch’s expiry. Findings are sorted by severity and labelled *flags for human review, not proof of error*.

> The detector found a **real bug in the original project** during development: `seed_data.py` re-inserted medicines (new auto-increment ids 32–61) but hard-coded sales to ids 1–30, leaving all 20 sales orphaned. Fixed (see §16).

---

## 10. Security and safety model

| Threat | Mitigation |
|---|---|
| LLM or user produces destructive SQL | 5-layer guard (§7.2); DB connection is read-only; tools use fixed templates |
| SQL injection via question text | no string interpolation of user text into SQL — values go through named parameters; rule-built SQL uses whitelisted columns/operators |
| Prompt injection inside documents | retrieved text is passed as *data*, citations are verified, LLM output is never executed; the LLM has no tools |
| Reading other users’ data | row-level temp views, column allow-lists, `users` table never exposed, conversation state keyed to user |
| Privilege escalation via AI | role checks on pages, API, tools and SQL; admin-only documents invisible in lower roles’ index |
| Leaking secrets | credential questions refused; `ip_address` hidden from AI; audit log stores redacted questions and **no rows** |
| XSS | all dynamic UI content is built with `textContent`/DOM nodes, never `innerHTML`; Jinja auto-escapes server-rendered values |
| CSRF | per-session token required on all AI `POST`s (header for fetch, hidden field for forms); `SameSite=Lax` cookie |
| Spreadsheet formula injection | `= + - @` cells neutralised in exports |
| Hallucinated numbers | facts computed in code; LLM explanations discarded if numbers aren’t grounded |
| Hallucinated documentation | relevance gate → “I won’t guess”; LLM answers need valid citations |
| Unsafe medical advice | deterministic refusal filter before any model; sample content has no dosing numbers |
| Resource abuse | 600-char questions, 200-row cap, 3 s SQL timeout, 5 MB uploads |
| Stale data | “as of” date printed on every data answer; today’s date is bound at query time (never the model’s memory) |

**Not covered (be aware):** no rate limiting, no per-user quotas, no login lockout, the legacy `/api/*` key shortcut remains, and the demo passwords are public. See §17.

---

## 11. Configuration reference

All optional. Put them in `.env` (loaded by python-dotenv) or the process environment. Defaults give a fully offline system.

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | built-in demo key | Flask session signing key (**change it**) |
| `PHARMAIQ_LLM_PROVIDER` | `offline` | `offline` · `anthropic` · `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `PHARMAIQ_API_KEY` | — | credentials |
| `PHARMAIQ_MODEL` | `claude-sonnet-5-5` / `gpt-4o-mini` | model name |
| `PHARMAIQ_BASE_URL` | OpenAI URL | for Ollama (`http://localhost:11434/v1`), LM Studio, vLLM, Groq … |
| `PHARMAIQ_LLM_MAX_ROWS` | 15 | rows an LLM may see (0 = aggregates only) |
| `PHARMAIQ_LLM_TIMEOUT` | 30 | seconds |
| `PHARMAIQ_EMBEDDINGS` | `tfidf` | or `sentence-transformers` |
| `PHARMAIQ_EMBED_MODEL` | `all-MiniLM-L6-v2` | |
| `PHARMAIQ_CHUNK_WORDS` / `_CHUNK_OVERLAP` | 140 / 30 | |
| `PHARMAIQ_MAX_ROWS` | 200 | result row cap |
| `PHARMAIQ_SQL_TIMEOUT` | 3 | seconds |
| `PHARMAIQ_UPLOAD_MAX_MB` | 5 | document size (Flask’s global limit is also 5 MB) |
| `PHARMAIQ_LOW_STOCK` / `PHARMAIQ_EXPIRY_DAYS` | 10 / 30 | overridden by the values passed from `app.py` |
| `PHARMAIQ_RESULTS_TTL_HOURS` | 48 | pruning of stored results / follow-up state |
| `PHARMAIQ_TODAY` | — | pin “today” (`YYYY-MM-DD`) for tests/evals |

---

## 12. REST API reference

All AI endpoints require a **logged-in session** (not the legacy API key) and return JSON errors: `401 Unauthorized`, `403 Forbidden`, `400 CSRF`. Fetch the token from `<meta name="csrf-token">` on any page.

### `POST /api/ai/ask`
```bash
# 1) log in and keep cookies  2) read the CSRF token from a page (-c re-saves the session that holds it)  3) ask
curl -c jar -d "username=admin&password=admin123" http://127.0.0.1:5000/login
TOKEN=$(curl -b jar -c jar -s http://127.0.0.1:5000/ai | grep -o 'name="csrf-token" content="[^"]*' | sed 's/.*content="//')
curl -b jar -c jar -X POST http://127.0.0.1:5000/api/ai/ask \
  -H "Content-Type: application/json" -H "X-CSRF-Token: $TOKEN" \
  -d '{"question":"Show all products with fewer than 10 units"}'
```

| Endpoint | Roles | Description |
|---|---|---|
| `POST /api/ai/ask` | all | body `{question, conv_id?}` → response object (§6) |
| `GET /api/ai/tools` | all | tool catalogue for the caller’s role |
| `GET /api/ai/health` | all | LLM label, retrieval backend + fallback note, document count |
| `GET /api/ai/export/<id>.xlsx\|csv` | owner | download stored result |
| `GET /api/ai/knowledge`, `GET /api/ai/knowledge/search?q=` | all | list / debug retrieval (shows keyword score, vector score, coverage) |
| `GET /api/ai/insights/forecast?name=&horizon=` | admin, pharmacist | |
| `GET /api/ai/insights/anomalies?days=` · `/reorder` | admin, pharmacist | |
| `POST /api/ai/evaluate` | admin | run all suites |

Pages: `/ai`, `/ai/knowledge`, `/ai/insights`, `/ai/audit`, `/ai/evaluation`; form posts `/ai/knowledge/upload`, `/ai/knowledge/delete/<id>`, `/ai/knowledge/load-samples`.

---

## 13. Frontend

* **`base.html`** — new sidebar section *PHARMAIQ · AI*, `ai.css` link, CSRF `<meta>`; role-conditioned links.
* **`ai_chat.html` + `ai.js`** — chat thread, suggestion chips, mode badge. `ai.js` renders a response with DOM builders only (XSS-safe), instantiates Chart.js charts (bar / horizontal bar / line with dashed uncertainty bands), a collapsible **Show query** panel, Excel/CSV buttons, follow-up chips.
* **`ai_knowledge.html`** — document table (type, visibility, passages), upload form, “Load sample documents”, retrieval test box showing keyword/vector/coverage scores.
* **`ai_insights.html`** — forecast chart + table for one medicine or all, anomaly table, reorder table.
* **`ai_audit.html`** — filterable, paginated audit table with expandable SQL.
* **`ai_evaluation.html`** — one-click scorecard (SQL correctness, RAG hit@1/@3, keyword recall, safety).
* **`ai.css`** — colour-coded zones (green facts, blue documents, purple explanation, amber warnings, red refusals) consistent with the existing green theme.

---

## 14. Evaluation and testing

### 14.1 Unit / integration tests — 89 tests (`tests/`)

| Module | Covers |
|---|---|
| `test_sql_guard.py` | every attack in §7.2, row limit, timeout, row-level filter, DB unchanged after attacks |
| `test_tools.py` | each tool vs fixed expected values, argument validation, SQL-injection-as-data, role-restricted tools, forecast bounds, spike + bad-total detection, analytics edge cases (flat series, short series, no false alarms) |
| `test_rag.py` | stemmer/synonyms, correct document, typo tolerance, named-document focus, no-answer, **role-based document visibility**, dedupe, chunker boundaries, DOCX ingestion |
| `test_agent.py` | routing incl. the brief’s example, follow-ups, user isolation, refusals + no false refusals, PII-free audit rows, **LLM path with a scripted fake provider** (valid plan, destructive SQL, `users` table, invalid JSON, forbidden tool, hallucinated numbers, uncited answers) — no network needed |
| `test_api.py` | auth, CSRF, role gates, export ownership, upload flow + visibility, original pages still work |
| `test_evaluation.py` | quality gate: the three evaluation suites must pass |

Run: `python -m unittest discover -s tests -t .` (or `pytest`). Tests build their own temporary database and pin “today” to 2026-06-15, so results are deterministic.

### 14.2 Evaluation harness (`pharmaiq/evaluate.py`, `evaluation/*.json`)

| Suite | Cases | How it scores |
|---|---|---|
| `sql_eval.json` | 12 | right route + tool, **and the returned count/value equals an independent oracle SQL** run directly on the DB (so it is correct on *any* data) |
| `rag_eval.json` | 15 | expected document in top-1 / top-3, keyword recall, and 3 “no-answer” traps |
| `safety_eval.json` | 24 | 7 must-refuse, 4 must-not-refuse, 12 SQL attacks, 1 integrity check |

Current result on the demo database (offline mode): **51 / 51**, RAG hit@1 = 1.00, hit@3 = 1.00, keyword recall = 1.00. Add your own cases by editing the JSON files.

**Evaluation caught real defects during development** (kept as a worked example): a missing *fridge ↔ refrigerator* synonym, and a document-focus rule where a generic word in one title (“damage”, “medicines”) overrode better matches — fixed with synonyms and the “only focus if it matches at least as well” rule (§8.2). Re-running the suite after each fix is the intended workflow when you tune retrieval.

---

## 15. Extending PharmaIQ

**Add a tool** (e.g. `get_supplier_summary`)
1. Write `def get_supplier_summary(ctx, ...)` in `tools.py` using `ctx.query(title, sql, params)`; return a `ToolResult` with `facts` and `assumptions`.
2. Register a `ToolSpec(name, description, params, roles, fn)` in `TOOLS`.
3. (Rule mode) add a regex in `planner.py`; (LLM mode) nothing — the catalogue is generated from `TOOLS`.
4. Add a test and an oracle case in `sql_eval.json`.

**Allow another table** — add it to `ROLE_SCHEMA` (columns explicitly) and, if rows must be restricted, to `ROW_FILTERS`. Nothing else changes: the guard and the LLM schema prompt derive from it.

**Use an LLM** — set `PHARMAIQ_LLM_PROVIDER` + key (see §11). For local/private: Ollama with `PHARMAIQ_BASE_URL=http://localhost:11434/v1`.

**Better retrieval** — `pip install -r requirements-optional.txt`, set `PHARMAIQ_EMBEDDINGS=sentence-transformers`. Embeddings are cached in the DB; the Knowledge Base page shows which backend is active.

**New document formats** — add an extractor returning `[{page, section, text}]` in `rag/ingest.py` and its extension to `SUPPORTED`.

**Move to PostgreSQL** — the guard is SQLite-specific (authorizer, temp views). On Postgres replace layers 2–4 with a read-only DB role, `GRANT SELECT` on allowed columns and row-level security; layers 1 and 5 carry over.

**Production serving** — use gunicorn behind HTTPS; the retriever cache is per-process (rebuilds lazily), all other state is in the database so multiple workers are safe.

---

## 16. Changes made to the original files

| File | Change | Why |
|---|---|---|
| `app.py` | `SECRET_KEY` read from environment (same default) | production hygiene |
| `app.py` | optional `python-dotenv` load at top | `.env` support, silent if absent |
| `app.py` | `from pharmaiq import register_pharmaiq` + `register_pharmaiq(app, DATABASE, LOW_STOCK_THRESHOLD, EXPIRY_WARN_DAYS)` | mounts the blueprint |
| `app.py` | `init_ai_tables(DATABASE)` at the end of `init_db()` | creates the 5 tables |
| `templates/base.html` | `ai.css`, CSRF meta tag, “PHARMAIQ · AI” sidebar block, brand badge | navigation |
| `seed_data.py` | resolve each sale’s `medicine_id` **by medicine name** after re-inserting | **bug fix**: ids drifted to 32–61 so all sales were orphaned |
| `instance/pharmacy.db` | the 20 shipped sales rows re-pointed to the correct medicine ids (name match) | same bug, repaired in the shipped data |
| `gitignore` → `.gitignore` | renamed; inline comments removed; new patterns | git ignores a file called `gitignore` and doesn’t support trailing comments (the old file would have mis-parsed `instance/pharmacy.db   # …`) |
| `requirements.txt` | added pandas, numpy, scikit-learn, python-docx, pypdf, python-dotenv | AI layer |
| `pharmacy_user_guide.pdf`, `pharmacare_technical_guide.pdf` | moved to `docs/legacy/` | still valid for the original app |

No original route, table, or template (other than `base.html`) was otherwise modified.

---

## 17. Known limitations (honest list)

1. **Offline planner is keyword-based.** It handles the documented question shapes and reasonable paraphrases; unusual phrasing may fall back to the documentation search or a “couldn’t find” answer. An LLM provider removes most of this limit (the guard keeps it safe).
2. **TF-IDF retrieval is lexical.** It tolerates typos and word forms but not deep paraphrase (“cold storage for diabetes pens”). Use the sentence-transformers option for that.
3. **Forecasts are simple by design.** No promotions, holidays, price changes or supplier lead times; ≥ 14 days history needed; the interval is approximate.
4. **Anomaly thresholds are fixed** (z ≥ 3.5, 28-day window) and not per-medicine tuned.
5. **Sample medical content is demonstrative.** Replace `knowledge_base/` with approved documents and have a pharmacist review any clinically relevant use.
6. **Single-currency, single-site, no stock ledger.** `sales` is the only movement history; stock changes by manual edit aren’t tracked, so “unexpected stock changes” can’t be detected beyond the data checks listed.
7. **Safety filter is rule-based.** It is intentionally conservative (rare false refusals) but a determined user could phrase a clinical question it misses; the documentation route still only shows leaflet text, never computes doses.
8. **No rate limiting / lockout** and the legacy API-key shortcut remains (see §10).
9. **SQLite concurrency**: fine for one pharmacy; heavy multi-user write loads would need PostgreSQL.
10. The **shipped demo database has old dates**; run `seed_ai_demo.py` to refresh them.

---

## 18. How to explain this project

**30-second version.** “I took an existing Flask pharmacy-management app and added an AI layer. Questions about live stock and sales are answered by validated read-only SQL and controlled tools; questions about leaflets and SOPs are answered by hybrid RAG with citations; forecasts and anomalies come from pandas models with stated uncertainty. The LLM is optional and never touches the database — it can only propose plans, which a five-layer guard checks. It respects the existing roles, refuses personal medical advice, keeps an audit trail, and I measure it with a 51-case evaluation suite.”

**Questions you should be ready for**

| Question | Short answer |
|---|---|
| Why not RAG for everything? | Live numbers must be exact and current; documents can’t answer “what expires in 30 days”. Route by question type (§1). |
| How do you stop the LLM dropping a table? | It never executes anything. SQL passes tokenizer rules, SQLite’s authorizer, row-level views, a read-only connection and a timeout (§7.2). Tests attack each layer. |
| How do you stop hallucinated numbers? | Numbers come from code. LLM prose is discarded if it contains a number not in the evidence (§7.6). |
| How is it role-aware? | Same roles as the app, enforced at the SQL level (column allow-lists, temp views), at tool level and at document level (§7.1, §8.2). |
| How do you know it works? | Oracle-based SQL evaluation, RAG hit@k, attack and refusal suites, plus 89 tests; the evaluation caught two real retrieval bugs (§14). |
| What would you improve? | Sentence-transformer embeddings by default, per-medicine anomaly thresholds, rate limiting, lead-time-aware reorder, PostgreSQL row-level security (§17). |
| How do you handle the medical risk? | Reference-only content, deterministic refusal filter before any model, no dosing in samples, citations, disclaimers, pharmacist-in-the-loop (§7.7, §8.4). |

---

*End of Technical Guide.*

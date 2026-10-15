# 💊 PharmaCare + PharmaIQ — Pharmacy Management with an AI Layer

**PharmaCare** is a Flask + SQLite pharmacy management system (inventory, sales, barcode, alerts, roles, REST API).
**PharmaIQ** is the AI layer added on top of it: ask questions in plain English and get answers from

| Source | How | Example |
|---|---|---|
| 🗄️ **Live database** | Controlled tools + validated, read-only text-to-SQL | “Which medicines are expiring within the next 30 days, and what is their current stock?” |
| 📚 **Documents** (leaflets, SOPs) | RAG: hybrid keyword + vector search, answers with citations | “How should insulin be stored?” · “What is the return policy?” |
| 📈 **Analytics** | pandas forecasting with uncertainty, anomaly detection | “Forecast demand for the next 14 days” · “Any unusual sales spikes?” |

> **Safety boundary.** PharmaIQ is reference + analytics. It **refuses** personal dosing, diagnosis and treatment advice, never invents stock numbers, and every database answer shows the exact SQL that produced it.

📘 Full documentation: **[`docs/TECHNICAL_GUIDE.md`](docs/TECHNICAL_GUIDE.md)** (architecture, every module) · **[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)** (inputs, sample questions, page-by-page tour). PDF versions sit next to them.

---

## 🚀 Quick start (5 commands)

```bash
# 1. unzip and enter the folder
cd PharmaCare-PharmaIQ

# 2. create a virtual environment (Python 3.9+)
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. install dependencies
pip install -r requirements.txt

# 4. (optional but recommended) load demo data + sample leaflets/SOPs so every AI feature has something to show
python seed_ai_demo.py

# 5. run
python app.py
```

Open **http://127.0.0.1:5000** → log in `admin / admin123` → sidebar **PharmaIQ · AI → Ask PharmaIQ**.

The database (`instance/pharmacy.db`) is created/upgraded automatically. PharmaIQ only **adds** five tables and never alters the original ones.

> Starting from an empty database instead? Run `python app.py` once, stop it, run `python seed_data.py` (30 medicines), then `python seed_ai_demo.py`.

### Run the tests and the AI evaluation

```bash
python -m unittest discover -s tests -t .       # 89 tests (or: pip install -r requirements-dev.txt && pytest)
python -m pharmaiq.evaluate                      # RAG / SQL-correctness / safety scorecard on your database
```

---

## 🔑 Default logins

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Pharmacist | `pharmacist` | `pharma123` |
| Staff | `staff` | `staff123` |

> ⚠️ Change all passwords and set `SECRET_KEY` before any real use.

---

## ✨ PharmaIQ features

| # | Feature | Where |
|---|---|---|
| 1 | **Medicine knowledge assistant** — upload PDF / DOCX / TXT / MD; answers cite document, section and page | *Knowledge Base*, *Ask PharmaIQ* |
| 2 | **Natural-language inventory analytics** — tables + charts, SQL shown | *Ask PharmaIQ* |
| 3 | **Expiry & stock intelligence** — transparent rules; database facts are visually separated from explanations | *Ask PharmaIQ*, *AI Insights* |
| 4 | **Conversational reporting** — follow-ups (“Now show only tablets”), Excel/CSV download, management summary | *Ask PharmaIQ* |
| 5 | **Role-aware assistant** — same permissions as the app, enforced at SQL level; staff see only their own sales | everywhere |
| 6 | **Hybrid retrieval** — BM25 keyword + vector search fused with RRF (optional sentence-transformers + FAISS) | *Knowledge Base* |
| 7 | **Text-to-SQL with validation** — 5 independent safety layers, read-only | *Ask PharmaIQ* |
| 8 | **Sales forecasting** — damped-trend exponential smoothing + weekday seasonality, 80 % interval, back-test | *AI Insights* |
| 9 | **Anomaly detection** — sales spikes/drops (robust z-score) + 7 data-quality checks | *AI Insights* |
| 10 | **Tool calling** — 13 controlled functions (`get_expiring_medicines()`, `get_low_stock_items()`, `get_sales_summary()` …) | *Ask PharmaIQ* |
| 11 | **Audit trail** — question (PII-redacted), route, tool, SQL, row count, evidence summary | *AI Audit Trail* (admin) |
| 12 | **Evaluation harness** — 51 cases: SQL answers vs oracle queries, RAG hit@k, safety/attack cases | *AI Evaluation* (admin) |

### Offline by default, LLM optional
With no configuration PharmaIQ runs **fully offline** using a rule-based planner and extractive, cited answers. To let an LLM plan queries and write explanations, set (see `.env.example`):

```bash
PHARMAIQ_LLM_PROVIDER=anthropic     # or: openai (also Ollama / LM Studio / vLLM via PHARMAIQ_BASE_URL)
ANTHROPIC_API_KEY=...
```
The LLM only *plans* and *explains*. Whatever it produces still passes the SQL guard, and its explanations are discarded if they contain numbers that aren't in the data.

---

## 👥 Role permissions

| Action | Admin | Pharmacist | Staff |
|---|:-:|:-:|:-:|
| Dashboard, medicines, sales, alerts, barcode | ✅ | ✅ | ✅ |
| Add / edit medicines | ✅ | ✅ | ❌ |
| Delete medicines, users, logs | ✅ | ❌ | ❌ |
| Reports, Excel export | ✅ | ✅ | ❌ |
| **Ask PharmaIQ** (live data + documents) | ✅ | ✅ | ✅ (own sales only) |
| **Knowledge Base** — search | ✅ | ✅ | ✅ (docs marked for staff) |
| **Knowledge Base** — upload | ✅ | ✅ | ❌ |
| **Knowledge Base** — delete / load samples | ✅ | ❌ | ❌ |
| **AI Insights** (forecast, anomalies, reorder) | ✅ | ✅ | ❌ |
| **AI Audit Trail**, **AI Evaluation** | ✅ | ❌ | ❌ |

---

## 🌐 REST API

Original endpoints (`/api/medicines`, `/api/stats`, …) are unchanged. New, **session-login only** (role-aware; the API-key shortcut is *not* accepted) and `POST`s need the `X-CSRF-Token` header:

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/ai/ask` | `{"question": "...", "conv_id": "..."}` → full answer object |
| `GET` | `/api/ai/tools` | Tools available to your role |
| `GET` | `/api/ai/health` | LLM mode, retrieval backend, document count |
| `GET` | `/api/ai/export/<result_id>.xlsx\|csv` | Download a result set (owner only) |
| `GET` | `/api/ai/knowledge` · `/api/ai/knowledge/search?q=` | List / test retrieval |
| `GET` | `/api/ai/insights/forecast?name=&horizon=` · `/anomalies` · `/reorder` | Analytics (admin/pharmacist) |
| `POST` | `/api/ai/evaluate` | Run the evaluation suites (admin) |

---

## 📁 Project structure

```
PharmaCare-PharmaIQ/
├── app.py                    # original Flask app (+ 3 small PharmaIQ hooks)
├── seed_data.py              # original demo medicines (orphan-sales bug fixed)
├── seed_ai_demo.py           # NEW: 120 days of sales, fresh expiry dates, sample documents
├── pharmaiq/                 # NEW: the AI layer
│   ├── agent.py              #   orchestrator: safety → plan → execute → compose → audit
│   ├── planner.py            #   rule-based + LLM planners, follow-up handling
│   ├── tools.py              #   13 controlled tools (fixed, parameterised SQL)
│   ├── sql_guard.py          #   5-layer read-only SQL validation / execution
│   ├── nl2sql.py             #   rule-based text-to-SQL (offline mode)
│   ├── nlp.py                #   vocabulary, period / number / medicine-name matching
│   ├── analytics.py          #   forecasting + anomaly detection (pandas)
│   ├── safety.py             #   clinical-advice boundary, PII redaction
│   ├── permissions.py        #   role → tables / columns / rows / pages
│   ├── llm.py                #   optional Anthropic / OpenAI-compatible providers
│   ├── exports.py            #   Excel / CSV reports
│   ├── evaluate.py           #   evaluation harness
│   ├── routes.py · db.py · config.py
│   └── rag/                  #   ingest.py · chunker.py · store.py · retriever.py · answer.py · text.py
├── knowledge_base/           # NEW: 6 sample leaflets + 6 sample SOPs (demo content)
├── evaluation/               # NEW: sql_eval.json · rag_eval.json · safety_eval.json
├── tests/                    # NEW: 89 tests
├── docs/                     # NEW: TECHNICAL_GUIDE · USER_GUIDE (md + pdf), legacy/ (original guides)
├── templates/                # original 12 pages + ai_chat · ai_knowledge · ai_insights · ai_audit · ai_evaluation
├── static/ (css/ai.css, js/ai.js added)
├── requirements*.txt · .env.example · .gitignore
└── instance/pharmacy.db      # SQLite (auto-created / upgraded)
```

---

## ⚙️ Configuration

Original constants in `app.py` (`LOW_STOCK_THRESHOLD = 10`, `EXPIRY_WARN_DAYS = 30`) are reused by PharmaIQ so the AI and the Alerts page always agree. All other settings are environment variables — see `.env.example` and the Technical Guide §11.

## 🔒 Security notes for production

1. Set a strong `SECRET_KEY` (now read from the environment) and change every default password.
2. The legacy `/api/*` endpoints still accept `X-API-Key: <secret key>`; rotate or remove that if you don't use it.
3. Run behind HTTPS (nginx / Caddy) and set `debug=False` in `app.py`.
4. Documents you upload are sent to an external LLM **only if** you enable one; with `PHARMAIQ_LLM_MAX_ROWS=0` no database rows leave your server.
5. Treat all AI output as decision support. Clinical decisions need a pharmacist.

## 🧯 Troubleshooting

| Symptom | Fix |
|---|---|
| Expiry / sales answers are empty | The shipped demo dates are old → run `python seed_ai_demo.py` |
| “Knowledge Base is empty” | Click **Load sample documents** (admin) or run `python seed_ai_demo.py` |
| `ModuleNotFoundError` | Activate the venv and `pip install -r requirements.txt` |
| PDF upload fails with “No selectable text” | The PDF is a scan — OCR it first |
| `sentence-transformers unavailable` banner | Optional package missing; PharmaIQ fell back to TF-IDF automatically |

---

*Sample leaflets and SOPs are demonstration content, not clinical references.*

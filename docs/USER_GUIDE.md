# PharmaCare + PharmaIQ — User Guide

**How to add your data, what to type, and a tour of every page.**

Written for pharmacy staff, pharmacists and managers — no programming needed.

---

## Contents

1. [Getting started](#1-getting-started)
2. [Adding your data (the inputs)](#2-adding-your-data-the-inputs)
3. [Website tour — page by page](#3-website-tour--page-by-page)
4. [Ask PharmaIQ — question cookbook](#4-ask-pharmaiq--question-cookbook)
5. [How to read an answer](#5-how-to-read-an-answer)
6. [Follow-up questions](#6-follow-up-questions)
7. [What PharmaIQ will not do](#7-what-pharmaiq-will-not-do)
8. [Who can do what](#8-who-can-do-what)
9. [10-minute demo script](#9-10-minute-demo-script)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Getting started

### 1.1 Start the app

```bash
cd PharmaCare-PharmaIQ
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
python seed_ai_demo.py              # recommended: demo sales, fresh expiry dates, sample documents
python app.py
```

Open **http://127.0.0.1:5000**.

> **Why run `seed_ai_demo.py`?** It makes the demo *fresh*: it moves medicine expiry dates so some are expired and some expire soon, adds ~120 days of sales (needed for forecasts and anomaly detection), and loads 12 sample documents. Without it the AI still works, but expiry/sales questions may return “nothing found” because the original demo dates are old. It is safe to re-run; `python seed_ai_demo.py --reset` removes only the sales it created.

### 1.2 Log in

| Role | Username | Password | Use it to see… |
|---|---|---|---|
| Admin | `admin` | `admin123` | everything |
| Pharmacist | `pharmacist` | `pharma123` | everything except audit, evaluation, document deletion |
| Staff | `staff` | `staff123` | the limited view — great for testing permissions |

Open a second browser (or a private window) to compare roles side by side.

### 1.3 Find the AI features

After login, the left sidebar has a new section **PHARMAIQ · AI**:

| Link | What it is | Who sees it |
|---|---|---|
| ✨ **Ask PharmaIQ** | chat — ask in plain English | everyone |
| 📖 **Knowledge Base** | leaflets & SOPs the AI can cite | everyone (upload: pharmacist/admin) |
| 📈 **AI Insights** | forecast, anomalies, reorder list | admin, pharmacist |
| 🛡 **AI Audit Trail** | log of every AI question | admin |
| ✅ **AI Evaluation** | one-click quality scorecard | admin |

---

## 2. Adding your data (the inputs)

PharmaIQ answers from **three kinds of input**. Better input → better answers.

### 2.1 Medicines (inventory)

**Sidebar → Medicines → “Add Medicine”.** (Admin / Pharmacist.)

| Field | Required | What to enter | Why PharmaIQ cares |
|---|:-:|---|---|
| Medicine Name | ✅ | e.g. `Paracetamol 500mg` | the name PharmaIQ matches in questions (first word, e.g. *paracetamol*, is enough; small typos are tolerated) |
| Medicine Type | ✅ | `Tablet`, `Capsule`, `Syrup`, `Injection`, `Inhaler`… | this is the **category** — “show only tablets” uses it. Keep spelling consistent |
| Company Name | | `Cipla Ltd` | “medicines from Cipla” |
| Reference No / Lot No | | batch identifiers | lot appears as **batch** in expiry answers |
| Issue Date / **Expiry Date** | ✅ | `YYYY-MM-DD` date pickers | expiry questions and alerts; PharmaIQ flags expiry before issue date |
| Price | ✅ | number | stock value, “priced above $20” |
| Quantity | ✅ | whole number | stock, low-stock (≤ 10 by default) |
| Dosage / Uses / Side Effects / Precautions | | free text | used only as a **fallback**, labelled “internal record — not an approved leaflet”, when no document matches |
| Barcode | | digits | duplicate barcodes are flagged as data-quality issues |
| Image | | JPG/PNG ≤ 5 MB | not used by AI |

Tips
* One row = one **batch**. Two batches of the same medicine = two rows (PharmaIQ lists them separately).
* Never leave the expiry date blank — blank expiry is reported as a data issue.
* Bulk loading: `python seed_data.py` loads 30 sample medicines; the REST API (`POST /api/medicines`) can load more.

### 2.2 Sales

**Sidebar → Sales → “New Sale”** — choose the medicine, type a quantity (the page shows stock, unit price and total), save. The app reduces stock automatically and records who sold it.

* PharmaIQ’s sales answers, forecasts and reorder suggestions all come from these records.
* **Forecasts need history:** at least **14 days** of sales; 28+ days is better (weekday patterns), 56+ is best. `seed_ai_demo.py` provides 120 days.
* Each person’s sales are tagged with their username — **staff users can only ask about their own sales.**

### 2.3 Documents (leaflets & SOPs) — the knowledge input

**Sidebar → Knowledge Base → “Upload a document”** (Pharmacist / Admin).

| Setting | Options |
|---|---|
| File | `.pdf`, `.docx`, `.txt`, `.md` — up to **5 MB** |
| Title | optional (auto-detected from the first heading) |
| Type | Leaflet (medicine information) · SOP (procedure/policy) · Reference |
| Visible to | **All staff** · **Pharmacists+** · **Admins only** (admin-only documents are invisible to everyone else, even in AI answers) |

What makes a document work well:
* **Use real text.** Scanned PDFs (photos of paper) are rejected — run OCR first.
* **Use headings.** Word “Heading 1/2” styles or Markdown `#` headings become the *section* shown in citations.
* **One topic per document** (one leaflet per medicine; one SOP per procedure) — citations are then precise.
* **Put the medicine name in the title** (“Metformin 500 mg Tablets — Product Information”). Questions that name a medicine are matched to that leaflet.
* Upload only **approved, current** material. PharmaIQ quotes it; it does not check it clinically.
* Uploading the same file twice is detected and ignored.

**Try it** — save this as `visitor_policy.md` and upload as *SOP / All staff*:

```markdown
# SOP-07 Visitor Policy

## Reception
All visitors must sign the register at reception and wear a visitor badge.

## Restricted areas
Visitors are never allowed in the dispensary or the controlled drugs cabinet area.
```

Then ask PharmaIQ: **“Where must visitors sign in?”** — you should get the sentence with a numbered source *SOP-07 Visitor Policy*.

**Sample documents** — 12 demo files live in the `knowledge_base/` folder (6 leaflets, 6 SOPs: returns, expired stock, cold chain, reorder, controlled medicines, counselling scope). Load them with **Knowledge Base → “Load sample documents”** (admin) or `python seed_ai_demo.py`. They are *demonstration content*, not clinical references.

### 2.4 The questions (your main input)

Type naturally — full sentences, short phrases, or keywords all work. Names don’t need exact spelling. You can include a number (`next 45 days`, `fewer than 20 units`), a category (`tablets`), a company (`Cipla`), a period (`this month`, `last 7 days`, `yesterday`) or a date (`expiring before 2027-01-31`). See section 4.

---

## 3. Website tour — page by page

### 3.1 Original PharmaCare pages

| Page | What you do there |
|---|---|
| **Dashboard** | totals, stock value, 7-day sales chart, alerts at a glance |
| **Medicines** | search, add / edit / delete, view details, scan barcode, pagination |
| **Barcode Scanner** | scan with the camera to find a medicine instantly |
| **Alerts** | three lists: expiring ≤ 30 days · already expired · low stock ≤ 10 |
| **Sales** | record a sale, see sales history |
| **Reports** *(admin, pharmacist)* | totals by type, expiry list, 30-day sales summary |
| **Users / Activity Logs** *(admin)* | manage accounts; see who did what |
| **Export Excel** | colour-coded inventory workbook (red = expired, yellow = low stock) |
| **Profile** | change your own password |

PharmaIQ uses the **same rules** as the Alerts page (low stock ≤ 10, 30-day expiry window), so the AI and the page agree.

### 3.2 ✨ Ask PharmaIQ

* **Chat box** at the bottom: type and press Enter.
* **“Try asking” chips** on the right: click one to ask it instantly.
* **Mode badge** (top right) shows how questions are planned: `offline (rule-based)` by default, or `anthropic:…` / `openai-compatible:…` if an LLM is configured.
* **New chat** clears the conversation (follow-ups start fresh).

Each answer can contain:

| Block | Colour | Meaning |
|---|---|---|
| **Database facts · as of DATE** | green | numbers computed from your real data, a chart, and the result table |
| **Show query** | grey button | the exact SQL that produced the table — nothing hidden |
| **Excel / CSV** | green buttons | download the result (the Excel file also records the question, date and SQL) |
| **From documents** + **Sources** | blue | text taken from leaflets/SOPs with numbered citations `[1]`, `[2]` |
| **Explanation** | purple | short commentary, labelled *rule-based* or *AI-generated* — it is never the source of the numbers |
| **Warnings / assumptions** | amber / grey | defaults that were applied (e.g. “assumed last 30 days”), caveats such as “forecasts are estimates” |
| **Follow-up chips** | pills | click to continue the conversation |

### 3.3 📖 Knowledge Base

* Table of documents: title, type, who can see it, number of searchable passages, date.
* **Upload** (pharmacist/admin), **Load sample documents** and **delete** (admin).
* **Retrieval backend** line at the top (`tfidf` by default). If an optional upgrade failed to load, an amber note explains why.
* **Test retrieval** box: type a query and see which passages match and *why* (keyword score, vector score, coverage; ✔ = counted as relevant, ✖ = too weak to cite). Use it to check whether a new document is findable.

### 3.4 📈 AI Insights *(admin, pharmacist)*

| Panel | How to use | How to read it |
|---|---|---|
| **Demand forecast** | pick a medicine (or “All”) and 7 / 14 / 30 days → *Forecast* | grey line = actual last 30 days; green = forecast; dashed = **80 % range**. Facts list the expected total, confidence (low/medium/high), a 7-day back-test, and days of cover for one medicine. **A wide band = low certainty.** If history is too short it says so instead of guessing |
| **Anomalies & data quality** | automatic; *Refresh* | `sales_spike` / `sales_drop` (unusually high/low days) and data issues: `sale_total_mismatch`, `sold_after_expiry`, `duplicate_barcode`, `negative_stock`, `expiry_before_issue`, `missing_expiry`, `sale_unknown_medicine`. Severity high/medium/low. These are **flags to check**, not accusations |
| **Reorder suggestions** | automatic | items at/below the low-stock level or with ≤ 14 days of cover (from the last 30 days’ sales), with a *suggested order quantity* — rules are printed under the table. A pharmacist should confirm before ordering |

With demo data you should see: one **sales_spike** for the medicine with the injected 70-unit day, and one **sale_total_mismatch** (a sale whose total doesn’t equal quantity × price). Those two are deliberate.

### 3.5 🛡 AI Audit Trail *(admin)*

Every AI request: time, user and role, the (privacy-redacted) question, route (`tool` / `sql` / `rag` / `refused` …), tool name, expandable SQL, row count, status (`ok`, `refused`, `denied`, `error`, `no_answer`) and latency. Filter by status. It stores **what was asked and how it was answered — never the returned rows** — and strips e-mail addresses and phone-like numbers from questions.

### 3.6 ✅ AI Evaluation *(admin)*

Click **Run evaluation** (a few seconds). You get a scorecard:
* **SQL correctness** — each test question is compared with an independent control query on your database.
* **RAG pass rate, hit@1, hit@3, keyword recall** — does the right document come first?
* **Safety** — must-refuse questions, must-not-refuse questions, and SQL attack attempts.

A healthy system shows 100 %. If you upload new documents or change data and a score drops, the failing row tells you which case to look at. You can add your own cases in `evaluation/*.json`.

---

## 4. Ask PharmaIQ — question cookbook

“Route” tells you where the answer comes from. **DB** = live database, **Doc** = documents, **Calc** = statistics.

### 4.1 Expiry

| Type this | Route | You get |
|---|---|---|
| Which medicines are expiring within the next 30 days, and what is their current stock? | DB tool | name, batch, expiry date, quantity, days left, stock value; total units and value at risk |
| What expires in the next 90 days? | DB tool | same, 90-day window |
| Medicines expiring this month | DB tool | days to month end |
| Show already expired medicines | DB tool | expired batches + days overdue + a reminder to follow the SOP |
| Tablets expiring in the next 60 days | DB tool | filtered by category |
| Medicines expiring before 2027-03-31 | DB (text-to-SQL) | custom date filter (also lists batches that already expired — the assumptions line says so) |

### 4.2 Stock

| Type this | Route | You get |
|---|---|---|
| Show low stock items | DB tool | items ≤ 10 units (rule shown) |
| Show all products with fewer than 10 units | DB (text-to-SQL) | strict “< 10” — compare with the line above |
| What is out of stock? | DB tool | threshold 0 |
| How much stock of amoxicillin do we have? | DB tool | per batch + status (OK / Low / Expiring soon / Expired) |
| How many tablets do we have? | DB (text-to-SQL) | a count |
| Medicines priced above $20 | DB (text-to-SQL) | list |
| Most expensive capsules | DB (text-to-SQL) | sorted list |
| Total value by category · inventory value by company | DB tool | stock value table + chart |
| Medicines from Cipla with less than 50 units | DB (text-to-SQL) | combined filters |

### 4.3 Sales

| Type this | Route | You get |
|---|---|---|
| Which categories generated the highest sales this month? | DB tool | revenue per category + bar chart |
| Top 5 best selling medicines last month | DB tool | units, revenue |
| Sales by day last 7 days | DB tool | line chart |
| Total sales this year · sales today | DB tool | transactions, units, revenue |
| Sales of paracetamol last month | DB (text-to-SQL) | daily units and revenue |
| Who sold the most this month? | DB tool | per seller *(staff see only themselves)* |
| Give me a management summary | DB tool | 5 tables: inventory KPIs, low stock, expiring, sales by category, top sellers — downloadable as a multi-sheet Excel report |

### 4.4 Intelligence *(admin / pharmacist)*

| Type this | Route | You get |
|---|---|---|
| Forecast demand for the next 14 days | Calc | expected units with 80 % range, confidence, back-test |
| Forecast demand for metformin next 2 weeks | Calc | plus days of cover for that medicine (needs recent sales for that item — a medicine that stopped selling forecasts ≈ 0) |
| Any unusual sales spikes? / Find anomalies | Calc + DB | spikes, drops, data-quality findings |
| What should we reorder? | Calc + DB | days of cover and suggested quantities |

### 4.5 Documents

| Type this | Route | You get (with sample documents) |
|---|---|---|
| What is the return policy? | Doc | SOP-01 passages, cited |
| How should insulin be stored? | Doc | refrigerate 2–8 °C, do not freeze, in-use pen ≤ 28 days — from the insulin leaflet |
| What are the side effects of metformin? | Doc | nausea, diarrhoea… from the metformin leaflet only |
| What is the procedure for expired stock? | Doc | quarantine, register, disposal (SOP-02) |
| What temperature should the pharmacy fridge be? | Doc | SOP-03 (“fridge” = “refrigerator”) |
| What are the warnings for warfarin? | Doc | bleeding, INR monitoring, interactions |
| Who can give advice on which medicine to take? | Doc | SOP-06: only a registered pharmacist |
| paracetmol overdose liver | Doc | typo tolerated → paracetamol leaflet |

If nothing relevant is found you get an honest *“I couldn’t find this… I won’t guess”* instead of an invented answer.

### 4.6 Questions it should decline

See section 7.

---

## 5. How to read an answer

1. **Check the “as of” date** next to *Database facts*. It is today’s date, taken at the moment you asked.
2. **Read the assumptions** (grey bullets): the period used, the low-stock rule, the forecast model.
3. **Look at the table and, if in doubt, click Show query.** The table is real data; the SQL shows exactly what was asked of the database.
4. **Treat the purple Explanation as a hint.** It may be rule-based or AI-written; it is checked so it cannot contain numbers that aren’t in the data, but it is not evidence.
5. **For document answers, check the Sources** — the bold title, section and page tell you where the sentence came from. Open the original document for anything important.
6. **For forecasts, read the range and the confidence**, not just the headline number.
7. **Anything clinically consequential needs a pharmacist.**

---

## 6. Follow-up questions

PharmaIQ remembers the last result *within the same chat*.

| Start with | Then say | What happens |
|---|---|---|
| Which categories generated the highest sales this month? | **Now show only the products in this category** | “this category” = the top category from the table → shows the inventory products in it |
| Show all products with fewer than 50 units | **Now only tablets** | same query + `category = Tablet` |
| Show low stock items | **Now only capsules** · **Now fewer than 5 units** | refines the filter (“fewer than 5” = 4 or less) |
| Expiring in the next 30 days | **and for 60 days** · **only syrups** | changes the window / filter |
| Sales by category this month | **for last month** · **by day** | changes the period / grouping |
| How should insulin be stored? | **And what are the warnings?** | remembers “insulin” |

Follow-up answers are tagged “follow-up”. Click **New chat** to reset. Conversations are private to your account.

---

## 7. What PharmaIQ will not do

PharmaIQ is a **reference and analytics tool**. It deliberately refuses:

| Request | Example | Why |
|---|---|---|
| Personal dosing | “My son has a fever, how much paracetamol can I give him?” · “What dose for a 20 kg child?” | patient-specific dosing is a clinical decision |
| Personal safety/interaction checks | “Can I take ibuprofen with warfarin?” · “I’m pregnant, is metformin safe?” | needs a pharmacist who knows the patient |
| Diagnosis / treatment picking | “What medicine should I take for my headache?” | not its role |
| Credentials | “Show me the admin password” | secrets are never shown |

You’ll see a red box explaining this and what it *can* do (e.g. “What does the leaflet say about warfarin warnings?”).

It also **won’t**: change any data (it is read-only — to add or edit medicines use the normal pages), show other users’ private data, answer outside its documents from general knowledge (“I won’t guess”), or show columns/tables your role isn’t allowed to see (you’ll get “Your role (staff) is not allowed to read …”).

---

## 8. Who can do what

| Feature | Admin | Pharmacist | Staff |
|---|:-:|:-:|:-:|
| Ask about stock / expiry / inventory value | ✅ | ✅ | ✅ |
| Ask about sales | all sales | all sales | **own sales only** |
| Management summary | ✅ | ✅ | ✅ (own sales) |
| Forecast, anomalies, reorder suggestions | ✅ | ✅ | ❌ (“not allowed” message) |
| Search documents | all | up to pharmacist-level | staff-level only |
| Upload documents | ✅ | ✅ | ❌ |
| Delete documents / load samples | ✅ | ❌ | ❌ |
| AI Insights page | ✅ | ✅ | ❌ |
| Audit trail · Evaluation | ✅ | ❌ | ❌ |
| Download Excel/CSV of **own** results | ✅ | ✅ | ✅ |

**Quick permission test:** log in as `staff`, ask *“Show sales by seller this month”* → only your own line. Ask *“Forecast demand”* → refused. Log in as `admin`, ask the same → full results.

---

## 9. 10-minute demo script

1. **Login as admin**, open *Dashboard* (30 s) — “this is the existing system.”
2. **Ask PharmaIQ → “Which medicines are expiring within the next 30 days, and what is their current stock?”** Point out: green *Database facts*, chart, table, **Show query**, Excel download.
3. **“Show all products with fewer than 10 units”** → click *Show query*: “the system generated a read-only SELECT and validated it.”
4. **“Which categories generated the highest sales this month?”** → then **“Now show only the products in this category”** (follow-up).
5. **“What is the return policy?”** then **“How should insulin be stored?”** → blue *From documents* with numbered sources: “live data comes from the DB, policies from documents.”
6. **“Who won the football world cup?”** → *“I won’t guess.”*
7. **“My son has a fever, how much paracetamol can I give him?”** → red refusal.
8. **AI Insights** → forecast with the dashed range; anomalies list (the injected spike and wrong total).
9. **AI Audit Trail** → show the questions, SQL and statuses logged.
10. **AI Evaluation → Run** → scorecard. Then log in as **staff** (private window) and repeat steps 2 and “Forecast demand” to show role limits.

---

## 10. Troubleshooting

| Problem | Likely cause → fix |
|---|---|
| “No medicines expire within…” / sales answers empty | the original demo dates are old → run `python seed_ai_demo.py` |
| Knowledge Base is empty / document questions say “I couldn’t find this” | click **Load sample documents** (admin) or upload documents |
| Uploaded PDF rejected: “No selectable text found” | it is a scan → OCR it, or upload a DOCX/TXT/MD version |
| Uploaded file says “already in the knowledge base” | identical file was uploaded before |
| My document isn’t cited | use **Test retrieval** with your question; add the medicine/topic name to the **title**, use headings, keep one topic per document |
| “Security token missing or expired” | reload the page (session expired) and try again |
| “Your role (staff) is not allowed to read …” | permission limit by design — ask an admin/pharmacist |
| Forecast says “Not enough sales history” | needs ≥ 14 days of sales for that medicine; try “All medicines” or add data |
| Question answered by documents when you wanted data (or vice-versa) | rephrase with the data words (“how many”, “stock”, “expiring”, “sales”) or the document words (“policy”, “procedure”, “side effects”, “stored”) |
| Mode badge says *offline (rule-based)* | normal. To use an LLM, set `PHARMAIQ_LLM_PROVIDER` and an API key (see `.env.example`) and restart |
| Amber banner “sentence-transformers unavailable” | optional upgrade not installed; the app fell back to the built-in search — nothing is broken |
| Page not found / blank after update | restart `python app.py`; the new tables are created automatically on start |

Need more detail? See **TECHNICAL_GUIDE.md**.

---

*Sample leaflets and SOPs are demonstration content. PharmaIQ provides reference information and data analytics, not medical advice.*

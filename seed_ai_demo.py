"""
seed_ai_demo.py — OPTIONAL demo data for PharmaIQ (forecasting, anomaly detection, sales questions).

What it does (all relative to TODAY, so the demo is always "fresh"):
  1. Re-bases medicine expiry dates so some are expired, some expire soon, most are fine
     (the original seed_data.py dates go stale as time passes).
  2. Adds ~120 days of realistic synthetic sales (weekday pattern, trend, noise) spread across
     admin / pharmacist / staff users.
  3. Injects TWO deliberate anomalies so the anomaly detector has something to find:
        • a one-day sales spike for one medicine
        • one sale row whose total_price does not equal quantity × unit_price
  4. Loads the sample leaflets / SOPs from ./knowledge_base into the Knowledge Base.

Usage:
    python seed_ai_demo.py            # add demo data
    python seed_ai_demo.py --reset    # remove ONLY the sales rows this script created

Requires the database to exist (run `python app.py` once, and `python seed_data.py` for medicines).
"""
import glob
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta

DB_PATH = os.path.join("instance", "pharmacy.db")
DAYS = 120


def connect():
    if not os.path.exists(DB_PATH):
        sys.exit("❌ Database not found. Run `python app.py` once (then `python seed_data.py`) first.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS ai_demo_rows (sale_id INTEGER PRIMARY KEY)")
    return conn


def reset(conn):
    ids = [r[0] for r in conn.execute("SELECT sale_id FROM ai_demo_rows")]
    conn.executemany("DELETE FROM sales WHERE id=?", [(i,) for i in ids])
    conn.execute("DELETE FROM ai_demo_rows")
    conn.commit()
    print(f"🧹 Removed {len(ids)} demo sales rows.")


def rebase_expiry(conn, today):
    meds = conn.execute("SELECT id FROM medicines ORDER BY id").fetchall()
    if not meds:
        sys.exit("❌ No medicines found. Run `python seed_data.py` first.")
    offsets = [-40, -12, 6, 14, 22, 27] + [90, 150, 210, 300, 420, 540, 700]
    for i, m in enumerate(meds):
        off = offsets[i % len(offsets)] if i < 14 else offsets[6 + (i % 7)]
        conn.execute("UPDATE medicines SET exp_date=? WHERE id=?", ((today + timedelta(days=off)).isoformat(), m["id"]))
    print(f"📅 Re-based expiry dates for {len(meds)} medicines (some expired, some expiring soon).")


def add_sales(conn, today):
    rng = random.Random(42)
    meds = conn.execute("SELECT id, medicine_name, price, exp_date FROM medicines WHERE price IS NOT NULL ORDER BY id").fetchall()
    sellers = ["admin", "pharmacist", "pharmacist", "staff", "staff"]
    popularity = {m["id"]: rng.choice([0.2, 0.35, 0.5, 0.8, 1.2, 2.0]) for m in meds}
    created, spike_med = 0, next((x for x in meds if not x["exp_date"] or x["exp_date"] > today.isoformat()), meds[0])
    for d in range(DAYS, 0, -1):
        day = today - timedelta(days=d)
        weekend = 1.35 if day.weekday() >= 5 else 1.0
        trend = 1 + 0.004 * (DAYS - d)
        for m in meds:
            if m["exp_date"] and day.isoformat() > m["exp_date"]:
                continue                                       # never "sell" expired stock in the demo data
            lam = popularity[m["id"]] * weekend * trend
            force_spike = (d == 6 and m["id"] == spike_med["id"])
            if force_spike or rng.random() < min(0.9, lam / 2.2):
                qty = max(1, int(rng.gauss(lam * 2.2, 1.2)))
                if d == 6 and m["id"] == spike_med["id"]:
                    qty = 70                                   # ← injected spike
                ts = datetime.combine(day, datetime.min.time()) + timedelta(hours=rng.randint(8, 20), minutes=rng.randint(0, 59))
                total = round(qty * m["price"], 2)
                cur = conn.execute(
                    "INSERT INTO sales (medicine_id, medicine_name, quantity, unit_price, total_price, sold_by, sale_date) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (m["id"], m["medicine_name"], qty, m["price"], total, rng.choice(sellers), ts.strftime("%Y-%m-%d %H:%M:%S")))
                conn.execute("INSERT OR IGNORE INTO ai_demo_rows VALUES (?)", (cur.lastrowid,))
                created += 1
    # injected inconsistency: wrong total
    m = spike_med if spike_med is not None else meds[0]
    cur = conn.execute(
        "INSERT INTO sales (medicine_id, medicine_name, quantity, unit_price, total_price, sold_by, sale_date) VALUES (?,?,?,?,?,?,?)",
        (m["id"], m["medicine_name"], 4, m["price"], round(4 * m["price"] + 37.5, 2), "staff",
         (datetime.combine(today - timedelta(days=3), datetime.min.time()) + timedelta(hours=11)).strftime("%Y-%m-%d %H:%M:%S")))
    conn.execute("INSERT OR IGNORE INTO ai_demo_rows VALUES (?)", (cur.lastrowid,))
    print(f"🛒 Added {created + 1} demo sales rows over {DAYS} days (incl. 1 spike for '{spike_med['medicine_name']}' and 1 wrong-total row).")


def load_knowledge():
    from pharmaiq.db import init_ai_tables
    from pharmaiq.rag import store
    init_ai_tables(DB_PATH)
    n = 0
    for f in sorted(glob.glob(os.path.join("knowledge_base", "*"))):
        if not f.lower().endswith((".md", ".txt", ".docx", ".pdf")):
            continue
        base = os.path.basename(f)
        with open(f, "rb") as fh:
            data = fh.read()
        r = store.add_document(DB_PATH, base, data, doc_type="sop" if base.startswith("sop_") else "leaflet",
                               min_role="staff", uploaded_by="seed")
        n += 0 if r["duplicate"] else 1
    print(f"📚 Loaded {n} new document(s) into the Knowledge Base.")


if __name__ == "__main__":
    conn = connect()
    if "--reset" in sys.argv:
        reset(conn)
        sys.exit(0)
    today = date.today()
    reset(conn)
    rebase_expiry(conn, today)
    add_sales(conn, today)
    conn.commit()
    conn.close()
    load_knowledge()
    print("\n✅ Done. Start the app (python app.py), log in as admin / admin123 and open  PharmaIQ → Ask PharmaIQ.")

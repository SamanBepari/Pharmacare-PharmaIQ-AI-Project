"""Shared fixture: a small, deterministic pharmacy database + the sample knowledge base."""
import glob
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TODAY = date(2026, 6, 15)
os.environ["PHARMAIQ_TODAY"] = TODAY.isoformat()
os.environ.pop("PHARMAIQ_LLM_PROVIDER", None)

from werkzeug.security import generate_password_hash  # noqa: E402

SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff', full_name TEXT, email TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE medicines (id INTEGER PRIMARY KEY AUTOINCREMENT, ref_no TEXT, company_name TEXT, medicine_type TEXT,
  medicine_name TEXT NOT NULL, lot_no TEXT, issue_date TEXT, exp_date TEXT, uses TEXT, side_effect TEXT, precautions TEXT,
  dosage TEXT, price REAL, product_qty INTEGER DEFAULT 0, image_filename TEXT, barcode TEXT,
  created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE activity_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, action TEXT, details TEXT,
  ip_address TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, medicine_id INTEGER, medicine_name TEXT, quantity INTEGER,
  unit_price REAL, total_price REAL, sold_by TEXT, sale_date TEXT DEFAULT (datetime('now')));
"""

# name, type, company, qty, price, expiry offset (days from TODAY), barcode
MEDS = [
    ("Paracetamol 500mg", "Tablet", "Sun Pharma", 150, 5.5, 400, "111"),
    ("Amoxicillin 500mg", "Capsule", "Cipla Ltd", 200, 12.0, 500, "112"),
    ("Cetirizine 5mg/5ml", "Syrup", "Dr Reddy's", 80, 8.75, 12, "113"),      # expiring soon
    ("Metformin 500mg", "Tablet", "Abbott India", 300, 15.0, 700, "114"),
    ("Insulin Glargine 100U/ml", "Injection", "Pfizer", 50, 45.0, 300, "115"),
    ("Warfarin 5mg", "Tablet", "Sun Pharma", 3, 30.0, 400, "116"),            # low stock
    ("Salbutamol 100mcg", "Inhaler", "Cipla Ltd", 5, 35.0, 350, "117"),       # low stock
    ("Ibuprofen 400mg", "Tablet", "Mankind Pharma", 45, 6.0, -10, "118"),     # expired
]


def build_db(with_sales=True, with_kb=True) -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="pharmaiq_"), "pharmacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for u, pw, role in (("admin", "admin123", "admin"), ("pharmacist", "pharma123", "pharmacist"), ("staff", "staff123", "staff")):
        conn.execute("INSERT INTO users (username,password,role,full_name) VALUES (?,?,?,?)",
                     (u, generate_password_hash(pw), role, u.title()))
    for name, typ, comp, qty, price, off, bc in MEDS:
        conn.execute("INSERT INTO medicines (company_name,medicine_type,medicine_name,lot_no,issue_date,exp_date,price,product_qty,barcode,uses) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (comp, typ, name, "LOT-1", "2025-01-01", (TODAY + timedelta(days=off)).isoformat(), price, qty, bc, "Demo uses"))
    if with_sales:
        import random
        rng = random.Random(7)
        ids = {r[1]: r[0] for r in conn.execute("SELECT id, medicine_name FROM medicines")}
        for d in range(90, 0, -1):
            day = TODAY - timedelta(days=d)
            for name in ("Paracetamol 500mg", "Metformin 500mg", "Amoxicillin 500mg", "Cetirizine 5mg/5ml"):
                qty = max(1, int(rng.gauss(6 if name.startswith("Para") else 3, 1)) + (2 if day.weekday() >= 5 else 0))
                if d == 5 and name.startswith("Cetirizine"):
                    qty = 60                                             # injected spike
                price = conn.execute("SELECT price FROM medicines WHERE medicine_name=?", (name,)).fetchone()[0]
                conn.execute("INSERT INTO sales (medicine_id,medicine_name,quantity,unit_price,total_price,sold_by,sale_date) VALUES (?,?,?,?,?,?,?)",
                             (ids[name], name, qty, price, round(qty * price, 2), rng.choice(["admin", "pharmacist", "staff"]),
                              f"{day.isoformat()} 10:00:00"))
        conn.execute("INSERT INTO sales (medicine_id,medicine_name,quantity,unit_price,total_price,sold_by,sale_date) VALUES (?,?,?,?,?,?,?)",
                     (ids["Paracetamol 500mg"], "Paracetamol 500mg", 2, 5.5, 99.0, "staff", f"{(TODAY - timedelta(days=2)).isoformat()} 12:00:00"))
    conn.commit()
    conn.close()
    from pharmaiq.db import init_ai_tables
    init_ai_tables(path)
    if with_kb:
        from pharmaiq.rag import store
        for f in sorted(glob.glob(os.path.join(ROOT, "knowledge_base", "*.md"))):
            b = os.path.basename(f)
            with open(f, "rb") as fh:
                store.add_document(path, b, fh.read(), doc_type="sop" if b.startswith("sop_") else "leaflet")
    return path


def user(db, role):
    conn = sqlite3.connect(db)
    r = conn.execute("SELECT id, username FROM users WHERE role=?", (role,)).fetchone()
    conn.close()
    return {"id": r[0], "username": r[1], "role": role}

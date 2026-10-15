"""
seed_data.py — Run ONCE to populate PharmaCare with demo data.

Usage:
    cd pharmacy_app
    python seed_data.py

This will insert:
  - 30 medicines (various types, some expired, some low stock, some expiring soon)
  - 20 sales records
  - Activity log entries
  - 2 extra users (already seeded by app, skipped if exist)

After running, EVERY feature page will have data to display.
"""

import sqlite3
import os
from datetime import date, timedelta
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join("instance", "pharmacy.db")

if not os.path.exists(DB_PATH):
    print("❌ Database not found. Run `python app.py` first to create it, then re-run this script.")
    exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

today = date.today()

# ─────────────────────────────────────────────
#  MEDICINES — 30 entries
# ─────────────────────────────────────────────
medicines = [
    # (ref_no, company, type, name, lot, issue, exp, uses, side, precaution, dosage, price, qty, barcode)

    # ── Normal stock, valid expiry ───────────────
    ("REF001", "Sun Pharma",       "Tablet",    "Paracetamol 500mg",        "LOT-2024-01", "2024-01-10", (today+timedelta(days=400)).isoformat(), "Fever, pain relief",         "Nausea",           "Avoid alcohol",       "1 tablet every 6 hrs",    5.50,  150, "8901030000001"),
    ("REF002", "Cipla Ltd",        "Capsule",   "Amoxicillin 500mg",        "LOT-2024-02", "2024-02-01", (today+timedelta(days=500)).isoformat(), "Bacterial infections",       "Diarrhea, rash",   "Allergy check",       "1 cap 3x daily",          12.00, 200, "8901030000002"),
    ("REF003", "Dr Reddy's",       "Syrup",     "Cetirizine 5mg/5ml",       "LOT-2024-03", "2024-03-15", (today+timedelta(days=600)).isoformat(), "Allergies, hay fever",       "Drowsiness",       "Avoid driving",       "10ml once daily",          8.75,  80,  "8901030000003"),
    ("REF004", "Abbott India",     "Tablet",    "Metformin 500mg",          "LOT-2024-04", "2024-01-20", (today+timedelta(days=700)).isoformat(), "Type 2 diabetes",            "Stomach upset",    "Take with meals",     "1 tablet twice daily",    15.00, 300, "8901030000004"),
    ("REF005", "Pfizer",           "Injection", "Insulin Glargine 100U/ml", "LOT-2024-05", "2024-04-01", (today+timedelta(days=300)).isoformat(), "Diabetes management",        "Hypoglycemia",     "Refrigerate",         "As prescribed",           45.00, 50,  "8901030000005"),
    ("REF006", "GlaxoSmithKline",  "Tablet",    "Atorvastatin 10mg",        "LOT-2024-06", "2024-02-10", (today+timedelta(days=800)).isoformat(), "High cholesterol",           "Muscle pain",      "Avoid grapefruit",    "1 tablet at night",       22.00, 120, "8901030000006"),
    ("REF007", "Ranbaxy",          "Capsule",   "Omeprazole 20mg",          "LOT-2024-07", "2024-03-01", (today+timedelta(days=450)).isoformat(), "Acid reflux, ulcers",        "Headache",         "30 min before meals", "1 cap daily",             10.50, 180, "8901030000007"),
    ("REF008", "Mankind Pharma",   "Tablet",    "Amlodipine 5mg",           "LOT-2024-08", "2024-01-05", (today+timedelta(days=550)).isoformat(), "High blood pressure",        "Swollen ankles",   "Do not stop abruptly","1 tablet daily",          18.00, 90,  "8901030000008"),
    ("REF009", "Lupin Ltd",        "Syrup",     "Azithromycin 200mg/5ml",   "LOT-2024-09", "2024-05-01", (today+timedelta(days=200)).isoformat(), "Respiratory infections",     "Nausea, vomiting", "Complete course",     "10ml once daily x 3 days",25.00, 60,  "8901030000009"),
    ("REF010", "Novartis",         "Tablet",    "Losartan 50mg",            "LOT-2024-10", "2024-02-20", (today+timedelta(days=650)).isoformat(), "Hypertension",               "Dizziness",        "Monitor BP",          "1 tablet daily",          20.00, 140, "8901030000010"),

    # ── LOW STOCK (qty ≤ 10) ─────────────────────
    ("REF011", "Cipla Ltd",        "Inhaler",   "Salbutamol 100mcg",        "LOT-2024-11", "2024-03-10", (today+timedelta(days=350)).isoformat(), "Asthma, bronchospasm",       "Tremors",          "Shake before use",    "2 puffs as needed",       35.00, 5,   "8901030000011"),
    ("REF012", "Sun Pharma",       "Tablet",    "Warfarin 5mg",             "LOT-2024-12", "2024-04-15", (today+timedelta(days=400)).isoformat(), "Blood clot prevention",      "Bleeding",         "Regular INR monitoring","1 tablet daily",          30.00, 3,   "8901030000012"),
    ("REF013", "Abbott India",     "Injection", "Epinephrine 1mg/ml",       "LOT-2024-13", "2024-01-30", (today+timedelta(days=500)).isoformat(), "Anaphylaxis emergency",      "Palpitations",     "Emergency use only",  "0.3-0.5mg IM",            55.00, 8,   "8901030000013"),
    ("REF014", "Pfizer",           "Capsule",   "Fluconazole 150mg",        "LOT-2024-14", "2024-05-10", (today+timedelta(days=420)).isoformat(), "Fungal infections",          "Nausea",           "Liver function check","1 capsule single dose",   18.00, 7,   "8901030000014"),
    ("REF015", "Dr Reddy's",       "Tablet",    "Digoxin 0.25mg",           "LOT-2024-15", "2024-02-28", (today+timedelta(days=480)).isoformat(), "Heart failure",              "Toxicity at high dose","Narrow therapeutic window","0.125-0.25mg daily",28.00, 2,   "8901030000015"),

    # ── EXPIRING SOON (within 30 days) ──────────
    ("REF016", "Mankind Pharma",   "Tablet",    "Ibuprofen 400mg",          "LOT-2024-16", "2023-06-01", (today+timedelta(days=12)).isoformat(),  "Pain, inflammation",         "GI upset",         "Take with food",      "1 tablet 3x daily",        6.00, 45,  "8901030000016"),
    ("REF017", "Lupin Ltd",        "Syrup",     "Paediatric Paracetamol",   "LOT-2024-17", "2023-07-01", (today+timedelta(days=7)).isoformat(),   "Child fever",                "Overdose risk",    "Weight-based dosing", "15mg/kg every 6 hrs",      9.00, 30,  "8901030000017"),
    ("REF018", "Ranbaxy",          "Capsule",   "Doxycycline 100mg",        "LOT-2024-18", "2023-08-01", (today+timedelta(days=20)).isoformat(),  "Bacterial infections",       "Photosensitivity", "Avoid sun exposure",  "1 cap twice daily",       14.00, 55,  "8901030000018"),
    ("REF019", "GlaxoSmithKline",  "Tablet",    "Prednisolone 5mg",         "LOT-2024-19", "2023-09-01", (today+timedelta(days=15)).isoformat(),  "Inflammation, allergies",    "Immunosuppression","Taper dose slowly",   "5-60mg daily",            11.00, 70,  "8901030000019"),
    ("REF020", "Sun Pharma",       "Injection", "Dexamethasone 4mg/ml",     "LOT-2024-20", "2023-10-01", (today+timedelta(days=25)).isoformat(),  "Severe allergic reactions",  "Hyperglycemia",    "Monitor blood sugar", "4-20mg IV/IM",            40.00, 20,  "8901030000020"),

    # ── EXPIRED ──────────────────────────────────
    ("REF021", "Cipla Ltd",        "Tablet",    "Ranitidine 150mg",         "LOT-2023-01", "2022-01-01", (today-timedelta(days=60)).isoformat(),  "Acid reflux",                "Headache",         "Avoid in kidney disease","1 tablet twice daily",  7.50,  25,  "8901030000021"),
    ("REF022", "Dr Reddy's",       "Syrup",     "Bromhexine 4mg/5ml",       "LOT-2023-02", "2022-03-01", (today-timedelta(days=90)).isoformat(),  "Chest congestion, cough",    "Nausea",           "Keep away from children","5-10ml 3x daily",       6.25,  10,  "8901030000022"),
    ("REF023", "Abbott India",     "Capsule",   "Vitamin D3 60000 IU",      "LOT-2023-03", "2022-06-01", (today-timedelta(days=30)).isoformat(),  "Vitamin D deficiency",       "Hypercalcemia at high dose","Take with fatty food","1 cap weekly",         9.00,  40,  "8901030000023"),
    ("REF024", "Mankind Pharma",   "Tablet",    "Aspirin 75mg",             "LOT-2023-04", "2022-04-01", (today-timedelta(days=45)).isoformat(),  "Blood thinning, heart protection","GI bleeding","Avoid if peptic ulcer","1 tablet daily",          4.00,  60,  "8901030000024"),

    # ── MORE VARIED TYPES ────────────────────────
    ("REF025", "Pfizer",           "Cream",     "Clotrimazole 1%",          "LOT-2024-21", "2024-03-01", (today+timedelta(days=730)).isoformat(), "Fungal skin infections",     "Mild irritation",  "External use only",   "Apply twice daily",       12.00, 95,  "8901030000025"),
    ("REF026", "Novartis",         "Drops",     "Ciprofloxacin Eye Drops",  "LOT-2024-22", "2024-04-01", (today+timedelta(days=365)).isoformat(), "Eye infections",             "Stinging",         "Do not touch dropper", "1-2 drops 4x daily",     15.00, 65,  "8901030000026"),
    ("REF027", "Lupin Ltd",        "Tablet",    "Pantoprazole 40mg",        "LOT-2024-23", "2024-05-01", (today+timedelta(days=600)).isoformat(), "GERD, peptic ulcer",         "Headache",         "30 min before breakfast","1 tablet daily",        16.00, 110, "8901030000027"),
    ("REF028", "Sun Pharma",       "Sachet",    "ORS (Oral Rehydration)",   "LOT-2024-24", "2024-02-01", (today+timedelta(days=900)).isoformat(), "Dehydration, diarrhea",      "None known",       "Mix with clean water","1 sachet in 200ml water",  2.00, 500, "8901030000028"),
    ("REF029", "Ranbaxy",          "Tablet",    "Metronidazole 400mg",      "LOT-2024-25", "2024-03-20", (today+timedelta(days=480)).isoformat(), "Anaerobic bacterial infections","Metallic taste","Avoid alcohol completely","1 tablet 3x daily",   8.00,  85,  "8901030000029"),
    ("REF030", "GlaxoSmithKline",  "Capsule",   "Multivitamin Complex",     "LOT-2024-26", "2024-04-10", (today+timedelta(days=800)).isoformat(), "Nutritional supplement",     "None known",       "Take after meals",    "1 capsule daily",          7.00, 250, "8901030000030"),
]

c.execute("DELETE FROM medicines")  # clear existing
c.executemany("""
    INSERT INTO medicines
    (ref_no, company_name, medicine_type, medicine_name, lot_no,
     issue_date, exp_date, uses, side_effect, precautions,
     dosage, price, product_qty, barcode)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", medicines)
print(f"✅ Inserted {len(medicines)} medicines")

# ─────────────────────────────────────────────
#  SALES — 20 records
# ─────────────────────────────────────────────
c.execute("DELETE FROM sales")

from datetime import datetime
sale_records = []
sale_data = [
    (1,  "Paracetamol 500mg",       10,  5.50,  "admin",     -6),
    (2,  "Amoxicillin 500mg",        5, 12.00,  "pharmacist",-5),
    (3,  "Cetirizine 5mg/5ml",       3,  8.75,  "staff",     -5),
    (4,  "Metformin 500mg",          8, 15.00,  "admin",     -4),
    (6,  "Atorvastatin 10mg",        4, 22.00,  "pharmacist",-4),
    (7,  "Omeprazole 20mg",          6, 10.50,  "staff",     -3),
    (1,  "Paracetamol 500mg",       15,  5.50,  "admin",     -3),
    (8,  "Amlodipine 5mg",           2, 18.00,  "pharmacist",-2),
    (9,  "Azithromycin 200mg/5ml",   4, 25.00,  "admin",     -2),
    (10, "Losartan 50mg",            3, 20.00,  "staff",     -1),
    (27, "Pantoprazole 40mg",        5, 16.00,  "pharmacist",-1),
    (28, "ORS (Oral Rehydration)",  20,  2.00,  "staff",      0),
    (30, "Multivitamin Complex",     7,  7.00,  "admin",      0),
    (25, "Clotrimazole 1%",          3, 12.00,  "pharmacist", 0),
    (2,  "Amoxicillin 500mg",        8, 12.00,  "admin",     -7),
    (4,  "Metformin 500mg",         12, 15.00,  "staff",     -7),
    (6,  "Atorvastatin 10mg",        6, 22.00,  "pharmacist",-8),
    (29, "Metronidazole 400mg",      4,  8.00,  "admin",     -8),
    (3,  "Cetirizine 5mg/5ml",       5,  8.75,  "staff",     -9),
    (7,  "Omeprazole 20mg",          9, 10.50,  "pharmacist",-9),
]

# FIX: medicines are deleted + re-inserted above, so AUTOINCREMENT ids drift (e.g. 32-61).
# Resolve each sale's medicine_id by NAME so sales never point at ids that don't exist.
_name_to_id = {r["medicine_name"]: r["id"] for r in c.execute("SELECT id, medicine_name FROM medicines")}

for (med_id, med_name, qty, unit_price, sold_by, days_offset) in sale_data:
    med_id = _name_to_id.get(med_name, med_id)
    total = round(qty * unit_price, 2)
    sale_date = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d %H:%M:%S")
    sale_records.append((med_id, med_name, qty, unit_price, total, sold_by, sale_date))

c.executemany("""
    INSERT INTO sales (medicine_id, medicine_name, quantity, unit_price, total_price, sold_by, sale_date)
    VALUES (?,?,?,?,?,?,?)
""", sale_records)
print(f"✅ Inserted {len(sale_records)} sales records")

# ─────────────────────────────────────────────
#  ACTIVITY LOGS — sample history
# ─────────────────────────────────────────────
c.execute("DELETE FROM activity_logs")

log_entries = [
    (1, "admin",       "LOGIN",          "User logged in from 127.0.0.1",                    "127.0.0.1", (datetime.now()-timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "ADD_MEDICINE",   "Added: Paracetamol 500mg",                         "127.0.0.1", (datetime.now()-timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "ADD_MEDICINE",   "Added: Amoxicillin 500mg",                         "127.0.0.1", (datetime.now()-timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")),
    (2, "pharmacist",  "LOGIN",          "User logged in from 127.0.0.1",                    "127.0.0.1", (datetime.now()-timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")),
    (2, "pharmacist",  "SALE",           "Sold 5x Amoxicillin 500mg for $60.0",              "127.0.0.1", (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")),
    (3, "staff",       "LOGIN",          "User logged in from 192.168.1.5",                  "192.168.1.5",(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")),
    (3, "staff",       "SALE",           "Sold 3x Cetirizine 5mg/5ml for $26.25",            "192.168.1.5",(datetime.now()-timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "UPDATE_MEDICINE","Updated ID:4 Metformin 500mg",                     "127.0.0.1", (datetime.now()-timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "EXPORT_EXCEL",   "Exported medicines to Excel",                      "127.0.0.1", (datetime.now()-timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")),
    (2, "pharmacist",  "ADD_MEDICINE",   "Added: Omeprazole 20mg",                           "127.0.0.1", (datetime.now()-timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "ADD_USER",       "Created user: staff2 role:staff",                  "127.0.0.1", (datetime.now()-timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")),
    (2, "pharmacist",  "SALE",           "Sold 4x Azithromycin 200mg/5ml for $100.0",       "127.0.0.1", (datetime.now()-timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")),
    (3, "staff",       "SALE",           "Sold 20x ORS (Oral Rehydration) for $40.0",        "192.168.1.5",(datetime.now()-timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "DELETE_MEDICINE","Deleted ID:31 TestMedicine",                       "127.0.0.1", (datetime.now()-timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "LOGIN",          "User logged in from 127.0.0.1",                    "127.0.0.1", (datetime.now()-timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")),
    (2, "pharmacist",  "UPDATE_MEDICINE","Updated ID:11 Salbutamol 100mcg",                  "127.0.0.1", (datetime.now()-timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")),
    (3, "staff",       "SALE",           "Sold 5x Cetirizine 5mg/5ml for $43.75",            "192.168.1.5",(datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "EXPORT_EXCEL",   "Exported medicines to Excel",                      "127.0.0.1", (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
    (1, "admin",       "SALE",           "Sold 7x Multivitamin Complex for $49.0",           "127.0.0.1", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    (2, "pharmacist",  "LOGIN",          "User logged in from 127.0.0.1",                    "127.0.0.1", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
]

c.executemany("""
    INSERT INTO activity_logs (user_id, username, action, details, ip_address, created_at)
    VALUES (?,?,?,?,?,?)
""", log_entries)
print(f"✅ Inserted {len(log_entries)} activity log entries")

conn.commit()
conn.close()

print()
print("=" * 55)
print("🎉  Seed complete! Summary:")
print("=" * 55)
print(f"  💊 Medicines   : {len(medicines):>3}  (normal, low stock, expiring, expired)")
print(f"  🛒 Sales       : {len(sale_records):>3}  (spread across 9 days)")
print(f"  📋 Log entries : {len(log_entries):>3}  (login, add, update, delete, export)")
print()
print("  Pages now populated:")
print("  ✅ Dashboard   — stats, charts, recent meds, activity feed")
print("  ✅ Medicines   — 30 items, searchable, paginated")
print("  ✅ Alerts      — expired (4), expiring soon (5), low stock (5)")
print("  ✅ Reports     — charts by category, low stock list, expiry list")
print("  ✅ Sales       — 20 transactions with revenue")
print("  ✅ Logs        — 20 activity entries (admin only)")
print("  ✅ Barcode     — scan barcode '8901030000001' to test")
print()
print("  Login at http://127.0.0.1:5000")
print("    admin / admin123")
print("=" * 55)

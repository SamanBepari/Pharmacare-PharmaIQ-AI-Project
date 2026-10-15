import os
import sqlite3
from datetime import datetime, timedelta, date
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_file, g)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import io
import json

# Optional: load settings from a .env file (see .env.example). Silent if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────
#  App Config
# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pharmacy_secret_key_2024")   # set SECRET_KEY in production
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024   # 5 MB
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
LOW_STOCK_THRESHOLD = 10
EXPIRY_WARN_DAYS = 30
DATABASE = os.path.join("instance", "pharmacy.db")

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs("instance", exist_ok=True)

# ── PharmaIQ (AI layer): adds /ai pages + /api/ai endpoints + 5 new tables ──────────
from pharmaiq import register_pharmaiq

# ─────────────────────────────────────────────
#  DB helpers
# ─────────────────────────────────────────────
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def query_db(sql, args=(), one=False, commit=False):
    db = get_db()
    cur = db.execute(sql, args)
    if commit:
        db.commit()
        return cur.lastrowid
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv


# ─────────────────────────────────────────────
#  Schema Bootstrap
# ─────────────────────────────────────────────
def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    c = db.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT    UNIQUE NOT NULL,
        password TEXT    NOT NULL,
        role     TEXT    NOT NULL DEFAULT 'staff',
        full_name TEXT,
        email    TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS medicines (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ref_no         TEXT,
        company_name   TEXT,
        medicine_type  TEXT,
        medicine_name  TEXT NOT NULL,
        lot_no         TEXT,
        issue_date     TEXT,
        exp_date       TEXT,
        uses           TEXT,
        side_effect    TEXT,
        precautions    TEXT,
        dosage         TEXT,
        price          REAL,
        product_qty    INTEGER DEFAULT 0,
        image_filename TEXT,
        barcode        TEXT,
        created_at     TEXT DEFAULT (datetime('now')),
        updated_at     TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS activity_logs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        username   TEXT,
        action     TEXT,
        details    TEXT,
        ip_address TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sales (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_id   INTEGER,
        medicine_name TEXT,
        quantity      INTEGER,
        unit_price    REAL,
        total_price   REAL,
        sold_by       TEXT,
        sale_date     TEXT DEFAULT (datetime('now'))
    );
    """)
    db.commit()

    # Seed admin if none exists
    row = db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not row:
        pw = generate_password_hash("admin123")
        db.execute("INSERT INTO users (username,password,role,full_name,email) VALUES (?,?,?,?,?)",
                   ("admin", pw, "admin", "Administrator", "admin@pharmacy.com"))
        # seed pharmacist & staff
        db.execute("INSERT INTO users (username,password,role,full_name,email) VALUES (?,?,?,?,?)",
                   ("pharmacist", generate_password_hash("pharma123"), "pharmacist",
                    "John Pharmacist", "pharma@pharmacy.com"))
        db.execute("INSERT INTO users (username,password,role,full_name,email) VALUES (?,?,?,?,?)",
                   ("staff", generate_password_hash("staff123"), "staff",
                    "Jane Staff", "staff@pharmacy.com"))
        db.commit()

    db.close()

    # PharmaIQ tables (kb_documents, kb_chunks, ai_audit_log, ai_results, ai_context) — additive only
    from pharmaiq.db import init_ai_tables
    init_ai_tables(DATABASE)


# ─────────────────────────────────────────────
#  Auth Decorators
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def roles_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                flash("Please login first.", "warning")
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                flash("Access denied. Insufficient permissions.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator

# ─────────────────────────────────────────────
#  Logging helper
# ─────────────────────────────────────────────
def log_activity(action, details=""):
    try:
        query_db(
            "INSERT INTO activity_logs (user_id,username,action,details,ip_address) VALUES (?,?,?,?,?)",
            (session.get("user_id"), session.get("username"), action, details,
             request.remote_addr),
            commit=True
        )
    except Exception:
        pass

# ─────────────────────────────────────────────
#  Utility
# ─────────────────────────────────────────────
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def get_alerts():
    today = date.today()
    warn_date = today + timedelta(days=EXPIRY_WARN_DAYS)
    expiring = query_db(
        "SELECT * FROM medicines WHERE exp_date IS NOT NULL AND exp_date != '' AND exp_date <= ? ORDER BY exp_date",
        (warn_date.isoformat(),))
    low_stock = query_db(
        "SELECT * FROM medicines WHERE product_qty <= ? ORDER BY product_qty",
        (LOW_STOCK_THRESHOLD,))
    expired = [m for m in expiring if m["exp_date"] <= today.isoformat()]
    return expiring, low_stock, expired


# ─────────────────────────────────────────────
#  Auth Routes
# ─────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        uname = request.form.get("username", "").strip()
        pw    = request.form.get("password", "")
        user  = query_db("SELECT * FROM users WHERE username=?", (uname,), one=True)
        if user and check_password_hash(user["password"], pw):
            session["user_id"]   = user["id"]
            session["username"]  = user["username"]
            session["role"]      = user["role"]
            session["full_name"] = user["full_name"]
            log_activity("LOGIN", f"User logged in from {request.remote_addr}")
            flash(f"Welcome back, {user['full_name'] or user['username']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    log_activity("LOGOUT", "User logged out")
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# ─────────────────────────────────────────────
#  Dashboard
# ─────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    total_medicines = query_db("SELECT COUNT(*) as c FROM medicines", one=True)["c"]
    total_qty       = query_db("SELECT COALESCE(SUM(product_qty),0) as s FROM medicines", one=True)["s"]
    total_value     = query_db("SELECT COALESCE(SUM(price*product_qty),0) as v FROM medicines", one=True)["v"]
    total_sales     = query_db("SELECT COALESCE(SUM(total_price),0) as s FROM sales", one=True)["s"]

    today     = date.today()
    warn_date = today + timedelta(days=EXPIRY_WARN_DAYS)
    expiring_count = query_db(
        "SELECT COUNT(*) as c FROM medicines WHERE exp_date IS NOT NULL AND exp_date != '' AND exp_date <= ?",
        (warn_date.isoformat(),), one=True)["c"]
    expired_count  = query_db(
        "SELECT COUNT(*) as c FROM medicines WHERE exp_date IS NOT NULL AND exp_date != '' AND exp_date < ?",
        (today.isoformat(),), one=True)["c"]
    low_stock_count = query_db(
        "SELECT COUNT(*) as c FROM medicines WHERE product_qty <= ?",
        (LOW_STOCK_THRESHOLD,), one=True)["c"]

    recent_meds = query_db("SELECT * FROM medicines ORDER BY id DESC LIMIT 5")
    recent_logs = query_db("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 8")

    # Sales chart – last 7 days
    chart_labels, chart_data = [], []
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        row = query_db("SELECT COALESCE(SUM(total_price),0) as s FROM sales WHERE date(sale_date)=?",
                       (d,), one=True)
        chart_labels.append((today - timedelta(days=i)).strftime("%b %d"))
        chart_data.append(float(row["s"]))

    expiring, low_stock, expired = get_alerts()

    return render_template("dashboard.html",
        total_medicines=total_medicines,
        total_qty=total_qty,
        total_value=total_value,
        total_sales=total_sales,
        expiring_count=expiring_count,
        expired_count=expired_count,
        low_stock_count=low_stock_count,
        recent_meds=recent_meds,
        recent_logs=recent_logs,
        chart_labels=json.dumps(chart_labels),
        chart_data=json.dumps(chart_data),
        expiring_soon=expiring,
        low_stock_list=low_stock,
        today=today.isoformat(),
        warn_date=warn_date.isoformat()
    )


# ─────────────────────────────────────────────
#  Medicines – CRUD
# ─────────────────────────────────────────────
@app.route("/medicines")
@login_required
def medicines():
    search = request.args.get("search", "").strip()
    page   = max(1, int(request.args.get("page", 1)))
    per    = 10
    offset = (page - 1) * per

    base_sql   = "FROM medicines"
    count_sql  = "SELECT COUNT(*) as c " + base_sql
    select_sql = "SELECT * " + base_sql

    args = []
    if search:
        like = f"%{search}%"
        where = (" WHERE medicine_name LIKE ? OR company_name LIKE ? OR"
                 " medicine_type LIKE ? OR ref_no LIKE ? OR barcode LIKE ?")
        base_sql  += where
        count_sql  = "SELECT COUNT(*) as c " + base_sql
        select_sql = "SELECT * " + base_sql
        args = [like, like, like, like, like]

    total = query_db(count_sql, args, one=True)["c"]
    meds  = query_db(select_sql + " ORDER BY id DESC LIMIT ? OFFSET ?", args + [per, offset])
    pages = (total + per - 1) // per

    today     = date.today().isoformat()
    warn_date = (date.today() + timedelta(days=EXPIRY_WARN_DAYS)).isoformat()
    expiring, low_stock, expired = get_alerts()

    return render_template("medicines.html",
        medicines=meds, search=search, page=page, pages=pages,
        total=total, per=per, today=today, warn_date=warn_date,
        low_threshold=LOW_STOCK_THRESHOLD,
        expiring_soon=expiring, low_stock_list=low_stock)

@app.route("/add_medicine", methods=["POST"])
@login_required
@roles_required("admin", "pharmacist")
def add_medicine():
    d = request.form
    image_filename = None
    if "image" in request.files:
        file = request.files["image"]
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            image_filename = filename

    query_db("""
        INSERT INTO medicines
        (ref_no,company_name,medicine_type,medicine_name,lot_no,
         issue_date,exp_date,uses,side_effect,precautions,
         dosage,price,product_qty,image_filename,barcode)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (d.get("ref_no"), d.get("company"), d.get("type"), d.get("name"),
          d.get("lot"), d.get("issue"), d.get("exp"), d.get("uses"),
          d.get("side"), d.get("precaution"), d.get("dosage"),
          d.get("price"), d.get("qty"), image_filename, d.get("barcode")),
        commit=True)
    log_activity("ADD_MEDICINE", f"Added: {d.get('name')}")
    flash(f"Medicine '{d.get('name')}' added successfully!", "success")
    return redirect(url_for("medicines"))

@app.route("/update_medicine/<int:id>", methods=["POST"])
@login_required
@roles_required("admin", "pharmacist")
def update_medicine(id):
    d = request.form
    med = query_db("SELECT * FROM medicines WHERE id=?", (id,), one=True)
    if not med:
        flash("Medicine not found.", "danger")
        return redirect(url_for("medicines"))

    image_filename = med["image_filename"]
    if "image" in request.files:
        file = request.files["image"]
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            image_filename = filename

    query_db("""
        UPDATE medicines SET
        ref_no=?,company_name=?,medicine_type=?,medicine_name=?,lot_no=?,
        issue_date=?,exp_date=?,uses=?,side_effect=?,precautions=?,
        dosage=?,price=?,product_qty=?,image_filename=?,barcode=?,
        updated_at=datetime('now')
        WHERE id=?
    """, (d.get("ref_no"), d.get("company"), d.get("type"), d.get("name"),
          d.get("lot"), d.get("issue"), d.get("exp"), d.get("uses"),
          d.get("side"), d.get("precaution"), d.get("dosage"),
          d.get("price"), d.get("qty"), image_filename, d.get("barcode"), id),
        commit=True)
    log_activity("UPDATE_MEDICINE", f"Updated ID:{id} {d.get('name')}")
    flash(f"Medicine '{d.get('name')}' updated!", "success")
    return redirect(url_for("medicines"))

@app.route("/delete_medicine/<int:id>")
@login_required
@roles_required("admin")
def delete_medicine(id):
    med = query_db("SELECT medicine_name FROM medicines WHERE id=?", (id,), one=True)
    if med:
        query_db("DELETE FROM medicines WHERE id=?", (id,), commit=True)
        log_activity("DELETE_MEDICINE", f"Deleted ID:{id} {med['medicine_name']}")
        flash("Medicine deleted.", "success")
    return redirect(url_for("medicines"))

@app.route("/medicine/<int:id>")
@login_required
def medicine_detail(id):
    med = query_db("SELECT * FROM medicines WHERE id=?", (id,), one=True)
    if not med:
        flash("Medicine not found.", "danger")
        return redirect(url_for("medicines"))
    today     = date.today().isoformat()
    warn_date = (date.today() + timedelta(days=EXPIRY_WARN_DAYS)).isoformat()
    return render_template("medicine_detail.html", med=med, today=today, warn_date=warn_date)


# ─────────────────────────────────────────────
#  Export to Excel
# ─────────────────────────────────────────────
@app.route("/export_excel")
@login_required
def export_excel():
    meds = query_db("SELECT * FROM medicines ORDER BY medicine_name")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Medicines"

    header_fill = PatternFill("solid", fgColor="1a6b3c")
    header_font = Font(bold=True, color="FFFFFF")
    headers = ["ID","Ref No","Company","Type","Medicine Name","Lot No",
               "Issue Date","Exp Date","Uses","Side Effects","Precautions",
               "Dosage","Price","Qty","Barcode","Created At"]

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font   = header_font
        cell.fill   = header_fill
        cell.alignment = Alignment(horizontal="center")

    today = date.today().isoformat()
    for row, m in enumerate(meds, 2):
        values = [m["id"], m["ref_no"], m["company_name"], m["medicine_type"],
                  m["medicine_name"], m["lot_no"], m["issue_date"], m["exp_date"],
                  m["uses"], m["side_effect"], m["precautions"], m["dosage"],
                  m["price"], m["product_qty"], m["barcode"], m["created_at"]]
        for col, val in enumerate(values, 1):
            ws.cell(row=row, column=col, value=val)
        # Red row for expired / low stock
        exp = m["exp_date"] or ""
        qty = m["product_qty"] or 0
        if exp and exp < today:
            fill = PatternFill("solid", fgColor="FFCCCC")
            for col in range(1, len(headers)+1):
                ws.cell(row=row, column=col).fill = fill
        elif qty <= LOW_STOCK_THRESHOLD:
            fill = PatternFill("solid", fgColor="FFF3CD")
            for col in range(1, len(headers)+1):
                ws.cell(row=row, column=col).fill = fill

    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    log_activity("EXPORT_EXCEL", "Exported medicines to Excel")
    return send_file(buf, download_name=f"medicines_{date.today()}.xlsx",
                     as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ─────────────────────────────────────────────
#  Alerts
# ─────────────────────────────────────────────
@app.route("/alerts")
@login_required
def alerts():
    expiring, low_stock, expired = get_alerts()
    return render_template("alerts.html", expiring=expiring,
                           low_stock=low_stock, expired=expired,
                           today=date.today().isoformat(),
                           warn_date=(date.today()+timedelta(days=EXPIRY_WARN_DAYS)).isoformat(),
                           low_threshold=LOW_STOCK_THRESHOLD)


# ─────────────────────────────────────────────
#  Reports
# ─────────────────────────────────────────────
@app.route("/reports")
@login_required
@roles_required("admin", "pharmacist")
def reports():
    today = date.today()

    # Medicines by type
    by_type = query_db("""
        SELECT medicine_type, COUNT(*) as count, COALESCE(SUM(product_qty),0) as total_qty,
               COALESCE(SUM(price*product_qty),0) as total_value
        FROM medicines GROUP BY medicine_type ORDER BY count DESC
    """)
    # Top 10 low stock
    low_stock_report = query_db(
        "SELECT * FROM medicines ORDER BY product_qty ASC LIMIT 10")
    # Expiring soon
    warn_date = (today + timedelta(days=EXPIRY_WARN_DAYS)).isoformat()
    expiring_report = query_db(
        "SELECT * FROM medicines WHERE exp_date IS NOT NULL AND exp_date != '' AND exp_date <= ? ORDER BY exp_date",
        (warn_date,))
    # Sales summary
    sales_summary = query_db("""
        SELECT date(sale_date) as day, COUNT(*) as transactions,
               SUM(quantity) as units, SUM(total_price) as revenue
        FROM sales GROUP BY day ORDER BY day DESC LIMIT 30
    """)
    total_inventory_value = query_db(
        "SELECT COALESCE(SUM(price*product_qty),0) as v FROM medicines", one=True)["v"]

    # Chart: qty by type
    type_labels = json.dumps([r["medicine_type"] or "Unknown" for r in by_type])
    type_data   = json.dumps([r["total_qty"] for r in by_type])

    return render_template("reports.html",
        by_type=by_type, low_stock_report=low_stock_report,
        expiring_report=expiring_report, sales_summary=sales_summary,
        total_inventory_value=total_inventory_value,
        today=today.isoformat(), warn_date=warn_date,
        low_threshold=LOW_STOCK_THRESHOLD,
        type_labels=type_labels, type_data=type_data)


# ─────────────────────────────────────────────
#  Activity Logs
# ─────────────────────────────────────────────
@app.route("/logs")
@login_required
@roles_required("admin")
def logs():
    page   = max(1, int(request.args.get("page", 1)))
    per    = 20
    offset = (page - 1) * per
    total  = query_db("SELECT COUNT(*) as c FROM activity_logs", one=True)["c"]
    all_logs = query_db(
        "SELECT * FROM activity_logs ORDER BY id DESC LIMIT ? OFFSET ?", (per, offset))
    pages = (total + per - 1) // per
    return render_template("logs.html", logs=all_logs, page=page, pages=pages, total=total)


# ─────────────────────────────────────────────
#  User Management (Admin only)
# ─────────────────────────────────────────────
@app.route("/users")
@login_required
@roles_required("admin")
def users():
    all_users = query_db("SELECT id,username,role,full_name,email,created_at FROM users ORDER BY id")
    return render_template("users.html", users=all_users)

@app.route("/add_user", methods=["POST"])
@login_required
@roles_required("admin")
def add_user():
    uname = request.form.get("username", "").strip()
    pw    = request.form.get("password", "")
    role  = request.form.get("role", "staff")
    fname = request.form.get("full_name", "")
    email = request.form.get("email", "")
    if not uname or not pw:
        flash("Username and password required.", "danger")
        return redirect(url_for("users"))
    existing = query_db("SELECT id FROM users WHERE username=?", (uname,), one=True)
    if existing:
        flash("Username already exists.", "danger")
        return redirect(url_for("users"))
    query_db("INSERT INTO users (username,password,role,full_name,email) VALUES (?,?,?,?,?)",
             (uname, generate_password_hash(pw), role, fname, email), commit=True)
    log_activity("ADD_USER", f"Created user: {uname} role:{role}")
    flash(f"User '{uname}' created.", "success")
    return redirect(url_for("users"))

@app.route("/delete_user/<int:id>")
@login_required
@roles_required("admin")
def delete_user(id):
    if id == session["user_id"]:
        flash("Cannot delete your own account.", "danger")
        return redirect(url_for("users"))
    u = query_db("SELECT username FROM users WHERE id=?", (id,), one=True)
    if u:
        query_db("DELETE FROM users WHERE id=?", (id,), commit=True)
        log_activity("DELETE_USER", f"Deleted user: {u['username']}")
        flash("User deleted.", "success")
    return redirect(url_for("users"))


# ─────────────────────────────────────────────
#  Sales
# ─────────────────────────────────────────────
@app.route("/sales")
@login_required
def sales():
    page   = max(1, int(request.args.get("page", 1)))
    per    = 15
    offset = (page - 1) * per
    total  = query_db("SELECT COUNT(*) as c FROM sales", one=True)["c"]
    all_sales = query_db(
        "SELECT * FROM sales ORDER BY id DESC LIMIT ? OFFSET ?", (per, offset))
    pages = (total + per - 1) // per
    meds  = query_db("SELECT id,medicine_name,price,product_qty FROM medicines ORDER BY medicine_name")
    return render_template("sales.html", sales=all_sales, page=page, pages=pages,
                           total=total, medicines=meds)

@app.route("/add_sale", methods=["POST"])
@login_required
@roles_required("admin", "pharmacist", "staff")
def add_sale():
    med_id = request.form.get("medicine_id")
    qty    = int(request.form.get("quantity", 1))
    med    = query_db("SELECT * FROM medicines WHERE id=?", (med_id,), one=True)
    if not med:
        flash("Medicine not found.", "danger")
        return redirect(url_for("sales"))
    if med["product_qty"] < qty:
        flash(f"Insufficient stock. Available: {med['product_qty']}", "warning")
        return redirect(url_for("sales"))
    total = round(med["price"] * qty, 2)
    query_db("""INSERT INTO sales (medicine_id,medicine_name,quantity,unit_price,total_price,sold_by)
                VALUES (?,?,?,?,?,?)""",
             (med_id, med["medicine_name"], qty, med["price"], total, session["username"]),
             commit=True)
    query_db("UPDATE medicines SET product_qty=product_qty-? WHERE id=?", (qty, med_id), commit=True)
    log_activity("SALE", f"Sold {qty}x {med['medicine_name']} for ${total}")
    flash(f"Sale recorded: {qty}x {med['medicine_name']} = ${total}", "success")
    return redirect(url_for("sales"))


# ─────────────────────────────────────────────
#  REST API
# ─────────────────────────────────────────────
def api_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")
        token   = request.headers.get("Authorization", "").replace("Bearer ", "")
        # Simple session-based API or token check
        if "user_id" in session:
            return f(*args, **kwargs)
        if api_key == app.secret_key or token == app.secret_key:
            return f(*args, **kwargs)
        return jsonify({"error": "Unauthorized", "message": "Login or provide X-API-Key header"}), 401
    return decorated

@app.route("/api/medicines", methods=["GET"])
@api_auth
def api_get_medicines():
    search = request.args.get("search", "")
    if search:
        like = f"%{search}%"
        meds = query_db(
            "SELECT * FROM medicines WHERE medicine_name LIKE ? OR company_name LIKE ?",
            (like, like))
    else:
        meds = query_db("SELECT * FROM medicines ORDER BY medicine_name")
    return jsonify([dict(m) for m in meds])

@app.route("/api/medicines/<int:id>", methods=["GET"])
@api_auth
def api_get_medicine(id):
    med = query_db("SELECT * FROM medicines WHERE id=?", (id,), one=True)
    if not med:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(med))

@app.route("/api/add_medicine", methods=["POST"])
@api_auth
def api_add_medicine():
    d = request.get_json()
    if not d or not d.get("medicine_name"):
        return jsonify({"error": "medicine_name is required"}), 400
    lid = query_db("""
        INSERT INTO medicines
        (ref_no,company_name,medicine_type,medicine_name,lot_no,
         issue_date,exp_date,uses,side_effect,precautions,dosage,price,product_qty,barcode)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (d.get("ref_no"), d.get("company_name"), d.get("medicine_type"),
          d["medicine_name"], d.get("lot_no"), d.get("issue_date"),
          d.get("exp_date"), d.get("uses"), d.get("side_effect"),
          d.get("precautions"), d.get("dosage"), d.get("price", 0),
          d.get("product_qty", 0), d.get("barcode")), commit=True)
    return jsonify({"success": True, "id": lid}), 201

@app.route("/api/update_medicine/<int:id>", methods=["PUT"])
@api_auth
def api_update_medicine(id):
    d = request.get_json()
    if not d:
        return jsonify({"error": "No data"}), 400
    med = query_db("SELECT * FROM medicines WHERE id=?", (id,), one=True)
    if not med:
        return jsonify({"error": "Not found"}), 404
    query_db("""UPDATE medicines SET
        ref_no=?,company_name=?,medicine_type=?,medicine_name=?,lot_no=?,
        issue_date=?,exp_date=?,uses=?,side_effect=?,precautions=?,
        dosage=?,price=?,product_qty=?,barcode=?,updated_at=datetime('now')
        WHERE id=?""",
        (d.get("ref_no", med["ref_no"]), d.get("company_name", med["company_name"]),
         d.get("medicine_type", med["medicine_type"]), d.get("medicine_name", med["medicine_name"]),
         d.get("lot_no", med["lot_no"]), d.get("issue_date", med["issue_date"]),
         d.get("exp_date", med["exp_date"]), d.get("uses", med["uses"]),
         d.get("side_effect", med["side_effect"]), d.get("precautions", med["precautions"]),
         d.get("dosage", med["dosage"]), d.get("price", med["price"]),
         d.get("product_qty", med["product_qty"]), d.get("barcode", med["barcode"]), id),
        commit=True)
    return jsonify({"success": True})

@app.route("/api/delete_medicine/<int:id>", methods=["DELETE"])
@api_auth
def api_delete_medicine(id):
    med = query_db("SELECT id FROM medicines WHERE id=?", (id,), one=True)
    if not med:
        return jsonify({"error": "Not found"}), 404
    query_db("DELETE FROM medicines WHERE id=?", (id,), commit=True)
    return jsonify({"success": True})

@app.route("/api/stats", methods=["GET"])
@api_auth
def api_stats():
    today     = date.today()
    warn_date = today + timedelta(days=EXPIRY_WARN_DAYS)
    return jsonify({
        "total_medicines": query_db("SELECT COUNT(*) as c FROM medicines", one=True)["c"],
        "total_qty":       query_db("SELECT COALESCE(SUM(product_qty),0) as s FROM medicines", one=True)["s"],
        "low_stock":       query_db("SELECT COUNT(*) as c FROM medicines WHERE product_qty<=?", (LOW_STOCK_THRESHOLD,), one=True)["c"],
        "expiring_soon":   query_db("SELECT COUNT(*) as c FROM medicines WHERE exp_date<=? AND exp_date IS NOT NULL AND exp_date!=''", (warn_date.isoformat(),), one=True)["c"],
        "expired":         query_db("SELECT COUNT(*) as c FROM medicines WHERE exp_date<? AND exp_date IS NOT NULL AND exp_date!=''", (today.isoformat(),), one=True)["c"],
    })

@app.route("/api/barcode/<barcode>", methods=["GET"])
@api_auth
def api_barcode_lookup(barcode):
    med = query_db("SELECT * FROM medicines WHERE barcode=?", (barcode,), one=True)
    if not med:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(med))


# ─────────────────────────────────────────────
#  Barcode scan page
# ─────────────────────────────────────────────
@app.route("/barcode")
@login_required
def barcode_page():
    return render_template("barcode.html")


# ─────────────────────────────────────────────
#  Profile
# ─────────────────────────────────────────────
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        fname = request.form.get("full_name", "")
        email = request.form.get("email", "")
        new_pw = request.form.get("new_password", "")
        u = query_db("SELECT * FROM users WHERE id=?", (session["user_id"],), one=True)
        if new_pw:
            cur_pw = request.form.get("current_password", "")
            if not check_password_hash(u["password"], cur_pw):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("profile"))
            query_db("UPDATE users SET full_name=?,email=?,password=? WHERE id=?",
                     (fname, email, generate_password_hash(new_pw), session["user_id"]), commit=True)
        else:
            query_db("UPDATE users SET full_name=?,email=? WHERE id=?",
                     (fname, email, session["user_id"]), commit=True)
        session["full_name"] = fname
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    user = query_db("SELECT * FROM users WHERE id=?", (session["user_id"],), one=True)
    return render_template("profile.html", user=user)


# ─────────────────────────────────────────────
#  Context processor – alerts in all templates
# ─────────────────────────────────────────────
@app.context_processor
def inject_alerts():
    if "user_id" in session:
        today     = date.today()
        warn_date = today + timedelta(days=EXPIRY_WARN_DAYS)
        exp_count = query_db(
            "SELECT COUNT(*) as c FROM medicines WHERE exp_date IS NOT NULL AND exp_date != '' AND exp_date <= ?",
            (warn_date.isoformat(),), one=True)["c"]
        ls_count  = query_db(
            "SELECT COUNT(*) as c FROM medicines WHERE product_qty <= ?",
            (LOW_STOCK_THRESHOLD,), one=True)["c"]
        return dict(nav_expiring=exp_count, nav_low_stock=ls_count)
    return dict(nav_expiring=0, nav_low_stock=0)


register_pharmaiq(app, DATABASE, LOW_STOCK_THRESHOLD, EXPIRY_WARN_DAYS)


# ─────────────────────────────────────────────
#  Run
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True, use_reloader=False)

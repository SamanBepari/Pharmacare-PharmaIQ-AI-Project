"""
Flask blueprint for PharmaIQ: pages under /ai, JSON API under /api/ai.

Security model
  * Every route needs a logged-in session (the AI layer is role-aware, so the API-key
    shortcut used by the older /api/* endpoints is NOT accepted here).
  * State-changing JSON requests need the X-CSRF-Token header; upload forms carry a token.
  * Role checks come from permissions.PAGE_ROLES.
"""
import json
import secrets
from functools import wraps

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from . import config, evaluate, exports, permissions
from .agent import PharmaIQ
from .db import connect_rw, init_ai_tables
from .llm import get_provider
from .rag import ingest, store
from .rag.retriever import Retriever
from .tools import tools_for_role

bp = Blueprint("pharmaiq", __name__)


# ── helpers ──────────────────────────────────────────────────────────
def _db():
    return current_app.config["PHARMAIQ_DB"]


def _settings():
    return config.get_settings(current_app.config.get("PHARMAIQ_LOW_STOCK"), current_app.config.get("PHARMAIQ_EXPIRY_DAYS"))


def _user():
    return {"id": session.get("user_id"), "username": session.get("username"), "role": session.get("role"),
            "full_name": session.get("full_name")}


def _agent():
    return PharmaIQ(_db(), _user(), _settings())


def csrf_token():
    if "ai_csrf" not in session:
        session["ai_csrf"] = secrets.token_urlsafe(24)
    return session["ai_csrf"]


def _check_csrf():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    return bool(sent) and secrets.compare_digest(sent, session.get("ai_csrf", ""))


def _activity(action, details):
    try:
        conn = connect_rw(_db())
        conn.execute("INSERT INTO activity_logs (user_id, username, action, details, ip_address) VALUES (?,?,?,?,?)",
                     (session.get("user_id"), session.get("username"), action, details, request.remote_addr))
        conn.commit()
        conn.close()
    except Exception:
        pass


def page_access(page):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if "user_id" not in session:
                flash("Please login first.", "warning")
                return redirect(url_for("login"))
            if not permissions.can_access_page(session.get("role"), page):
                flash("Access denied. Insufficient permissions.", "danger")
                return redirect(url_for("dashboard"))
            return f(*a, **kw)
        return wrapper
    return deco


def api_access(page, write=False):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if "user_id" not in session:
                return jsonify({"error": "Unauthorized", "message": "Please log in."}), 401
            if not permissions.can_access_page(session.get("role"), page):
                return jsonify({"error": "Forbidden", "message": "Your role cannot use this feature."}), 403
            if write and not _check_csrf():
                return jsonify({"error": "CSRF", "message": "Missing or invalid CSRF token. Reload the page."}), 400
            return f(*a, **kw)
        return wrapper
    return deco


@bp.app_context_processor
def _inject():
    return {"csrf_token": csrf_token}


# ── pages ────────────────────────────────────────────────────────────
@bp.route("/ai")
@page_access("chat")
def ai_chat():
    s = _settings()
    provider = get_provider(s)
    return render_template("ai_chat.html", provider_label=provider.label, tools=sorted(tools_for_role(session["role"])),
                           role=session["role"])


@bp.route("/ai/knowledge")
@page_access("knowledge")
def ai_knowledge():
    docs = store.list_documents(_db(), session["role"])
    retr = Retriever(_db(), session["role"], _settings())
    return render_template("ai_knowledge.html", docs=docs, backend=retr.backend_name, backend_note=retr.backend_note,
                           can_upload=permissions.can_access_page(session["role"], "knowledge_upload"),
                           can_delete=permissions.can_access_page(session["role"], "knowledge_delete"))


@bp.route("/ai/insights")
@page_access("insights")
def ai_insights():
    conn = connect_rw(_db())
    meds = conn.execute("SELECT DISTINCT medicine_name FROM medicines ORDER BY medicine_name").fetchall()
    conn.close()
    return render_template("ai_insights.html", medicines=[m[0] for m in meds])


@bp.route("/ai/audit")
@page_access("audit")
def ai_audit():
    page = max(1, request.args.get("page", 1, type=int))
    status = request.args.get("status", "").strip()
    per = 20
    conn = connect_rw(_db())
    where, args = ("WHERE status = ?", [status]) if status else ("", [])
    total = conn.execute(f"SELECT COUNT(*) FROM ai_audit_log {where}", args).fetchone()[0]
    rows = conn.execute(f"SELECT * FROM ai_audit_log {where} ORDER BY id DESC LIMIT ? OFFSET ?", args + [per, (page - 1) * per]).fetchall()
    stats = conn.execute("SELECT route, COUNT(*) c FROM ai_audit_log GROUP BY route").fetchall()
    conn.close()
    return render_template("ai_audit.html", rows=rows, page=page, pages=(total + per - 1) // per, total=total,
                           status=status, stats=stats)


@bp.route("/ai/evaluation")
@page_access("evaluation")
def ai_evaluation():
    return render_template("ai_evaluation.html")


# ── API: ask ─────────────────────────────────────────────────────────
@bp.route("/api/ai/ask", methods=["POST"])
@api_access("chat", write=True)
def api_ask():
    data = request.get_json(silent=True) or {}
    q = data.get("question")
    if not isinstance(q, str):
        return jsonify({"error": "question (string) is required"}), 400
    conv = data.get("conv_id") if isinstance(data.get("conv_id"), str) and len(data.get("conv_id")) <= 40 else None
    return jsonify(_agent().ask(q, conv))


@bp.route("/api/ai/tools")
@api_access("chat")
def api_tools():
    return jsonify([{"name": t.name, "description": t.description, "params": t.params}
                    for t in tools_for_role(session["role"]).values()])


@bp.route("/api/ai/health")
@api_access("chat")
def api_health():
    s = _settings()
    provider = get_provider(s)
    retr = Retriever(_db(), session["role"], s)
    return jsonify({"llm": provider.label, "retrieval_backend": retr.backend_name, "retrieval_note": retr.backend_note,
                    "documents": len(store.list_documents(_db(), session["role"])), "role": session["role"]})


# ── API: export ──────────────────────────────────────────────────────
@bp.route("/api/ai/export/<rid>.<fmt>")
@api_access("chat")
def api_export(rid, fmt):
    if fmt not in ("xlsx", "csv"):
        abort(404)
    conn = connect_rw(_db())
    row = conn.execute("SELECT payload, user_id FROM ai_results WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not row or row["user_id"] != session["user_id"]:
        abort(404)                       # also hides other users' result ids
    payload = json.loads(row["payload"])
    _activity("AI_EXPORT", f"Exported AI result {rid} as {fmt}")
    if fmt == "xlsx":
        return Response(exports.to_xlsx(payload), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=pharmaiq_{rid}.xlsx"})
    return Response(exports.to_csv(payload), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=pharmaiq_{rid}.csv"})


# ── API: knowledge base ──────────────────────────────────────────────
@bp.route("/api/ai/knowledge")
@api_access("knowledge")
def api_kb_list():
    return jsonify(store.list_documents(_db(), session["role"]))


@bp.route("/api/ai/knowledge/search")
@api_access("knowledge")
def api_kb_search():
    q = (request.args.get("q") or "").strip()[:300]
    if not q:
        return jsonify({"error": "q is required"}), 400
    retr = Retriever(_db(), session["role"], _settings())
    hits = retr.search(q, k=5)
    return jsonify({"backend": retr.backend_name, "hits": [
        {"doc_id": h.doc_id, "title": h.title, "page": h.page, "section": h.section, "snippet": h.text[:400],
         "bm25": round(h.bm25, 2), "vector": round(h.dense, 2), "coverage": h.coverage, "relevant": h.relevant}
        for h in hits]})


@bp.route("/ai/knowledge/upload", methods=["POST"])
@page_access("knowledge_upload")
def kb_upload():
    if not _check_csrf():
        flash("Security token missing or expired. Please try again.", "danger")
        return redirect(url_for("pharmaiq.ai_knowledge"))
    f = request.files.get("document")
    if not f or not f.filename:
        flash("Choose a file to upload.", "warning")
        return redirect(url_for("pharmaiq.ai_knowledge"))
    s = _settings()
    data = f.read()
    if len(data) > s.upload_max_mb * 1024 * 1024:
        flash(f"File too large (max {s.upload_max_mb} MB).", "danger")
        return redirect(url_for("pharmaiq.ai_knowledge"))
    min_role = request.form.get("min_role", "staff")
    if min_role == "admin" and session["role"] != "admin":
        min_role = "pharmacist"
    try:
        res = store.add_document(_db(), f.filename, data, request.form.get("title"), request.form.get("doc_type", "reference"),
                                 min_role, session["username"], s.chunk_words, s.chunk_overlap)
    except ingest.IngestError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("pharmaiq.ai_knowledge"))
    if res["duplicate"]:
        flash(f"This exact file is already in the knowledge base as “{res['title']}”.", "info")
    else:
        _activity("KB_UPLOAD", f"Uploaded document '{res['title']}' ({res['chunks']} chunks, visible to {min_role}+)")
        flash(f"“{res['title']}” added — {res['chunks']} searchable passages created.", "success")
    return redirect(url_for("pharmaiq.ai_knowledge"))


@bp.route("/ai/knowledge/delete/<int:doc_id>", methods=["POST"])
@page_access("knowledge_delete")
def kb_delete(doc_id):
    if not _check_csrf():
        flash("Security token missing or expired.", "danger")
    elif store.delete_document(_db(), doc_id):
        _activity("KB_DELETE", f"Deleted knowledge document id {doc_id}")
        flash("Document removed.", "success")
    return redirect(url_for("pharmaiq.ai_knowledge"))


@bp.route("/ai/knowledge/load-samples", methods=["POST"])
@page_access("knowledge_delete")
def kb_load_samples():
    import glob
    import os
    if not _check_csrf():
        flash("Security token missing or expired.", "danger")
        return redirect(url_for("pharmaiq.ai_knowledge"))
    s, added = _settings(), 0
    folder = os.path.join(current_app.root_path, "knowledge_base")
    for path in sorted(glob.glob(os.path.join(folder, "*"))):
        base = os.path.basename(path)
        if not base.lower().endswith((".md", ".txt", ".docx", ".pdf")):
            continue
        with open(path, "rb") as fh:
            r = store.add_document(_db(), base, fh.read(), None, "sop" if base.startswith("sop_") else "leaflet",
                                   "staff", session["username"], s.chunk_words, s.chunk_overlap)
        added += 0 if r["duplicate"] else 1
    _activity("KB_SAMPLES", f"Loaded {added} sample documents")
    flash(f"Loaded {added} sample document(s).", "success" if added else "info")
    return redirect(url_for("pharmaiq.ai_knowledge"))


# ── API: insights & evaluation ───────────────────────────────────────
@bp.route("/api/ai/insights/forecast")
@api_access("insights")
def api_forecast():
    return jsonify(_agent().tool_response("forecast_demand", {
        "name": (request.args.get("name") or "").strip() or None,
        "horizon_days": request.args.get("horizon", 14, type=int)}))


@bp.route("/api/ai/insights/anomalies")
@api_access("insights")
def api_anomalies():
    return jsonify(_agent().tool_response("detect_anomalies", {"lookback_days": request.args.get("days", 60, type=int)}))


@bp.route("/api/ai/insights/reorder")
@api_access("insights")
def api_reorder():
    return jsonify(_agent().tool_response("get_reorder_suggestions", {}))


@bp.route("/api/ai/evaluate", methods=["POST"])
@api_access("evaluation", write=True)
def api_evaluate():
    return jsonify(evaluate.run_all(_db(), _settings()))


def register(app, db_path, low_stock=10, expiry_days=30):
    """Hook PharmaIQ into the existing Flask app (called once from app.py)."""
    app.config["PHARMAIQ_DB"] = db_path
    app.config["PHARMAIQ_LOW_STOCK"] = low_stock
    app.config["PHARMAIQ_EXPIRY_DAYS"] = expiry_days
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    init_ai_tables(db_path)
    app.register_blueprint(bp)

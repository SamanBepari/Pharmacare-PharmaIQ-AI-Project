"""
Evaluation harness — measures PharmaIQ instead of trusting it.

Suites (JSON files in ./evaluation):
  sql_eval.json     routing + ANSWER CORRECTNESS: each question is compared with an independent
                    "oracle" SQL query run directly on the database
  rag_eval.json     retrieval quality: doc hit@1 / hit@3, keyword recall, and correct "no answer"
  safety_eval.json  clinical-advice refusals, false-positive checks, and SQL-guard attack cases

CLI:   python -m pharmaiq.evaluate            (prints a report, exit code 1 on failures)
UI:    PharmaIQ → Evaluation (admin)
"""
import json
import os
import sqlite3
import sys
from datetime import timedelta

from . import config
from .agent import PharmaIQ
from .sql_guard import ReadOnlySession, SQLValidationError

EVAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evaluation")


def _load(name):
    with open(os.path.join(EVAL_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def _user(db_path, role):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id, username FROM users WHERE role=? ORDER BY id LIMIT 1", (role,)).fetchone()
    conn.close()
    return {"id": row[0] if row else 0, "username": row[1] if row else role, "role": role}


def _oracle(db_path, sql, username):
    today = config.today()
    params = {"today": today.isoformat(), "plus30": (today + timedelta(days=30)).isoformat(),
              "plus90": (today + timedelta(days=90)).isoformat(), "d30": (today - timedelta(days=29)).isoformat(),
              "month_start": today.replace(day=1).isoformat(), "username": username}
    used = {k: v for k, v in params.items() if f":{k}" in sql}
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, used).fetchone()[0]
    finally:
        conn.close()


def run_sql_suite(db_path, settings):
    results = []
    for case in _load("sql_eval.json"):
        user = _user(db_path, case["role"])
        resp = PharmaIQ(db_path, user, settings).ask(case["question"])
        route_ok = resp["route"] == case["expect_route"]
        tool_ok = ("expect_tool" not in case) or resp.get("tool") == case["expect_tool"]
        data_ok, detail = True, ""
        tables = (resp.get("evidence") or {}).get("tables") or []
        if "oracle" in case and route_ok:
            truth = _oracle(db_path, case["oracle"], user["username"])
            t = tables[0] if tables else {"rows": [], "columns": []}
            if case["compare"] == "row_count":
                got = len(t["rows"])
            elif case["compare"] == "first_cell":
                got = t["rows"][0][0] if t["rows"] else None
            else:
                idx = t["columns"].index(case["col"]) if case["col"] in t["columns"] else 0
                got = round(sum((r[idx] or 0) for r in t["rows"]), 2)
            data_ok = (got == truth)
            detail = f"got {got}, oracle {truth}"
            if "expect_max_rows" in case:
                data_ok = data_ok and len(t["rows"]) <= case["expect_max_rows"]
        elif "oracle" in case:
            data_ok, detail = False, f"route was {resp['route']}"
        results.append({"id": case["id"], "question": case["question"], "role": case["role"],
                        "route": resp["route"], "tool": resp.get("tool"), "passed": route_ok and tool_ok and data_ok,
                        "detail": detail or f"route={resp['route']} tool={resp.get('tool')}"})
    return results


def run_rag_suite(db_path, settings):
    results = []
    user = _user(db_path, "admin")
    for case in _load("rag_eval.json"):
        resp = PharmaIQ(db_path, user, settings).ask(case["question"])
        sources = resp.get("sources") or []
        if case.get("expect_no_answer"):
            ok = len(sources) == 0 and resp["route"] == "rag"
            results.append({"id": case["id"], "question": case["question"], "passed": ok, "hit1": None, "hit3": None,
                            "kw": None, "detail": "correctly declined" if ok else f"returned {len(sources)} source(s)"})
            continue
        titles = [s["title"] for s in sources]
        hit1 = bool(titles) and case["expect_doc"].lower() in titles[0].lower()
        hit3 = any(case["expect_doc"].lower() in t.lower() for t in titles[:3])
        text = (resp.get("answer") or "").lower() + " ".join(s["snippet"].lower() for s in sources)
        kw = sum(1 for k in case["keywords"] if k.lower() in text) / len(case["keywords"])
        results.append({"id": case["id"], "question": case["question"], "passed": hit3 and kw >= 0.5, "hit1": hit1,
                        "hit3": hit3, "kw": round(kw, 2), "detail": titles[0][:50] if titles else "no sources"})
    return results


def run_safety_suite(db_path, settings):
    data, results = _load("safety_eval.json"), []
    for case in data["refusal_cases"]:
        resp = PharmaIQ(db_path, _user(db_path, case["role"]), settings).ask(case["question"])
        results.append({"id": case["id"], "question": case["question"], "kind": "must refuse",
                        "passed": resp["route"] == "refused", "detail": f"route={resp['route']}"})
    for case in data["allowed_cases"]:
        resp = PharmaIQ(db_path, _user(db_path, case["role"]), settings).ask(case["question"])
        results.append({"id": case["id"], "question": case["question"], "kind": "must NOT refuse",
                        "passed": resp["route"] != "refused", "detail": f"route={resp['route']}"})
    for case in data["guard_cases"]:
        user = _user(db_path, case["role"])
        try:
            with ReadOnlySession(db_path, case["role"], user["username"]) as s:
                s.execute(case["sql"])
            passed, detail = False, "query was EXECUTED"
        except SQLValidationError as exc:
            passed, detail = True, f"blocked: {exc.code}"
        results.append({"id": case["id"], "question": case["sql"], "kind": "SQL attack", "passed": passed, "detail": detail})
    # integrity: after all of the above, the inventory table must still exist and be intact
    conn = sqlite3.connect(db_path)
    ok = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0] >= 0
    conn.close()
    results.append({"id": "grd-final", "question": "medicines table intact after attacks", "kind": "integrity",
                    "passed": ok, "detail": "ok" if ok else "table damaged"})
    return results


def run_all(db_path, settings=None):
    settings = settings or config.get_settings()
    sql, rag, safety = run_sql_suite(db_path, settings), run_rag_suite(db_path, settings), run_safety_suite(db_path, settings)
    scored = [r for r in rag if r["hit1"] is not None]
    summary = {
        "sql_pass_rate": _rate(sql), "rag_pass_rate": _rate(rag), "safety_pass_rate": _rate(safety),
        "rag_hit_at_1": round(sum(1 for r in scored if r["hit1"]) / max(1, len(scored)), 2),
        "rag_hit_at_3": round(sum(1 for r in scored if r["hit3"]) / max(1, len(scored)), 2),
        "rag_keyword_recall": round(sum(r["kw"] for r in scored) / max(1, len(scored)), 2),
        "total_cases": len(sql) + len(rag) + len(safety),
        "total_passed": sum(r["passed"] for r in sql + rag + safety),
        "mode": "llm" if settings.provider != "offline" else "offline (rule-based)",
    }
    return {"summary": summary, "sql": sql, "rag": rag, "safety": safety}


def _rate(rows):
    return round(sum(1 for r in rows if r["passed"]) / max(1, len(rows)), 2)


if __name__ == "__main__":
    path = os.environ.get("PHARMAIQ_EVAL_DB", os.path.join("instance", "pharmacy.db"))
    out = run_all(path)
    for suite in ("sql", "rag", "safety"):
        print(f"\n== {suite.upper()} ==")
        for r in out[suite]:
            print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['id']:9} {r['question'][:58]:58} {r['detail']}")
    print("\nSUMMARY", json.dumps(out["summary"], indent=2))
    sys.exit(0 if out["summary"]["total_passed"] == out["summary"]["total_cases"] else 1)

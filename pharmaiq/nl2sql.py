"""
Rule-based text-to-SQL (offline mode).

Turns an inventory / sales question into a structured SPEC (so follow-ups can refine it)
and then renders a parameterised, read-only SQL statement from the spec. With a real LLM
provider the model writes the SQL instead — but the statement goes through the very same
SQL guard either way.
"""
import re
from dataclasses import dataclass, field

from . import nlp
from .tools import period_range


@dataclass
class SQLPlan:
    sql: str
    params: dict
    title: str
    assumptions: list = field(default_factory=list)
    spec: dict = field(default_factory=dict)


_LT = r"(?:fewer|less|lower|smaller|below|under|lesser)\s+than|<\s*=?|below|under|less than|fewer than"
_GT = r"(?:more|greater|higher|larger|above|over|bigger)\s+than|>\s*=?|above|over"
_NUM = r"\$?\s*(\d+(?:\.\d+)?)"


def _comparison(q):
    """Return (op, value, matched_text) for the first numeric comparison in q."""
    m = re.search(rf"(at least|minimum of|min of|>=|not less than)\s*{_NUM}", q)
    if m:
        return ">=", float(m.group(2)), m.group(0)
    m = re.search(rf"(at most|up to|maximum of|max of|<=|not more than)\s*{_NUM}", q)
    if m:
        return "<=", float(m.group(2)), m.group(0)
    m = re.search(rf"\b(?:fewer|less|lower|smaller|lesser)\s+than\s*{_NUM}|\b(?:below|under)\s*{_NUM}|<\s*{_NUM}", q)
    if m:
        v = next(g for g in m.groups() if g)
        return "<", float(v), m.group(0)
    m = re.search(rf"\b(?:more|greater|higher|larger|bigger)\s+than\s*{_NUM}|\b(?:above|over)\s*{_NUM}|>\s*{_NUM}", q)
    if m:
        v = next(g for g in m.groups() if g)
        return ">", float(v), m.group(0)
    m = re.search(rf"\b(?:exactly|equal to|equals)\s*{_NUM}", q)
    if m:
        return "=", float(m.group(1)), m.group(0)
    return None


def parse_spec(question: str, vocab, today):
    """Question → spec dict, or None when the question isn't a recognisable data question."""
    q = nlp.norm(question)
    spec = {"table": "medicines"}

    # which table
    if re.search(r"\b(sales?|sold|selling|revenue|turnover|transactions?)\b", q):
        spec["table"] = "sales"

    cat = nlp.match_category(q, vocab)
    comp = nlp.match_company(q, vocab)
    med = nlp.match_medicine(q, vocab)
    if cat:
        spec["category"] = cat
    if comp:
        spec["company"] = comp
    if med:
        spec["name"] = med
    period = nlp.extract_period(q)
    if period:
        spec["period"] = period
    limit = nlp.extract_limit(q)
    if limit:
        spec["limit"] = max(1, min(limit, 200))

    # numeric comparison → quantity or price
    comp_ = _comparison(q)
    if comp_ and spec["table"] == "medicines":
        op, val, text = comp_
        price_words = re.search(r"\$|\bprice|\bcost|\bcosts|\bpriced|\bexpensive|\bcheap", q)
        qty_words = re.search(rf"{re.escape(text)}\s*(units?|pcs|pieces|items|in stock|boxes|packs|tablets)", q) \
            or re.search(r"\bunits?\b|\bstock\b|\bquantity\b|\bqty\b|\bin stock\b", q)
        field_ = "price" if (price_words and not re.search(rf"{re.escape(text)}\s*(units?|pcs|pieces|items|in stock)", q)) else "product_qty"
        if not price_words and not qty_words:
            field_ = "product_qty"
        spec["cmp"] = {"field": field_, "op": op, "value": val}

    # expiry before / after a date
    m = re.search(r"(?:expir\w*|expires)\s+(before|after|by|on or before)\s+(\d{4}-\d{2}-\d{2})", q)
    if m and spec["table"] == "medicines":
        spec["exp"] = {"op": ">" if m.group(1) == "after" else "<=", "value": m.group(2)}

    # aggregation
    if re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", q) and spec["table"] == "medicines":
        spec["agg"] = "count"
    if re.search(r"\btotal (units|stock|quantity)\b", q) and spec["table"] == "medicines":
        spec["agg"] = "units"
    if re.search(r"\baverage price\b|\bavg price\b|\bmean price\b", q):
        spec["agg"] = "avg_price"
    g = re.search(r"\b(?:by|per|for each|grouped by|group by)\s+(category|categories|type|company|manufacturer|medicine|seller|staff|day|date)\b", q)
    if g:
        spec["group"] = {"categories": "category", "type": "category", "manufacturer": "company", "staff": "seller",
                         "date": "day"}.get(g.group(1), g.group(1))

    # ordering
    if re.search(r"\bmost expensive\b|\bhighest price\b|\bpriciest\b", q):
        spec["order"] = "price_desc"
    elif re.search(r"\bcheapest\b|\blowest price\b|\bleast expensive\b", q):
        spec["order"] = "price_asc"
    elif re.search(r"\bhighest stock\b|\bmost stock\b|\bbiggest stock\b|\bmost units\b", q):
        spec["order"] = "qty_desc"
    elif re.search(r"\blowest stock\b|\bleast stock\b|\bfewest\b", q):
        spec["order"] = "qty_asc"
    elif re.search(r"\bhighest sales\b|\btop sellers?\b|\bhighest revenue\b|\bgenerated the highest\b", q):
        spec["order"] = "revenue_desc"

    meaningful = any(k in spec for k in ("cmp", "exp", "agg", "group", "order")) \
        or (spec["table"] == "sales" and ("name" in spec or "category" in spec))
    if not meaningful and not (set(spec) - {"table", "period", "limit"}):
        return None
    if not meaningful:
        # only filters like "tablets from Cipla" — still a valid filtered listing
        if not any(k in spec for k in ("category", "company", "name")):
            return None
    return spec


def merge_spec(old: dict, new: dict) -> dict:
    out = dict(old or {})
    for k, v in (new or {}).items():
        if k == "table" and v == "medicines" and old and old.get("table") == "sales":
            continue
        out[k] = v
    return out


def build_sql(spec: dict, settings, today):
    """Render a spec to SQL. Always parameterised; returns SQLPlan."""
    params, conds, assumptions = {}, [], []
    table = spec.get("table", "medicines")
    limit = int(spec.get("limit") or min(100, settings.max_rows))

    if table == "sales":
        start, end, label = period_range(spec.get("period") or "last_30_days", today)
        if not spec.get("period"):
            assumptions.append(f"No period given — assumed {label}.")
        params.update(start=start, end=end)
        conds += ["date(s.sale_date) >= :start", "date(s.sale_date) <= :end"]
        if spec.get("name"):
            conds.append("LOWER(s.medicine_name) LIKE LOWER(:name)")
            params["name"] = f"%{spec['name']}%"
        if spec.get("category"):
            conds.append("LOWER(m.medicine_type) = LOWER(:category)")
            params["category"] = spec["category"]
        where = "WHERE " + " AND ".join(conds)
        group = spec.get("group") or ("day" if (spec.get("name") or spec.get("category")) else None)
        gcol = {"day": ("date(s.sale_date)", "day"), "medicine": ("s.medicine_name", "medicine"),
                "category": ("COALESCE(m.medicine_type, 'Unknown')", "category"), "seller": ("s.sold_by", "seller")}
        join = "LEFT JOIN medicines m ON m.id = s.medicine_id"
        if group in gcol:
            expr, alias = gcol[group]
            order = "revenue DESC" if group != "day" else "day ASC"
            sql = (f"SELECT {expr} AS {alias}, COUNT(*) AS transactions, SUM(s.quantity) AS units, "
                   f"ROUND(SUM(s.total_price), 2) AS revenue FROM sales s {join} {where} "
                   f"GROUP BY {expr} ORDER BY {order} LIMIT {limit}")
        else:
            sql = (f"SELECT COUNT(*) AS transactions, COALESCE(SUM(s.quantity), 0) AS units, "
                   f"ROUND(COALESCE(SUM(s.total_price), 0), 2) AS revenue FROM sales s {join} {where}")
        return SQLPlan(sql, params, f"Sales query — {label}", assumptions, spec)

    # ── medicines ──
    if spec.get("category"):
        conds.append("LOWER(medicine_type) = LOWER(:category)")
        params["category"] = spec["category"]
    if spec.get("company"):
        conds.append("LOWER(company_name) = LOWER(:company)")
        params["company"] = spec["company"]
    if spec.get("name"):
        conds.append("LOWER(medicine_name) LIKE LOWER(:name)")
        params["name"] = f"%{spec['name']}%"
    c = spec.get("cmp")
    if c:
        if c["op"] not in ("<", ">", "<=", ">=", "="):
            raise ValueError("bad operator")
        col = "price" if c["field"] == "price" else "product_qty"
        conds.append(f"COALESCE({col}, 0) {c['op']} :cmp_value")
        params["cmp_value"] = c["value"]
        if col == "product_qty":
            assumptions.append("'Fewer than N units' is a strict comparison (< N); the Alerts page 'low stock' rule is ≤ 10.")
    if spec.get("exp"):
        if spec["exp"]["op"] in ("<", "<="):
            assumptions.append("'Expiring before a date' includes batches that have already expired.")
        conds.append(f"exp_date {spec['exp']['op']} :exp_value AND exp_date IS NOT NULL AND exp_date != ''")
        params["exp_value"] = spec["exp"]["value"]
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    group = spec.get("group")
    gcol = {"category": ("medicine_type", "category"), "company": ("company_name", "company")}
    agg = spec.get("agg")
    if group in gcol:
        col, alias = gcol[group]
        sql = (f"SELECT COALESCE({col}, 'Unknown') AS {alias}, COUNT(*) AS products, "
               f"COALESCE(SUM(product_qty), 0) AS units, ROUND(COALESCE(SUM(price * product_qty), 0), 2) AS stock_value "
               f"FROM medicines {where} GROUP BY {col} ORDER BY products DESC LIMIT {limit}")
        title = f"Medicines grouped by {alias}"
    elif agg == "count":
        sql = f"SELECT COUNT(*) AS medicines FROM medicines {where}"
        title = "Medicine count"
    elif agg == "units":
        sql = f"SELECT COALESCE(SUM(product_qty), 0) AS total_units FROM medicines {where}"
        title = "Total units in stock"
    elif agg == "avg_price":
        sql = f"SELECT ROUND(AVG(price), 2) AS average_price, COUNT(*) AS medicines FROM medicines {where}"
        title = "Average price"
    else:
        order = {"price_desc": "price DESC", "price_asc": "price ASC", "qty_desc": "product_qty DESC",
                 "qty_asc": "product_qty ASC"}.get(spec.get("order"))
        if not order:
            order = "price ASC" if (c and c["field"] == "price") else "product_qty ASC"
        sql = (f"SELECT medicine_name, medicine_type AS category, company_name AS company, product_qty AS quantity, "
               f"price, exp_date FROM medicines {where} ORDER BY {order}, medicine_name ASC LIMIT {limit}")
        title = "Inventory query"
        if c:
            nice = {"<": "below", ">": "above", "<=": "at most", ">=": "at least", "=": "exactly"}[c["op"]]
            fld = "price" if c["field"] == "price" else "units in stock"
            val = int(c["value"]) if float(c["value"]).is_integer() else c["value"]
            title = f"Medicines with {fld} {nice} {val}"
    return SQLPlan(sql, params, title, assumptions, spec)

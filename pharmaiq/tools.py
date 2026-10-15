"""
Controlled tool functions — the ONLY database operations the LLM can request by name.

Each tool:
  * has a fixed, parameterised, hand-written SQL template (no free-form SQL),
  * validates and clamps its arguments,
  * runs through the same ReadOnlySession guard as text-to-SQL (so role limits apply),
  * returns a ToolResult holding the real rows + deterministic "facts".

Tools named in the project brief: get_expiring_medicines, get_low_stock_items,
get_sales_summary. More are provided for the other use-cases.
"""
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from . import analytics
from .sql_guard import ReadOnlySession, SQLValidationError


class ToolError(Exception):
    pass


# ──────────────────────────────────────────────────────────────────────
#  Result containers
# ──────────────────────────────────────────────────────────────────────
@dataclass
class Table:
    title: str
    columns: list
    rows: list
    sql: str = ""
    params: dict = field(default_factory=dict)
    truncated: bool = False


@dataclass
class ToolResult:
    tool: str
    title: str
    tables: list
    facts: list = field(default_factory=list)          # deterministic summary lines
    assumptions: list = field(default_factory=list)    # defaults the tool applied
    notes: list = field(default_factory=list)          # caveats / uncertainty statements
    chart: dict = None
    followups: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def primary(self):
        return self.tables[0] if self.tables else None


@dataclass
class ToolContext:
    session: ReadOnlySession
    role: str
    username: str
    today: date
    settings: object

    def query(self, title, sql, params=None) -> Table:
        res = self.session.execute(sql, params or {})
        return Table(title=title, columns=res.columns, rows=res.rows, sql=res.sql,
                     params=res.params, truncated=res.truncated)


@dataclass
class ToolSpec:
    name: str
    description: str
    params: dict
    roles: set
    fn: Callable


# ──────────────────────────────────────────────────────────────────────
#  Argument helpers
# ──────────────────────────────────────────────────────────────────────
def _int(v, default, lo, hi):
    if v is None or v == "":
        return default
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        raise ToolError(f"'{v}' is not a valid number.")


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ToolError(f"'{v}' is not a valid number.")


def _str(v, maxlen=80):
    if v is None:
        return None
    v = str(v).strip()
    return v[:maxlen] or None


def _enum(v, allowed, default):
    if v is None or v == "":
        return default
    v = str(v).strip().lower().replace(" ", "_")
    if v not in allowed:
        raise ToolError(f"'{v}' is not valid. Choose one of: {', '.join(sorted(allowed))}.")
    return v


PERIODS = {"today", "yesterday", "last_7_days", "this_month", "last_month",
           "last_30_days", "last_90_days", "this_year", "all_time"}


def period_range(period: str, today: date):
    """Return (start_iso, end_iso, human label) for a named period."""
    if period == "today":
        s = e = today
    elif period == "yesterday":
        s = e = today - timedelta(days=1)
    elif period == "last_7_days":
        s, e = today - timedelta(days=6), today
    elif period == "this_month":
        s, e = today.replace(day=1), today
    elif period == "last_month":
        e = today.replace(day=1) - timedelta(days=1)
        s = e.replace(day=1)
    elif period == "last_90_days":
        s, e = today - timedelta(days=89), today
    elif period == "this_year":
        s, e = date(today.year, 1, 1), today
    elif period == "all_time":
        s, e = date(1970, 1, 1), today
    else:  # last_30_days
        s, e = today - timedelta(days=29), today
    label = period.replace("_", " ")
    if period != "all_time":
        label += f" ({s.isoformat()} → {e.isoformat()})"
    return s.isoformat(), e.isoformat(), label


def _med_filters(a, params, alias=""):
    """Common optional filters for medicine-based tools."""
    p = f"{alias}." if alias else ""
    conds = []
    if a.get("category"):
        conds.append(f"LOWER({p}medicine_type) = LOWER(:category)")
        params["category"] = _str(a["category"])
    if a.get("company"):
        conds.append(f"LOWER({p}company_name) LIKE LOWER(:company)")
        params["company"] = f"%{_str(a['company'])}%"
    if a.get("name"):
        conds.append(f"LOWER({p}medicine_name) LIKE LOWER(:name)")
        params["name"] = f"%{_str(a['name'])}%"
    return conds


def _scope_note(ctx):
    return ["Showing only your own sales (staff accounts see their own records)."] if ctx.role == "staff" else []


def _chart_bar(labels, values, label, horizontal=False):
    return {"type": "bar", "labels": [str(l) for l in labels],
            "datasets": [{"label": label, "data": [float(v or 0) for v in values]}],
            "horizontal": horizontal}


def _sum_col(table, col):
    i = table.columns.index(col)
    return sum((r[i] or 0) for r in table.rows)


# ──────────────────────────────────────────────────────────────────────
#  Inventory tools
# ──────────────────────────────────────────────────────────────────────
def get_expiring_medicines(ctx, days=30, include_expired=False, category=None, company=None, name=None, **_):
    days = _int(days, ctx.settings.expiry_warn_days, 1, 730)
    today = ctx.today.isoformat()
    limit_date = (ctx.today + timedelta(days=days)).isoformat()
    params = {"today": today, "limit_date": limit_date}
    conds = ["exp_date IS NOT NULL", "exp_date != ''", "exp_date <= :limit_date"]
    if not include_expired:
        conds.append("exp_date >= :today")
    conds += _med_filters(dict(category=category, company=company, name=name), params)
    sql = f"""SELECT medicine_name, medicine_type AS category, lot_no AS batch, exp_date,
                     product_qty AS quantity,
                     CAST(julianday(exp_date) - julianday(:today) AS INTEGER) AS days_left,
                     ROUND(COALESCE(price, 0) * COALESCE(product_qty, 0), 2) AS stock_value
              FROM medicines WHERE {' AND '.join(conds)}
              ORDER BY exp_date ASC, medicine_name ASC"""
    t = ctx.query(f"Medicines expiring within {days} days", sql, params)
    r = ToolResult("get_expiring_medicines", t.title, [t],
                   assumptions=[f"As-of date: {today}. Window: {today} → {limit_date} ({days} days)."])
    n = len(t.rows)
    if n:
        r.facts.append(f"{n} medicine batch(es) expire within {days} days.")
        r.facts.append(f"Units affected: {int(_sum_col(t, 'quantity'))}; stock value at risk: {_sum_col(t, 'stock_value'):.2f}.")
        r.facts.append(f"Soonest expiry: {t.rows[0][0]} on {t.rows[0][3]} ({t.rows[0][5]} days left).")
        r.chart = _chart_bar([x[0] for x in t.rows[:15]], [x[4] for x in t.rows[:15]], "Units in stock", True)
    else:
        r.facts.append(f"No medicines expire within the next {days} days.")
    r.followups = ["Which of these have the highest stock value?", "Show already expired medicines",
                   "Now show only tablets"]
    return r


def get_expired_medicines(ctx, category=None, company=None, name=None, **_):
    today = ctx.today.isoformat()
    params = {"today": today}
    conds = ["exp_date IS NOT NULL", "exp_date != ''", "exp_date < :today"]
    conds += _med_filters(dict(category=category, company=company, name=name), params)
    sql = f"""SELECT medicine_name, medicine_type AS category, lot_no AS batch, exp_date,
                     product_qty AS quantity,
                     CAST(julianday(:today) - julianday(exp_date) AS INTEGER) AS days_overdue,
                     ROUND(COALESCE(price, 0) * COALESCE(product_qty, 0), 2) AS stock_value
              FROM medicines WHERE {' AND '.join(conds)}
              ORDER BY exp_date ASC"""
    t = ctx.query("Already expired medicines", sql, params)
    r = ToolResult("get_expired_medicines", t.title, [t], assumptions=[f"As-of date: {today}."])
    if t.rows:
        r.facts.append(f"{len(t.rows)} expired batch(es) still in the system, {int(_sum_col(t, 'quantity'))} units, "
                       f"value {_sum_col(t, 'stock_value'):.2f}.")
        r.notes.append("Expired stock must be quarantined and disposed of per the pharmacy SOP — do not sell.")
        r.chart = _chart_bar([x[0] for x in t.rows[:15]], [x[4] for x in t.rows[:15]], "Units", True)
    else:
        r.facts.append("No expired medicines found.")
    r.followups = ["What is the procedure for expired stock?", "Show medicines expiring in the next 30 days"]
    return r


def get_low_stock_items(ctx, threshold=None, category=None, company=None, name=None, limit=None, **_):
    threshold = _int(threshold, ctx.settings.low_stock_threshold, 0, 100000)
    limit = _int(limit, ctx.settings.max_rows, 1, ctx.settings.max_rows)
    params = {"t": threshold, "limit": limit}
    conds = ["COALESCE(product_qty, 0) <= :t"] + _med_filters(dict(category=category, company=company, name=name), params)
    sql = f"""SELECT medicine_name, medicine_type AS category, company_name AS company,
                     product_qty AS quantity, price, exp_date
              FROM medicines WHERE {' AND '.join(conds)}
              ORDER BY product_qty ASC, medicine_name ASC LIMIT :limit"""
    t = ctx.query(f"Low-stock items (≤ {threshold} units)", sql, params)
    r = ToolResult("get_low_stock_items", t.title, [t],
                   assumptions=[f"Rule: low stock means quantity ≤ {threshold} units (same rule as the Alerts page)."])
    if t.rows:
        zero = sum(1 for x in t.rows if (x[3] or 0) == 0)
        r.facts.append(f"{len(t.rows)} product(s) are at or below {threshold} units.")
        if zero:
            r.facts.append(f"{zero} product(s) are completely out of stock.")
        r.chart = _chart_bar([x[0] for x in t.rows[:15]], [x[3] for x in t.rows[:15]], "Units in stock", True)
    else:
        r.facts.append(f"No products are at or below {threshold} units.")
    r.followups = ["Which of these should I reorder first?", "Now show only capsules", "Show items with fewer than 5 units"]
    return r


def get_stock_level(ctx, name=None, **_):
    name = _str(name)
    if not name:
        raise ToolError("Please tell me which medicine to look up.")
    today = ctx.today.isoformat()
    warn = (ctx.today + timedelta(days=ctx.settings.expiry_warn_days)).isoformat()
    params = {"pat": f"%{name}%", "today": today, "warn": warn, "t": ctx.settings.low_stock_threshold}
    sql = """SELECT medicine_name, medicine_type AS category, lot_no AS batch,
                    product_qty AS quantity, exp_date, price,
                    CASE WHEN exp_date IS NOT NULL AND exp_date != '' AND exp_date < :today THEN 'Expired'
                         WHEN exp_date IS NOT NULL AND exp_date != '' AND exp_date <= :warn THEN 'Expiring soon'
                         WHEN product_qty <= :t THEN 'Low stock'
                         ELSE 'OK' END AS status
             FROM medicines WHERE LOWER(medicine_name) LIKE LOWER(:pat)
             ORDER BY medicine_name, exp_date"""
    t = ctx.query(f"Stock for '{name}'", sql, params)
    r = ToolResult("get_stock_level", t.title, [t], assumptions=[f"As-of date: {today}."])
    if t.rows:
        r.facts.append(f"{len(t.rows)} matching batch(es); {int(_sum_col(t, 'quantity'))} units in stock in total.")
    else:
        r.facts.append(f"No medicine matching '{name}' was found in the inventory.")
    r.followups = ["Show sales of this medicine", "Forecast demand for this medicine"]
    return r


def search_medicines(ctx, category=None, company=None, name=None, min_price=None, max_price=None,
                     min_qty=None, max_qty=None, sort_by=None, limit=None, **_):
    limit = _int(limit, 50, 1, ctx.settings.max_rows)
    sort_by = _enum(sort_by, {"name", "quantity", "price", "expiry"}, "name")
    params = {"limit": limit}
    conds = _med_filters(dict(category=category, company=company, name=name), params)
    for key, col, op in (("min_price", "price", ">="), ("max_price", "price", "<="),
                         ("min_qty", "product_qty", ">="), ("max_qty", "product_qty", "<=")):
        v = _num(locals()[key])
        if v is not None:
            conds.append(f"{col} {op} :{key}")
            params[key] = v
    order = {"name": "medicine_name ASC", "quantity": "product_qty ASC", "price": "price DESC",
             "expiry": "exp_date ASC"}[sort_by]
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""SELECT medicine_name, medicine_type AS category, company_name AS company,
                     product_qty AS quantity, price, exp_date, barcode
              FROM medicines {where} ORDER BY {order} LIMIT :limit"""
    t = ctx.query("Medicine search", sql, params)
    r = ToolResult("search_medicines", t.title, [t])
    r.facts.append(f"{len(t.rows)} medicine(s) matched.")
    return r


def get_inventory_value(ctx, group_by=None, **_):
    group_by = _enum(group_by, {"none", "category", "company"}, "category")
    col = {"category": "medicine_type", "company": "company_name"}.get(group_by)
    if col:
        sql = f"""SELECT COALESCE({col}, 'Unknown') AS {group_by}, COUNT(*) AS products,
                         COALESCE(SUM(product_qty), 0) AS units,
                         ROUND(COALESCE(SUM(price * product_qty), 0), 2) AS stock_value
                  FROM medicines GROUP BY {col} ORDER BY stock_value DESC"""
    else:
        sql = """SELECT COUNT(*) AS products, COALESCE(SUM(product_qty), 0) AS units,
                        ROUND(COALESCE(SUM(price * product_qty), 0), 2) AS stock_value FROM medicines"""
    t = ctx.query("Inventory value", sql)
    r = ToolResult("get_inventory_value", t.title, [t])
    total = _sum_col(t, "stock_value")
    r.facts.append(f"Total inventory value: {total:.2f} (price × quantity).")
    if col and t.rows:
        r.facts.append(f"Highest-value {group_by}: {t.rows[0][0]} ({t.rows[0][3]:.2f}).")
        r.chart = _chart_bar([x[0] for x in t.rows[:12]], [x[3] for x in t.rows[:12]], "Stock value")
    return r


# ──────────────────────────────────────────────────────────────────────
#  Sales tools
# ──────────────────────────────────────────────────────────────────────
def get_sales_summary(ctx, period="last_30_days", group_by="none", category=None, **_):
    period = _enum(period, PERIODS, "last_30_days")
    group_by = _enum(group_by, {"none", "day", "medicine", "category", "seller"}, "none")
    start, end, label = period_range(period, ctx.today)
    params = {"start": start, "end": end}
    where = "WHERE date(s.sale_date) >= :start AND date(s.sale_date) <= :end"
    join = ""
    if category:
        join = "LEFT JOIN medicines m ON m.id = s.medicine_id"
        where += " AND LOWER(m.medicine_type) = LOWER(:category)"
        params["category"] = _str(category)
        label += f", category {_str(category)}"
    if group_by == "day":
        sql = f"""SELECT date(s.sale_date) AS day, COUNT(*) AS transactions, SUM(s.quantity) AS units,
                         ROUND(SUM(s.total_price), 2) AS revenue
                  FROM sales s {join} {where} GROUP BY date(s.sale_date) ORDER BY day ASC"""
    elif group_by == "medicine":
        sql = f"""SELECT s.medicine_name AS medicine, COUNT(*) AS transactions, SUM(s.quantity) AS units,
                         ROUND(SUM(s.total_price), 2) AS revenue
                  FROM sales s {join} {where} GROUP BY s.medicine_name ORDER BY revenue DESC"""
    elif group_by == "category":
        sql = f"""SELECT COALESCE(m.medicine_type, 'Unknown') AS category, COUNT(*) AS transactions,
                         SUM(s.quantity) AS units, ROUND(SUM(s.total_price), 2) AS revenue
                  FROM sales s LEFT JOIN medicines m ON m.id = s.medicine_id {where}
                  GROUP BY COALESCE(m.medicine_type, 'Unknown') ORDER BY revenue DESC"""
    elif group_by == "seller":
        sql = f"""SELECT s.sold_by AS seller, COUNT(*) AS transactions, SUM(s.quantity) AS units,
                         ROUND(SUM(s.total_price), 2) AS revenue
                  FROM sales s {join} {where} GROUP BY s.sold_by ORDER BY revenue DESC"""
    else:
        sql = f"""SELECT COUNT(*) AS transactions, COALESCE(SUM(s.quantity), 0) AS units,
                         ROUND(COALESCE(SUM(s.total_price), 0), 2) AS revenue,
                         ROUND(COALESCE(AVG(s.total_price), 0), 2) AS avg_sale_value
                  FROM sales s {join} {where}"""
    t = ctx.query(f"Sales summary — {label}", sql, params)
    r = ToolResult("get_sales_summary", t.title, [t],
                   assumptions=[f"Period: {label}."], notes=_scope_note(ctx))
    if group_by == "none":
        tr, un, rev, avg = t.rows[0]
        r.facts.append(f"{tr} transaction(s), {un} unit(s), revenue {rev:.2f}" + (f", average sale {avg:.2f}." if tr else "."))
    else:
        total_rev = _sum_col(t, "revenue")
        r.facts.append(f"Total revenue in period: {total_rev:.2f} across {int(_sum_col(t, 'transactions'))} transaction(s).")
        if t.rows and group_by != "day":
            top = t.rows[0]
            share = (top[3] / total_rev * 100) if total_rev else 0
            r.facts.append(f"Top {group_by}: {top[0]} with revenue {top[3]:.2f} ({share:.0f}% of total).")
        if t.rows:
            labels = [x[0] for x in t.rows[:20]]
            vals = [x[3] for x in t.rows[:20]]
            r.chart = ({"type": "line", "labels": labels, "datasets": [{"label": "Revenue", "data": [float(v or 0) for v in vals]}]}
                       if group_by == "day" else _chart_bar(labels, vals, "Revenue"))
    if not t.rows or (group_by == "none" and t.rows[0][0] == 0):
        r.facts.append("No sales were recorded in this period.")
    r.followups = ["Show it by category", "Show the top selling medicines", "Compare with last month"]
    return r


def get_category_sales(ctx, period="this_month", category=None, **_):
    r = get_sales_summary(ctx, period=period, group_by="category", category=category)
    r.tool = "get_category_sales"
    r.title = r.title.replace("Sales summary", "Sales by category")
    r.tables[0].title = r.title
    r.followups = ["Now show only tablets", "Show the top selling medicines", "Show the same for last month"]
    return r


def get_top_selling(ctx, limit=5, period="last_30_days", metric="units", category=None, **_):
    limit = _int(limit, 5, 1, 50)
    period = _enum(period, PERIODS, "last_30_days")
    metric = _enum(metric, {"units", "revenue"}, "units")
    start, end, label = period_range(period, ctx.today)
    params = {"start": start, "end": end, "limit": limit}
    join, extra = "", ""
    if category:
        join, extra = "LEFT JOIN medicines m ON m.id = s.medicine_id", " AND LOWER(m.medicine_type) = LOWER(:category)"
        params["category"] = _str(category)
        label += f", category {_str(category)}"
    sql = f"""SELECT s.medicine_name AS medicine, SUM(s.quantity) AS units,
                     ROUND(SUM(s.total_price), 2) AS revenue, COUNT(*) AS transactions
              FROM sales s {join} WHERE date(s.sale_date) >= :start AND date(s.sale_date) <= :end{extra}
              GROUP BY s.medicine_name ORDER BY {metric} DESC LIMIT :limit"""
    t = ctx.query(f"Top {limit} selling medicines by {metric} — {label}", sql, params)
    r = ToolResult("get_top_selling", t.title, [t], assumptions=[f"Period: {label}."], notes=_scope_note(ctx))
    if t.rows:
        r.facts.append(f"Best seller by {metric}: {t.rows[0][0]} ({t.rows[0][1]} units, revenue {t.rows[0][2]:.2f}).")
        r.chart = _chart_bar([x[0] for x in t.rows], [x[1] if metric == "units" else x[2] for x in t.rows], metric.title(), True)
    else:
        r.facts.append("No sales were recorded in this period.")
    r.followups = ["Forecast demand for the top seller", "Show sales by category"]
    return r


# ──────────────────────────────────────────────────────────────────────
#  Intelligence tools (admin / pharmacist)
# ──────────────────────────────────────────────────────────────────────
def get_reorder_suggestions(ctx, target_days=30, **_):
    target_days = _int(target_days, 30, 7, 120)
    start = (ctx.today - timedelta(days=29)).isoformat()
    params = {"start": start, "t": ctx.settings.low_stock_threshold, "cover": 14}
    sql = """SELECT m.medicine_name, m.medicine_type AS category, m.product_qty AS quantity,
                    COALESCE(x.units, 0) AS units_sold_30d,
                    ROUND(COALESCE(x.units, 0) / 30.0, 2) AS avg_daily_sales,
                    CASE WHEN COALESCE(x.units, 0) = 0 THEN NULL
                         ELSE ROUND(m.product_qty / (x.units / 30.0), 1) END AS days_of_cover
             FROM medicines m
             LEFT JOIN (SELECT medicine_id, SUM(quantity) AS units FROM sales
                        WHERE date(sale_date) >= :start GROUP BY medicine_id) x ON x.medicine_id = m.id
             WHERE COALESCE(m.product_qty, 0) <= :t
                OR (x.units IS NOT NULL AND m.product_qty / (x.units / 30.0) <= :cover)
             ORDER BY days_of_cover IS NULL, days_of_cover ASC, m.product_qty ASC"""
    t = ctx.query("Reorder suggestions", sql, params)
    cols = t.columns + ["suggested_order_qty"]
    rows = []
    for row in t.rows:
        qty, avg = row[2] or 0, row[4] or 0
        suggest = max(0, math.ceil(avg * target_days - qty))
        if suggest == 0 and qty <= ctx.settings.low_stock_threshold:
            suggest = max(ctx.settings.low_stock_threshold * 2 - qty, 0)
        rows.append(row + [suggest])
    t.columns, t.rows = cols, rows
    r = ToolResult("get_reorder_suggestions", t.title, [t], assumptions=[
        f"Rule: flag items with ≤ {ctx.settings.low_stock_threshold} units OR ≤ 14 days of cover (30-day average sales).",
        f"Suggested quantity = average daily sales × {target_days} days − current stock (minimum top-up of "
        f"{ctx.settings.low_stock_threshold * 2} units for items with no recent sales).",
    ])
    r.facts.append(f"{len(rows)} product(s) should be reviewed for reordering.")
    r.notes.append("Suggested quantities are a heuristic from recent sales — a pharmacist should confirm before ordering.")
    if rows:
        r.chart = _chart_bar([x[0] for x in rows[:15]], [x[-1] for x in rows[:15]], "Suggested order qty", True)
    r.followups = ["Forecast demand for the first item", "Show low-stock items"]
    return r


def _daily_units(ctx, medicine_id=None, days=120):
    start = (ctx.today - timedelta(days=days - 1)).isoformat()
    params = {"start": start, "end": ctx.today.isoformat()}
    extra = ""
    if medicine_id is not None:
        extra = " AND medicine_id = :mid"
        params["mid"] = medicine_id
    t = ctx.query("daily", f"""SELECT date(sale_date) AS day, SUM(quantity) AS units FROM sales
                               WHERE date(sale_date) >= :start AND date(sale_date) <= :end{extra}
                               GROUP BY date(sale_date) ORDER BY day""", params)
    return t, analytics.daily_series([r[0] for r in t.rows], [r[1] for r in t.rows], start, ctx.today.isoformat())


def forecast_demand(ctx, name=None, horizon_days=14, **_):
    horizon = _int(horizon_days, 14, 1, 60)
    med_row, med_label, mid = None, "All medicines (total units)", None
    if name:
        params = {"pat": f"%{_str(name)}%"}
        found = ctx.query("lookup", "SELECT id, medicine_name, product_qty FROM medicines "
                                    "WHERE LOWER(medicine_name) LIKE LOWER(:pat) ORDER BY product_qty DESC", params)
        if not found.rows:
            raise ToolError(f"No medicine matching '{name}' was found.")
        mid, med_label = found.rows[0][0], found.rows[0][1]
        med_row = found.rows[0]
        if len(found.rows) > 1:
            med_label = found.rows[0][1]
    hist_table, series = _daily_units(ctx, mid)
    fc = analytics.forecast_series(series, horizon)
    r = ToolResult("forecast_demand", f"Demand forecast — {med_label}", [],
                   assumptions=["Model: damped-trend exponential smoothing with weekday seasonality, "
                                "trained on up to 120 days of sales. Interval shown is an 80% prediction interval."])
    if not fc.ok:
        r.facts.append(fc.reason)
        r.notes.append("No forecast was produced because it would not be reliable.")
        r.tables = [Table("Forecast", ["note"], [[fc.reason]])]
        return r
    rows = [[d.date().isoformat(), round(f, 1), round(lo, 1), round(hi, 1)]
            for d, f, lo, hi in fc.frame.itertuples(index=False)]
    r.tables = [Table(f"Forecast — next {horizon} days", ["date", "forecast_units", "lower_80", "upper_80"], rows)]
    r.facts.append(f"Expected demand over the next {horizon} days: {fc.total:.0f} units "
                   f"(80% range {fc.total_lower:.0f}–{fc.total_upper:.0f}).")
    r.facts.append(f"Confidence: {fc.confidence} ({fc.confidence_reason}).")
    if fc.backtest_mae is not None:
        r.facts.append(f"Back-test (last 7 days): average error {fc.backtest_mae:.1f} units/day vs "
                       f"{fc.naive_mae:.1f} for a naive average.")
    if med_row is not None and fc.total > 0:
        stock = med_row[2] or 0
        cover = stock / (fc.total / horizon)
        r.facts.append(f"Current stock {stock} units ≈ {cover:.0f} days of cover at the forecast rate.")
    r.notes += fc.notes
    r.notes.append("Forecasts are statistical estimates, not guarantees. Promotions, outbreaks or supply issues are not modelled.")
    hist = series.iloc[-30:]
    labels = [d.date().isoformat() for d in hist.index] + [row[0] for row in rows]
    pad = [None] * len(hist)
    r.chart = {"type": "line", "labels": labels, "datasets": [
        {"label": "Actual units", "data": [float(v) for v in hist.values] + [None] * len(rows)},
        {"label": "Forecast", "data": pad + [row[1] for row in rows]},
        {"label": "Lower 80%", "data": pad + [row[2] for row in rows], "dashed": True},
        {"label": "Upper 80%", "data": pad + [row[3] for row in rows], "dashed": True},
    ]}
    r.extra = {"history_days": fc.history_days, "confidence": fc.confidence}
    r.followups = ["Which items should I reorder?", "Are there any unusual sales spikes?"]
    return r


def _consistency_checks(ctx):
    today = ctx.today.isoformat()
    checks = [
        ("negative_stock", "high", "SELECT medicine_name AS entity, 'Quantity is ' || product_qty AS detail FROM medicines WHERE product_qty < 0", {}),
        ("expiry_before_issue", "medium",
         "SELECT medicine_name AS entity, 'Expiry ' || exp_date || ' is before issue date ' || issue_date AS detail FROM medicines "
         "WHERE exp_date IS NOT NULL AND exp_date != '' AND issue_date IS NOT NULL AND issue_date != '' AND exp_date < issue_date", {}),
        ("missing_expiry", "low", "SELECT medicine_name AS entity, 'No expiry date recorded' AS detail FROM medicines WHERE exp_date IS NULL OR exp_date = ''", {}),
        ("duplicate_barcode", "medium",
         "SELECT medicine_name AS entity, 'Barcode ' || barcode || ' is used by ' || (SELECT COUNT(*) FROM medicines b WHERE b.barcode = medicines.barcode) || ' products' AS detail "
         "FROM medicines WHERE barcode IS NOT NULL AND barcode != '' AND (SELECT COUNT(*) FROM medicines b WHERE b.barcode = medicines.barcode) > 1", {}),
        ("sale_total_mismatch", "high",
         "SELECT medicine_name || ' (sale #' || id || ')' AS entity, 'Total ' || total_price || ' ≠ qty × unit price (' || ROUND(quantity * unit_price, 2) || ')' AS detail "
         "FROM sales WHERE ABS(total_price - quantity * unit_price) > 0.01", {}),
        ("sale_unknown_medicine", "medium",
         "SELECT medicine_name || ' (sale #' || id || ')' AS entity, 'References a medicine id that no longer exists' AS detail "
         "FROM sales WHERE medicine_id NOT IN (SELECT id FROM medicines)", {}),
        ("sold_after_expiry", "high",
         "SELECT s.medicine_name || ' (sale #' || s.id || ')' AS entity, 'Sold on ' || date(s.sale_date) || ' but expired ' || m.exp_date AS detail "
         "FROM sales s JOIN medicines m ON m.id = s.medicine_id WHERE m.exp_date IS NOT NULL AND m.exp_date != '' AND date(s.sale_date) > m.exp_date", {}),
    ]
    out = []
    for code, sev, sql, params in checks:
        try:
            res = ctx.session.execute(sql, params)
        except SQLValidationError:
            continue   # role may not read a table needed for this check
        for entity, detail in res.rows[:25]:
            out.append([code, sev, entity, detail, ""])
    return out


def detect_anomalies(ctx, lookback_days=60, **_):
    lookback = _int(lookback_days, 60, 14, 365)
    rows = []
    # total daily units
    _, series = _daily_units(ctx, None, days=max(lookback, 40) + 30)
    for a in analytics.spike_anomalies(series):
        if a["date"] >= (ctx.today - timedelta(days=lookback)).isoformat():
            rows.append([f"sales_{a['kind']}", "medium", "All medicines",
                         f"{a['value']:.0f} units vs ~{a['expected']} expected (z={a['z_score']})", a["date"]])
    # per medicine
    meds = ctx.query("m", "SELECT medicine_id, medicine_name, COUNT(DISTINCT date(sale_date)) AS d FROM sales "
                          "WHERE date(sale_date) >= :s GROUP BY medicine_id, medicine_name HAVING d >= 10",
                     {"s": (ctx.today - timedelta(days=max(lookback, 40) + 29)).isoformat()})
    for mid, mname, _d in meds.rows[:40]:
        _, s2 = _daily_units(ctx, mid, days=max(lookback, 40) + 30)
        for a in analytics.spike_anomalies(s2, min_units=8):
            if a["date"] >= (ctx.today - timedelta(days=lookback)).isoformat():
                rows.append([f"sales_{a['kind']}", "medium", mname,
                             f"{a['value']:.0f} units vs ~{a['expected']} expected (z={a['z_score']})", a["date"]])
    rows += _consistency_checks(ctx)
    sev_order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (sev_order.get(r[1], 3), r[4] or "", r[2]), reverse=False)
    t = Table("Anomalies & data-quality findings", ["type", "severity", "entity", "detail", "date"], rows)
    r = ToolResult("detect_anomalies", t.title, [t], assumptions=[
        f"Sales spikes/drops: robust z-score (median/MAD) vs the previous 28 days, last {lookback} days.",
        "Data checks: negative stock, expiry before issue date, missing expiry, duplicate barcodes, sale totals, "
        "orphan sales, sales after expiry."])
    if rows:
        by = {}
        for x in rows:
            by[x[0]] = by.get(x[0], 0) + 1
        r.facts.append(f"{len(rows)} finding(s): " + ", ".join(f"{v}× {k.replace('_', ' ')}" for k, v in sorted(by.items())) + ".")
    else:
        r.facts.append("No anomalies or data-quality issues were found.")
    r.notes.append("Findings are flags for human review, not proof of error or misconduct.")
    r.followups = ["Show sales by day", "Show low-stock items"]
    return r


def get_management_summary(ctx, period="this_month", **_):
    period = _enum(period, PERIODS, "this_month")
    start, end, label = period_range(period, ctx.today)
    inv = ctx.query("Inventory KPIs", """SELECT COUNT(*) AS products, COALESCE(SUM(product_qty),0) AS units,
                  ROUND(COALESCE(SUM(price*product_qty),0),2) AS stock_value FROM medicines""")
    sections = [inv]
    facts = [f"Inventory: {inv.rows[0][0]} products, {inv.rows[0][1]} units, value {inv.rows[0][2]:.2f}."]
    for fn, kwargs in ((get_low_stock_items, {}), (get_expiring_medicines, {}),
                       (get_sales_summary, dict(period=period, group_by="category")),
                       (get_top_selling, dict(period=period))):
        sub = fn(ctx, **kwargs)
        sections.append(sub.tables[0])
        facts += sub.facts[:2]
    r = ToolResult("get_management_summary", f"Management summary — {label}", sections, facts=facts,
                   assumptions=[f"Sales period: {label}. As-of date {ctx.today.isoformat()}."], notes=_scope_note(ctx))
    cat = sections[3]
    if cat.rows:
        r.chart = _chart_bar([x[0] for x in cat.rows[:12]], [x[3] for x in cat.rows[:12]], "Revenue by category")
    r.followups = ["Show the low-stock items again", "Forecast demand for the next 14 days"]
    return r


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────
ALL = {"admin", "pharmacist", "staff"}
MGMT = {"admin", "pharmacist"}

TOOLS = {t.name: t for t in [
    ToolSpec("get_expiring_medicines", "Medicines expiring within N days with batch, expiry date and stock.",
             {"days": "int (default 30)", "include_expired": "bool", "category": "str", "company": "str", "name": "str"},
             ALL, get_expiring_medicines),
    ToolSpec("get_expired_medicines", "Medicines whose expiry date has already passed.",
             {"category": "str", "company": "str", "name": "str"}, ALL, get_expired_medicines),
    ToolSpec("get_low_stock_items", "Products at or below the low-stock threshold.",
             {"threshold": "int (default 10)", "category": "str", "company": "str", "name": "str", "limit": "int"},
             ALL, get_low_stock_items),
    ToolSpec("get_stock_level", "Current stock, batch and status of one named medicine.",
             {"name": "str (required)"}, ALL, get_stock_level),
    ToolSpec("search_medicines", "Filter inventory by category, company, name, price or quantity range.",
             {"category": "str", "company": "str", "name": "str", "min_price": "num", "max_price": "num",
              "min_qty": "int", "max_qty": "int", "sort_by": "name|quantity|price|expiry", "limit": "int"},
             ALL, search_medicines),
    ToolSpec("get_inventory_value", "Stock value (price × quantity), optionally grouped.",
             {"group_by": "none|category|company"}, ALL, get_inventory_value),
    ToolSpec("get_sales_summary", "Sales totals for a period, optionally grouped by day/medicine/category/seller.",
             {"period": "today|yesterday|last_7_days|this_month|last_month|last_30_days|last_90_days|this_year|all_time",
              "group_by": "none|day|medicine|category|seller", "category": "str (optional filter)"}, ALL, get_sales_summary),
    ToolSpec("get_category_sales", "Revenue and units sold per medicine category.",
             {"period": "see get_sales_summary", "category": "str"}, ALL, get_category_sales),
    ToolSpec("get_top_selling", "Best-selling medicines by units or revenue.",
             {"limit": "int (default 5)", "period": "see get_sales_summary", "metric": "units|revenue", "category": "str"}, ALL, get_top_selling),
    ToolSpec("get_reorder_suggestions", "Items to reorder, using stock level and recent sales velocity.",
             {"target_days": "int (default 30)"}, MGMT, get_reorder_suggestions),
    ToolSpec("forecast_demand", "Statistical demand forecast with uncertainty for one medicine or all.",
             {"name": "str (optional)", "horizon_days": "int (default 14, max 60)"}, MGMT, forecast_demand),
    ToolSpec("detect_anomalies", "Unusual sales spikes/drops and data-quality problems.",
             {"lookback_days": "int (default 60)"}, MGMT, detect_anomalies),
    ToolSpec("get_management_summary", "One-page overview: inventory, low stock, expiry, sales by category, top sellers.",
             {"period": "see get_sales_summary"}, ALL, get_management_summary),
]}


def tools_for_role(role: str) -> dict:
    return {n: t for n, t in TOOLS.items() if role in t.roles}


def run_tool(name: str, args: dict, ctx: ToolContext) -> ToolResult:
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool '{name}'.")
    if ctx.role not in spec.roles:
        raise ToolError(f"Your role ({ctx.role}) is not allowed to use '{name}'.")
    args = {k: v for k, v in (args or {}).items() if k in spec.params and v is not None}
    return spec.fn(ctx, **args)

"""
Planner — decides HOW to answer a question.

A Plan is one of:
    route="tool"    call a named, controlled tool with arguments
    route="sql"     run a validated, read-only SELECT (rule-built spec or LLM-written SQL)
    route="rag"     search the knowledge base (leaflets / SOPs) and answer with citations
    route="help"    explain what PharmaIQ can do
    route="clarify" ask the user to rephrase

Two planners share this interface:
    RulePlanner  – deterministic, offline, always available (default)
    LLMPlanner   – asks an LLM for a JSON plan; falls back to RulePlanner if the reply is
                   invalid, so a flaky model can never break the assistant.
"""
import re
from dataclasses import dataclass, field

from . import nl2sql, nlp, permissions
from .llm import LLMError, extract_json
from .tools import TOOLS, tools_for_role


@dataclass
class Plan:
    route: str
    tool: str = None
    args: dict = field(default_factory=dict)
    sql: str = None
    params: dict = field(default_factory=dict)
    spec: dict = None                  # rule-built SQL spec (enables follow-ups)
    query: str = None                  # text for RAG
    subject: str = None                # medicine / topic carried across follow-ups
    message: str = ""
    reason: str = ""
    planner: str = "rules"
    is_followup: bool = False


# ── intent regexes ───────────────────────────────────────────────────
HELP = re.compile(r"^(help|hi|hello|hey)\b|what can you do|how do (you|i use)|what do you know|capabilities|^\?+$")
KNOWLEDGE = re.compile(
    r"\b(procedure|policy|policies|sop|guideline|protocol|leaflet|side[- ]effects?|adverse|warnings?|precautions?|"
    r"contraindicat\w*|interactions?|storage|how (?:should|do|to) (?:we|i|staff|you)\b.*\b(store|handle|dispose|return|"
    r"receive|check|record|report|label)|what (?:do|should) (?:we|i|staff) do|how (?:is|are) .* (stored|handled|disposed)|"
    r"disposal|dispose|quarantine|cold chain|refrigerat\w*|what is .* (used|for)\b|used for|uses of|what does .* (do|treat)|"
    r"allerg\w*|overdose|return policy|refund|counsel\w*|scope of practice|incident|controlled (drugs?|medicines?)|"
    r"high[- ]alert|recall|what temperature|which temperature|temperature (for|of|range)|fefo|receiving|"
    r"stored|storing|kept|handled|handling|disposed)\b")
FOLLOWUP = re.compile(
    r"^(now|and|also|then|ok|okay|what about|how about|only|just|same|but|instead|filter|sort|limit|top \d+|by|per|for|during|"
    r"show (?:me )?(?:only|just)|break (?:it|that) down|group (?:it|that))\b"
    r"|\b(those|them|these|that one|this category|that category|the same|of these|among them|from these|in that|"
    r"for that|the top one|the first one|the top category)\b")
SALES = re.compile(r"\b(sales?|sell|selling|sold|revenue|turnover|income|transactions?|earned|earnings)\b")
EXPIRED = re.compile(r"\b(already expired|has expired|have expired|past (?:their )?expiry|out of date|expired)\b")
EXPIRING = re.compile(r"\b(expir\w*|shelf[- ]life|best before|going out of date|use[- ]by)\b")
LOWSTOCK = re.compile(r"\b(low[- ]stock|running low|almost out|nearly out|out of stock|stock[- ]?outs?|shortages?|"
                      r"running out|low on|need restocking|stock alert)\b")
REORDER = re.compile(r"\b(re-?order|restock|order more|what (?:should|do) (?:we|i) order|purchase order|replenish\w*)\b")
FORECAST = re.compile(r"\b(forecast\w*|predict\w*|projection|project(?:ed)?|expected demand|future demand|demand for|"
                      r"how much (?:will|do) we (?:sell|need))\b")
ANOMALY = re.compile(r"\b(anomal\w*|unusual|suspicious|spikes?|irregular\w*|inconsisten\w*|data quality|discrepanc\w*|"
                     r"weird|odd|outliers?|errors? in (?:the )?data|duplicate barcodes?)\b")
SUMMARY = re.compile(r"\b(summary|summari[sz]e|overview|snapshot|digest|dashboard|report|briefing)\b")
VALUE = re.compile(r"\b(inventory value|stock value|value of (?:the )?(?:stock|inventory)|worth|valuation|total value)\b")
STOCKQ = re.compile(r"\b(stock|quantity|qty|how many|do we have|available|in stock|left|on hand|remaining)\b")
INFO_WORDS = re.compile(r"\b(use|uses|used|side|effects?|warning|precaution|storage|store|dosage|dose|interaction|"
                        r"allergy|safe|safety|contraindication|about|information|leaflet|what is)\b")


def _tool_args_for_followup(tool, args):
    spec = TOOLS.get(tool)
    return {k: v for k, v in args.items() if spec and k in spec.params}


class RulePlanner:
    name = "rules"

    def plan(self, question, state, vocab, role, today, settings) -> Plan:
        q = nlp.norm(question)
        cat = nlp.match_category(q, vocab)
        comp = nlp.match_company(q, vocab)
        med = nlp.match_medicine(q, vocab)
        period = nlp.extract_period(q)
        days = nlp.extract_days(q, today)
        limit = nlp.extract_limit(q)

        if HELP.search(q):
            return Plan("help", reason="help request")

        # ── follow-ups ───────────────────────────────────────────────
        if state and FOLLOWUP.search(q):
            fp = self._followup(q, state, vocab, role, today, settings, cat, comp, med, period, days, limit)
            if fp:
                return fp

        # ── knowledge questions first (policy / leaflet wording wins over inventory keywords) ──
        if KNOWLEDGE.search(q) and not SALES.search(q):
            return Plan("rag", query=question, subject=med, reason="documentation question")

        # ── analytics / intelligence ─────────────────────────────────
        if ANOMALY.search(q):
            return Plan("tool", tool="detect_anomalies", reason="anomaly keywords")
        if FORECAST.search(q):
            weeks = nlp.find_int(q, [r"(\d+)\s*weeks?"])
            horizon = (weeks * 7 if weeks else None) or nlp.find_int(q, [r"(\d+)\s*days?"]) or 14
            return Plan("tool", tool="forecast_demand", args={"name": med, "horizon_days": horizon},
                        subject=med, reason="forecast keywords")
        if REORDER.search(q):
            return Plan("tool", tool="get_reorder_suggestions", reason="reorder keywords")
        if SUMMARY.search(q) and not KNOWLEDGE.search(q):
            return Plan("tool", tool="get_management_summary", args={"period": period or "this_month"},
                        reason="summary / report keywords")
        if VALUE.search(q):
            grp = "company" if re.search(r"compan|manufactur", q) else "none" if re.search(r"\btotal\b", q) and not cat \
                and not re.search(r"\bby\b|\bper\b|categor", q) else "category"
            return Plan("tool", tool="get_inventory_value", args={"group_by": grp}, reason="inventory value")

        # ── sales ────────────────────────────────────────────────────
        if SALES.search(q):
            return self._sales(q, vocab, today, settings, cat, med, period, limit, role)

        # ── expiry before/after an explicit date → text-to-SQL (the tools use day windows) ──
        if re.search(r"expir\w*\s+(?:on or before|before|after|by)\s+\d{4}-\d{2}-\d{2}", q):
            sp = nl2sql.parse_spec(question, vocab, today)
            if sp and "exp" in sp:
                return Plan("sql", spec=sp, reason="expiry compared with an explicit date", subject=med)

        # ── expiry ───────────────────────────────────────────────────
        if EXPIRING.search(q) or EXPIRED.search(q):
            args = {k: v for k, v in dict(category=cat, company=comp).items() if v}
            if EXPIRING.search(q) and not re.search(r"\balready\b|\bhave expired\b|\bhas expired\b", q):
                args["days"] = days or settings.expiry_warn_days
                if re.search(r"\bincluding expired\b|\bor expired\b|\band expired\b", q):
                    args["include_expired"] = True
                return Plan("tool", tool="get_expiring_medicines", args=args, reason="expiry keywords")
            return Plan("tool", tool="get_expired_medicines", args=args, reason="already-expired keywords")

        # ── stock alerts ─────────────────────────────────────────────
        spec = nl2sql.parse_spec(question, vocab, today)
        if LOWSTOCK.search(q) and not (spec and "cmp" in spec):
            args = {k: v for k, v in dict(category=cat, company=comp).items() if v}
            if re.search(r"out of stock|stock[- ]?outs?", q):
                args["threshold"] = 0
            return Plan("tool", tool="get_low_stock_items", args=args, reason="low-stock keywords")

        # ── ad-hoc data question → text-to-SQL ───────────────────────
        if spec and any(k in spec for k in ("cmp", "exp", "agg", "group", "order")):
            return Plan("sql", spec=spec, reason="structured filter / aggregation", subject=med)

        # ── named medicine ───────────────────────────────────────────
        if med:
            if INFO_WORDS.search(q) and not STOCKQ.search(q.replace("what is", "")):
                return Plan("rag", query=question, subject=med, reason="medicine information")
            return Plan("tool", tool="get_stock_level", args={"name": med}, subject=med, reason="medicine named")

        if spec:
            return Plan("sql", spec=spec, reason="filters only")
        if cat or comp:
            return Plan("tool", tool="search_medicines",
                        args={k: v for k, v in dict(category=cat, company=comp).items() if v}, reason="category/company")
        if STOCKQ.search(q):
            return Plan("tool", tool="search_medicines", args={"sort_by": "quantity"}, reason="generic stock question")

        # last resort: let the knowledge base try
        return Plan("rag", query=question, subject=None, reason="fallback to documentation")

    # ------------------------------------------------------------------
    def _sales(self, q, vocab, today, settings, cat, med, period, limit, role):
        period = period or "last_30_days"
        if med:   # sales of one medicine → text-to-SQL
            spec = {"table": "sales", "name": med, "period": period}
            if re.search(r"\bby day|daily|trend|over time\b", q):
                spec["group"] = "day"
            return Plan("sql", spec=spec, subject=med, reason="sales of one medicine")
        if re.search(r"\b(top|best|most|highest)\b.*\b(sell\w*|sold|sellers?)\b|\bbest[- ]?sell\w*|\btop sellers?\b|\bmost popular\b", q):
            args = {"period": period, "limit": limit or 5}
            if re.search(r"\brevenue\b|\bmoney\b|\bincome\b", q):
                args["metric"] = "revenue"
            if cat:
                args["category"] = cat
            return Plan("tool", tool="get_top_selling", args=args, reason="top sellers")
        if re.search(r"\bcategor\w*|\bby type\b|\bmedicine types?\b", q):
            args = {"period": period if nlp.extract_period(q) else "this_month"}
            if cat:
                args["category"] = cat
            return Plan("tool", tool="get_category_sales", args=args, reason="sales by category")
        group = "none"
        if re.search(r"\bby day\b|\bdaily\b|\bper day\b|\btrend\b|\bover time\b|\beach day\b", q):
            group = "day"
        elif re.search(r"\bby (staff|seller|user|cashier|employee|person)\b|\bper (staff|seller|user)\b|\bwho sold\b", q):
            group = "seller"
        elif re.search(r"\bby medicine\b|\bper medicine\b|\beach medicine\b|\bby product\b", q):
            group = "medicine"
        args = {"period": period, "group_by": group}
        if cat:
            args["category"] = cat
        return Plan("tool", tool="get_sales_summary", args=args, reason="sales summary")

    def _followup(self, q, state, vocab, role, today, settings, cat, comp, med, period, days, limit):
        route, tool = state.get("route"), state.get("tool")
        args = dict(state.get("args") or {})
        # "this category" → category of the top row of the previous result
        top = state.get("top_label")
        if re.search(r"\b(this|that|the top) category\b|\bthe top one\b|\bthe first one\b", q) and top and not cat:
            if state.get("top_kind") == "category":
                cat = top
            elif state.get("top_kind") == "medicine":
                med = top.split()[0].lower()
        noun_products = re.search(r"\b(products?|medicines?|items?|stock|inventory)\b", q)

        if route == "rag":
            subject = med or state.get("subject")
            query = (f"{subject} {q}" if subject and (not med) else q)
            return Plan("rag", query=query, subject=subject, is_followup=True, reason="follow-up on documentation")

        if route == "sql" and state.get("spec"):
            new = {}
            if cat: new["category"] = cat
            if comp: new["company"] = comp
            if med: new["name"] = med
            if period: new["period"] = period
            if limit: new["limit"] = limit
            fresh = nl2sql.parse_spec(q, vocab, today) or {}
            for k in ("cmp", "group", "order", "agg", "exp"):
                if k in fresh:
                    new[k] = fresh[k]
            if not new:
                return None
            return Plan("sql", spec=nl2sql.merge_spec(state["spec"], new), is_followup=True,
                        subject=state.get("subject"), reason="refined previous SQL spec")

        if route == "tool" and tool:
            # sales result + "show only the products in this category" → inventory listing for that category
            if cat and noun_products and tool in ("get_category_sales", "get_sales_summary", "get_top_selling"):
                return Plan("tool", tool="search_medicines", args={"category": cat, "sort_by": "quantity"},
                            is_followup=True, reason="follow-up: products of the chosen category")
            new = {}
            if cat: new["category"] = cat
            if comp: new["company"] = comp
            if med and tool in ("get_stock_level", "forecast_demand", "get_expiring_medicines", "get_low_stock_items"):
                new["name"] = med
            if period: new["period"] = period
            if days and tool == "get_expiring_medicines": new["days"] = days
            if limit: new["limit"] = limit
            grp = re.search(r"\b(?:by|per)\s+(category|day|medicine|seller)\b", q)
            if grp and tool in ("get_sales_summary", "get_category_sales"): new["group_by"] = grp.group(1)
            m = re.search(r"(?:fewer|less|below|under)\s+than\s+(\d+)|\b(?:threshold|at most|up to)\s+(\d+)", q)
            if m and tool == "get_low_stock_items":
                # the tool rule is "quantity <= threshold", so "fewer than N" means threshold N-1
                new["threshold"] = max(0, int(m.group(1)) - 1) if m.group(1) else int(m.group(2))
            if tool == "get_category_sales" and new.get("group_by"):
                tool, new = "get_sales_summary", {**new}
            # keep only args the tool understands
            merged = _tool_args_for_followup(tool, {**args, **new})
            if not new:
                return None
            return Plan("tool", tool=tool, args=merged, is_followup=True, subject=state.get("subject"),
                        reason="refined previous tool call")
        return None


# ──────────────────────────────────────────────────────────────────────
#  LLM planner
# ──────────────────────────────────────────────────────────────────────
PLANNER_SYSTEM = """You are the planning module of PharmaIQ, an assistant inside a pharmacy management system.
Decide how to answer the user's question and reply with ONE JSON object only (no prose, no markdown).

Routes:
  {"route":"tool","tool":"<name>","args":{...}}     – PREFERRED for inventory / sales / expiry / forecast questions
  {"route":"sql","sql":"SELECT ...","params":{}}   – only if no tool fits; a single read-only SELECT
  {"route":"rag","query":"<search text>"}          – questions answered by medicine leaflets or pharmacy SOPs/policies
  {"route":"help"}                                  – user asks what you can do
  {"route":"clarify","message":"<one short question>"}

Rules:
- Never plan anything that gives patient-specific doses, diagnosis or treatment advice.
- SQL: SQLite dialect, SELECT only, use named parameters (:name) for values, only the tables/columns listed
  below, no comments, no CTE column lists, no functions other than count,sum,avg,min,max,round,abs,coalesce,
  lower,upper,length,substr,date,datetime,julianday,strftime,iif,cast. Today's date is passed as :today if needed.
- Live data (stock, expiry, sales) must come from tool/sql routes — never from documents.
- If the user's message is a follow-up, use PREVIOUS PLAN to keep filters the user did not change.
"""


class LLMPlanner:
    name = "llm"

    def __init__(self, provider, fallback: RulePlanner):
        self.provider, self.fallback = provider, fallback

    def plan(self, question, state, vocab, role, today, settings) -> Plan:
        tool_lines = "\n".join(f"- {n}({', '.join(f'{k}: {v}' for k, v in t.params.items())}): {t.description}"
                               for n, t in tools_for_role(role).items())
        user = (f"TODAY: {today.isoformat()}\nROLE: {role}\n\nTOOLS:\n{tool_lines}\n\n"
                f"DATABASE SCHEMA:\n{permissions.schema_prompt(role)}\n\n"
                f"KNOWN CATEGORIES: {', '.join(vocab.categories)}\n"
                f"PREVIOUS PLAN: {state if state else 'none'}\n\nQUESTION: {question}")
        try:
            data = extract_json(self.provider.complete(PLANNER_SYSTEM, user, max_tokens=500))
            plan = self._validate(data, question, role)
            plan.planner = "llm"
            return plan
        except (LLMError, ValueError, KeyError, TypeError):
            p = self.fallback.plan(question, state, vocab, role, today, settings)
            p.planner = "rules (llm fallback)"
            return p

    @staticmethod
    def _validate(data, question, role) -> Plan:
        route = str(data.get("route", "")).lower()
        if route == "tool":
            name = data.get("tool")
            if name not in tools_for_role(role):
                raise ValueError("unknown or forbidden tool")
            args = data.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
            return Plan("tool", tool=name, args=args, reason="llm plan")
        if route == "sql":
            sql = data.get("sql")
            params = data.get("params") or {}
            if not isinstance(sql, str) or not isinstance(params, dict):
                raise ValueError("bad sql plan")
            return Plan("sql", sql=sql, params=params, reason="llm plan")
        if route == "rag":
            return Plan("rag", query=str(data.get("query") or question), reason="llm plan")
        if route == "help":
            return Plan("help", reason="llm plan")
        if route == "clarify":
            return Plan("clarify", message=str(data.get("message") or "Could you rephrase that?"), reason="llm plan")
        raise ValueError("unknown route")

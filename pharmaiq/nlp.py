"""
Light natural-language helpers shared by the rule-based planner and rule-based text-to-SQL:
vocabulary (categories / companies / medicine names read from the DB), period and number
extraction, fuzzy medicine matching.
"""
import difflib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .rag.text import STOPWORDS


@dataclass
class Vocab:
    categories: list = field(default_factory=list)
    companies: list = field(default_factory=list)
    medicines: list = field(default_factory=list)

    @property
    def first_words(self):
        out = {}
        for m in self.medicines:
            w = re.split(r"[\s/(]", m.strip().lower())[0]
            if len(w) >= 4:
                out.setdefault(w, m)
        return out


def load_vocab(session) -> Vocab:
    cats = [r[0] for r in session.execute(
        "SELECT DISTINCT medicine_type FROM medicines WHERE medicine_type IS NOT NULL AND medicine_type != ''").rows]
    comps = [r[0] for r in session.execute(
        "SELECT DISTINCT company_name FROM medicines WHERE company_name IS NOT NULL AND company_name != ''").rows]
    meds = [r[0] for r in session.execute(
        "SELECT DISTINCT medicine_name FROM medicines WHERE medicine_name IS NOT NULL").rows]
    return Vocab(cats, comps, meds)


def norm(q: str) -> str:
    return " ".join((q or "").lower().replace("’", "'").split())


# ── category / company / medicine matching ───────────────────────────
def match_category(q: str, vocab: Vocab):
    ql = norm(q)
    for cat in sorted(vocab.categories, key=len, reverse=True):
        c = cat.lower()
        if re.search(rf"\b{re.escape(c)}(s|es)?\b", ql):
            return cat
    return None


def match_company(q: str, vocab: Vocab):
    ql = norm(q)
    for comp in sorted(vocab.companies, key=len, reverse=True):
        c = comp.lower()
        if c in ql:
            return comp
        first = c.split()[0]
        if len(first) >= 5 and re.search(rf"\b{re.escape(first)}\b", ql):
            return comp
    return None


_COMMON = STOPWORDS | {"medicine", "medicines", "stock", "sales", "sale", "price", "units", "unit", "category",
                       "product", "products", "items", "expiry", "expire", "report", "summary", "storage",
                       "policy", "procedure", "tablets", "tablet", "capsule", "capsules", "syrup", "injection"}


def match_medicine(q: str, vocab: Vocab):
    """Return a short search term for a medicine mentioned in q (first word, e.g. 'paracetamol')."""
    ql = norm(q)
    fw = vocab.first_words
    for w in re.findall(r"[a-z][a-z0-9-]{3,}", ql):
        if w in fw:
            return w
    for w in re.findall(r"[a-z]{5,}", ql):
        if w in _COMMON:
            continue
        m = difflib.get_close_matches(w, list(fw), n=1, cutoff=0.84)
        if m:
            return m[0]
    return None


# ── numbers / periods ────────────────────────────────────────────────
def find_int(q: str, patterns):
    for p in patterns:
        m = re.search(p, q)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def extract_period(q: str):
    ql = norm(q)
    if re.search(r"\byesterday\b", ql):
        return "yesterday"
    if re.search(r"\btoday\b|\btoday's\b", ql):
        return "today"
    if re.search(r"\blast month\b|\bprevious month\b", ql):
        return "last_month"
    if re.search(r"\bthis month\b|\bmonth to date\b|\bmtd\b|\bcurrent month\b", ql):
        return "this_month"
    if re.search(r"\bthis year\b|\bytd\b|\byear to date\b", ql):
        return "this_year"
    if re.search(r"\ball[- ]time\b|\bever\b|\boverall\b|\bsince the beginning\b", ql):
        return "all_time"
    m = re.search(r"\b(?:last|past|previous)\s+(\d+)\s+days?\b", ql)
    if m:
        n = int(m.group(1))
        return "last_7_days" if n == 7 else "last_90_days" if n == 90 else "last_30_days" if n == 30 else \
            ("last_7_days" if n < 14 else "last_30_days" if n < 60 else "last_90_days")
    if re.search(r"\b(last|past) week\b|\bthis week\b|\b7 days\b", ql):
        return "last_7_days"
    if re.search(r"\blast quarter\b|\b90 days\b|\bquarter\b", ql):
        return "last_90_days"
    if re.search(r"\b30 days\b|\bmonth\b", ql):
        return "last_30_days"
    return None


def extract_days(q: str, today: date):
    """'in the next 45 days', 'within 2 weeks', 'this month', 'next month' → days."""
    ql = norm(q)
    n = find_int(ql, [r"(?:next|within|in|over the next|coming)\s+(\d+)\s+days?", r"(\d+)\s+days?"])
    if n:
        return n
    w = find_int(ql, [r"(?:next|within|in|coming)\s+(\d+)\s+weeks?"])
    if w:
        return w * 7
    mth = find_int(ql, [r"(?:next|within|in|coming)\s+(\d+)\s+months?"])
    if mth:
        return mth * 30
    if re.search(r"\bnext week\b|\bthis week\b|\bone week\b", ql):
        return 7
    if re.search(r"\bthis month\b", ql):
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return max(1, (nxt - today).days - 1)
    if re.search(r"\bnext month\b", ql):
        return 60
    if re.search(r"\bnext quarter\b|\bnext 3 months\b", ql):
        return 90
    return None


def extract_limit(q: str):
    return find_int(norm(q), [r"\btop\s+(\d+)\b", r"\bfirst\s+(\d+)\b", r"\b(\d+)\s+(?:best|most|highest|lowest|cheapest)\b",
                              r"\blimit\s+(\d+)\b"])

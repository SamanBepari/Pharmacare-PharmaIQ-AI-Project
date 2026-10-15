"""Tiny shared text utilities (tokeniser, light stemmer, stop-words)."""
import re

STOPWORDS = set("""
a an and are as at be but by can could do does did for from had has have how i if in into is it its
of on or our should so than that the their them then there these they this to us was we were what
when where which who whom why will with would you your about tell me please show give list any all
""".split())

_WORD = re.compile(r"[a-z0-9]+")


# Domain synonyms applied BEFORE stemming, so "fridge" finds text that says "refrigerator".
SYNONYMS = {"fridge": "refrigerator", "fridges": "refrigerator", "refrigerated": "refrigerator",
            "refrigeration": "refrigerator", "meds": "medicine", "med": "medicine", "drug": "medicine",
            "drugs": "medicine", "pills": "tablet", "pill": "tablet", "cold": "refrigerator",
            "outdated": "expired", "recalled": "recall", "refunds": "refund", "refunded": "refund",
            "overdosing": "overdose", "poisoning": "overdose", "unwanted": "side"}


def stem(w: str) -> str:
    """Very light English stemmer — good enough to match store/stored/storing/stores."""
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def tokens(text: str, keep_stop=False) -> list:
    out = []
    for w in _WORD.findall((text or "").lower()):
        if not keep_stop and w in STOPWORDS:
            continue
        out.append(stem(SYNONYMS.get(w, w)))
    return out


def sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip(" -•*\t") for p in parts if len(p.strip()) > 3]

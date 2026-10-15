"""Build cited answers from retrieved chunks (extractive / offline path)."""
from .text import sentences, tokens


def pick_sentences(query, hit, max_sentences=3):
    q = set(tokens(query))
    scored = []
    for idx, s in enumerate(sentences(hit.text)):
        if s.lower().startswith(("sample ", "> sample", "this procedure applies")) or "not a substitute for" in s.lower():
            continue          # boilerplate banners are never useful as an answer
        st = set(tokens(s))
        overlap = len(q & st)
        if overlap:
            scored.append((overlap / (1 + 0.02 * len(s.split())), idx, s))
    scored.sort(reverse=True)
    chosen = sorted(scored[:max_sentences], key=lambda x: x[1])
    return [s for _, _, s in chosen]


def extractive_answer(query, hits, max_sources=3):
    """Return (answer_text, sources). Only RELEVANT hits are used; none → (None, [])."""
    good = [h for h in hits if h.relevant]
    if not good:
        return None, []
    sources, lines = [], []
    picked = [(h, pick_sentences(query, h)) for h in good]
    with_text = [(h, s) for h, s in picked if s][:max_sources]      # skip empty/boilerplate chunks, THEN cap
    if not with_text:
        return None, []              # only boilerplate matched → treat as "no answer"
    for n, (h, sents) in enumerate(with_text, 1):
        loc = f", page {h.page}" if h.page else ""
        sec = f" › {h.section}" if h.section and h.section.strip() != h.title.strip() else ""
        sources.append({"n": n, "doc_id": h.doc_id, "chunk_id": h.chunk_id, "title": h.title,
                        "doc_type": h.doc_type, "page": h.page, "section": h.section,
                        "snippet": h.text[:420], "bm25": round(h.bm25, 2), "dense": round(h.dense, 2),
                        "coverage": h.coverage})
        lines.append(f"**{h.title}{sec}{loc}** [{n}]: " + " ".join(sents))
    return "\n\n".join(lines), sources

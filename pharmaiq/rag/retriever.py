"""
Hybrid retriever = BM25 keyword search  +  vector search, fused with Reciprocal Rank Fusion.

Vector backends
  tfidf                (default, no download)  char n-gram TF-IDF → tolerant to typos / word forms
  sentence-transformers (optional)             true semantic embeddings (+ FAISS if installed)

If an optional backend cannot be loaded the retriever silently falls back to `tfidf`
and records why in `Retriever.backend_note` (shown on the Knowledge Base page).
"""
import math
from collections import Counter
from dataclasses import dataclass

import numpy as np

from . import store
from .text import tokens

RRF_K = 60


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    title: str
    doc_type: str
    page: int
    section: str
    text: str
    bm25: float
    dense: float
    coverage: float
    score: float
    relevant: bool
    body_coverage: float = 0.0


class BM25:
    def __init__(self, docs_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(t) for t in docs_tokens]
        self.lens = np.array([len(t) for t in docs_tokens], dtype=float)
        self.avg = float(self.lens.mean()) if len(self.lens) else 0.0
        df = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, q_tokens):
        out = np.zeros(len(self.docs))
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q_tokens:
                f = d.get(t, 0)
                if f:
                    denom = f + self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avg or 1))
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / denom
            out[i] = s
        return out


class _TfidfBackend:
    name = "tfidf (char n-gram)"

    def __init__(self, texts):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, lowercase=True)
        self.matrix = self.vec.fit_transform(texts)

    def similarities(self, query):
        q = self.vec.transform([query])
        return np.asarray((self.matrix @ q.T).todense()).ravel()


class _SentenceTransformerBackend:
    def __init__(self, chunks, model_name, db_path):
        from sentence_transformers import SentenceTransformer   # optional dependency
        self.model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers ({model_name})"
        vectors, missing = [None] * len(chunks), []
        for i, c in enumerate(chunks):
            if c["embedding"] is not None and c["embed_model"] == model_name:
                vectors[i] = np.frombuffer(c["embedding"], dtype=np.float32)
            else:
                missing.append(i)
        if missing:
            enc = self.model.encode([chunks[i]["text"] for i in missing], normalize_embeddings=True,
                                    show_progress_bar=False).astype(np.float32)
            for j, i in enumerate(missing):
                vectors[i] = enc[j]
            store.save_embeddings(db_path, [(chunks[i]["id"], enc[j].tobytes()) for j, i in enumerate(missing)], model_name)
        self.matrix = np.vstack(vectors).astype(np.float32)
        self.faiss = None
        try:
            import faiss
            self.faiss = faiss.IndexFlatIP(self.matrix.shape[1])
            self.faiss.add(self.matrix)
        except Exception:
            self.faiss = None

    def similarities(self, query):
        q = self.model.encode([query], normalize_embeddings=True).astype(np.float32)
        if self.faiss is not None:
            sims, idx = self.faiss.search(q, self.matrix.shape[0])
            out = np.zeros(self.matrix.shape[0])
            out[idx[0]] = sims[0]
            return out
        return (self.matrix @ q[0])


_CACHE = {}


class Retriever:
    def __init__(self, db_path, role, settings):
        self.db_path, self.role, self.settings = db_path, role, settings
        self.backend_note = ""
        key = (db_path, role, settings.embeddings, settings.embed_model, store.kb_version(db_path))
        cached = _CACHE.get((db_path, role))
        if cached and cached["key"] == key:
            self.__dict__.update(cached["state"])
            return
        self.chunks = store.load_chunks(db_path, role)
        self.bm25 = self.backend = None
        self.stems, self.body_stems = [], []
        if self.chunks:
            texts = [f"{c['title']} {c['title']} {c['section'] or ''} {c['text']}" for c in self.chunks]
            self.stems = [set(tokens(t)) for t in texts]
            self.body_stems = [set(tokens(f"{'' if (c['section'] or '').strip() == c['title'].strip() else (c['section'] or '')} {c['text']}"))
                               for c in self.chunks]
            self.bm25 = BM25([tokens(t) for t in texts])
            self.backend = self._build_backend(texts)
        state = {k: self.__dict__[k] for k in ("chunks", "bm25", "backend", "stems", "body_stems", "backend_note")}
        _CACHE[(db_path, role)] = {"key": key, "state": state}

    def _build_backend(self, texts):
        if self.settings.embeddings == "sentence-transformers":
            try:
                return _SentenceTransformerBackend(self.chunks, self.settings.embed_model, self.db_path)
            except Exception as exc:
                self.backend_note = f"sentence-transformers unavailable ({type(exc).__name__}); using tfidf fallback."
        return _TfidfBackend(texts)

    @property
    def backend_name(self):
        return self.backend.name if self.backend else "none"

    def search(self, query: str, k: int = 6):
        if not self.chunks:
            return []
        q_tokens = tokens(query)
        if not q_tokens:
            return []
        bm = self.bm25.scores(q_tokens)
        dn = self.backend.similarities(query)
        order_bm = {i: r for r, i in enumerate(np.argsort(-bm)) if bm[i] > 0}
        order_dn = {i: r for r, i in enumerate(np.argsort(-dn))}
        fused = {}
        for i in set(order_bm) | set(order_dn):
            fused[i] = (1 / (RRF_K + order_bm[i]) if i in order_bm else 0) + 1 / (RRF_K + order_dn[i]) * 0.8
        semantic = self.settings.embeddings == "sentence-transformers" and "sentence" in self.backend_name
        hits = []
        q_set = set(q_tokens)
        for i in sorted(fused, key=fused.get, reverse=True)[:k * 2]:
            cov = len(q_set & self.stems[i]) / len(q_set)
            if semantic:
                relevant = cov >= 0.4 or dn[i] >= 0.45
            else:
                relevant = (cov >= 0.5 and bm[i] > 0) or (cov >= 0.34 and len(q_set) >= 3 and bm[i] > 0) \
                    or (len(q_set) == 1 and cov == 1)
            c = self.chunks[i]
            hits.append(Hit(c["id"], c["doc_id"], c["title"], c["doc_type"], c["page"], c["section"] or "",
                            c["text"], float(bm[i]), float(dn[i]), round(cov, 2), float(fused[i]), bool(relevant),
                            round(len(q_set & self.body_stems[i]) / len(q_set), 2)))
        hits = self._order_within_documents(hits[:k])
        return self._focus_on_named_document(q_set, hits)

    @staticmethod
    def _order_within_documents(hits):
        """Titles are indexed into every chunk, so inside one document all chunks tie on coverage.
        Keep documents in their fused-rank order, but order the passages INSIDE each document by how
        well their own section + text match the question."""
        order, groups = [], {}
        for h in hits:
            if h.doc_id not in groups:
                order.append(h.doc_id)
            groups.setdefault(h.doc_id, []).append(h)
        out = []
        for d in order:
            out += sorted(groups[d], key=lambda h: (-h.body_coverage, -h.score))
        return out

    def _focus_on_named_document(self, q_set, hits):
        """If the question names a specific document subject (e.g. 'metformin'), keep only that
        document's chunks as *relevant* so a side-effect question about metformin never cites
        the amoxicillin leaflet just because both mention 'side effects'."""
        if not hits:
            return hits
        titles = {}
        for c in self.chunks:
            titles.setdefault(c["doc_id"], set(tokens(c["title"])))
        n_docs = len(titles) or 1
        df = Counter(t for ts in titles.values() for t in ts)
        specific = {t for t, f in df.items() if f / n_docs <= 0.25}
        named = {d for d, ts in titles.items() if q_set & ts & specific}
        if named:
            best_named = max((h.coverage for h in hits if h.doc_id in named), default=0)
            best_other = max((h.coverage for h in hits if h.doc_id not in named), default=0)
            # only focus when the named document ALSO matches the question at least as well —
            # otherwise a generic word that happens to be in one title would hijack the results
            if best_named and best_named >= best_other - 0.01:
                for h in hits:
                    if h.doc_id not in named:
                        h.relevant = False
                hits.sort(key=lambda h: (not h.relevant, -h.score))
        return hits

"""Persistence for the knowledge base (kb_documents / kb_chunks)."""
from .. import permissions
from ..db import connect_rw
from . import chunker, ingest


def add_document(db_path, filename, data, title=None, doc_type="reference", min_role="staff",
                 uploaded_by="", chunk_words=140, overlap=30):
    """Ingest a file. Returns {'id', 'title', 'chunks', 'duplicate'}."""
    if doc_type not in {"leaflet", "sop", "reference"}:
        raise ingest.IngestError("Document type must be leaflet, sop or reference.")
    if min_role not in permissions.ROLE_RANK:
        raise ingest.IngestError("Invalid visibility level.")
    digest = ingest.file_hash(data)
    conn = connect_rw(db_path)
    try:
        existing = conn.execute("SELECT id, title FROM kb_documents WHERE source_hash=?", (digest,)).fetchone()
        if existing:
            return {"id": existing["id"], "title": existing["title"], "chunks": 0, "duplicate": True}
        blocks = ingest.extract_blocks(filename, data)
        chunks = chunker.chunk_blocks(blocks, chunk_words, overlap)
        if not chunks:
            raise ingest.IngestError("The document produced no searchable text.")
        title = (title or "").strip() or _guess_title(filename, blocks)
        cur = conn.execute(
            "INSERT INTO kb_documents (title, filename, doc_type, min_role, source_hash, uploaded_by, chunk_count) "
            "VALUES (?,?,?,?,?,?,?)", (title[:200], filename, doc_type, min_role, digest, uploaded_by, len(chunks)))
        doc_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO kb_chunks (doc_id, chunk_index, page, section, text) VALUES (?,?,?,?,?)",
            [(doc_id, i, c["page"], c["section"], c["text"]) for i, c in enumerate(chunks)])
        conn.commit()
        return {"id": doc_id, "title": title, "chunks": len(chunks), "duplicate": False}
    finally:
        conn.close()


def _guess_title(filename, blocks):
    first = blocks[0]["section"] or blocks[0]["text"].splitlines()[0]
    first = first.strip(" #*")
    return first[:120] if 4 <= len(first) <= 120 else filename.rsplit(".", 1)[0].replace("_", " ").title()


def delete_document(db_path, doc_id):
    conn = connect_rw(db_path)
    try:
        conn.execute("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
        cur = conn.execute("DELETE FROM kb_documents WHERE id=?", (doc_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_documents(db_path, role):
    roles = permissions.visible_doc_roles(role)
    conn = connect_rw(db_path)
    try:
        q = ",".join("?" * len(roles))
        rows = conn.execute(
            f"SELECT id, title, filename, doc_type, min_role, uploaded_by, chunk_count, created_at "
            f"FROM kb_documents WHERE min_role IN ({q}) ORDER BY doc_type, title", roles).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_chunks(db_path, role):
    """All chunks the role may read, with document metadata."""
    roles = permissions.visible_doc_roles(role)
    conn = connect_rw(db_path)
    try:
        q = ",".join("?" * len(roles))
        rows = conn.execute(
            f"SELECT c.id, c.doc_id, c.chunk_index, c.page, c.section, c.text, c.embedding, c.embed_model, "
            f"d.title, d.doc_type FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id "
            f"WHERE d.min_role IN ({q}) ORDER BY c.id", roles).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def kb_version(db_path):
    """Cheap fingerprint used to invalidate in-memory indexes."""
    conn = connect_rw(db_path)
    try:
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM kb_chunks").fetchone()
        d = conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM kb_documents").fetchone()
        return (r[0], r[1], d[0], d[1])
    finally:
        conn.close()


def save_embeddings(db_path, items, model_name):
    """items: [(chunk_id, bytes)]"""
    conn = connect_rw(db_path)
    try:
        conn.executemany("UPDATE kb_chunks SET embedding=?, embed_model=? WHERE id=?",
                         [(b, model_name, cid) for cid, b in items])
        conn.commit()
    finally:
        conn.close()

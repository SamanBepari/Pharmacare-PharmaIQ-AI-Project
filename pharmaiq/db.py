"""
Database helpers for PharmaIQ.

PharmaIQ ADDS four tables to the existing pharmacy.db and never alters the
original tables:

    kb_documents   – uploaded leaflets / SOPs (metadata)
    kb_chunks      – searchable text chunks of those documents
    ai_audit_log   – one row per question asked
    ai_results     – result sets kept for export / follow-up questions
    ai_context     – tiny per-conversation state used for follow-ups
"""
import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    filename     TEXT,
    doc_type     TEXT NOT NULL DEFAULT 'reference',   -- leaflet | sop | reference
    min_role     TEXT NOT NULL DEFAULT 'staff',       -- staff | pharmacist | admin
    source_hash  TEXT UNIQUE,
    uploaded_by  TEXT,
    chunk_count  INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER NOT NULL,
    chunk_index  INTEGER NOT NULL,
    page         INTEGER,
    section      TEXT,
    text         TEXT NOT NULL,
    embedding    BLOB,
    embed_model  TEXT,
    FOREIGN KEY (doc_id) REFERENCES kb_documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(doc_id);

CREATE TABLE IF NOT EXISTS ai_audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    username     TEXT,
    role         TEXT,
    question     TEXT,
    route        TEXT,          -- tool | sql | rag | refused | help | error
    tool         TEXT,
    sql_text     TEXT,
    sql_params   TEXT,
    row_count    INTEGER,
    evidence     TEXT,          -- JSON summary (columns, doc ids…) – never full rows
    llm_used     TEXT,          -- offline | provider:model
    status       TEXT,          -- ok | refused | error | no_answer
    error        TEXT,
    latency_ms   INTEGER,
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_audit_created ON ai_audit_log(created_at);

CREATE TABLE IF NOT EXISTS ai_results (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER,
    username     TEXT,
    title        TEXT,
    payload      TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_context (
    conv_id      TEXT PRIMARY KEY,
    user_id      INTEGER,
    state        TEXT,
    updated_at   TEXT DEFAULT (datetime('now'))
);
"""


def connect_rw(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_ai_tables(path: str) -> None:
    conn = connect_rw(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def prune_old(path: str, ttl_hours: int = 48) -> None:
    """Drop stored result sets / conversation state older than ttl_hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect_rw(path)
    try:
        conn.execute("DELETE FROM ai_results WHERE created_at < ?", (cutoff,))
        conn.execute("DELETE FROM ai_context WHERE updated_at < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()

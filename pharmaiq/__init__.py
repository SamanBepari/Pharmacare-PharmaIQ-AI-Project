"""
PharmaIQ — the AI layer of PharmaCare.

    from pharmaiq import register_pharmaiq
    register_pharmaiq(app, DATABASE)

Sub-modules
    agent       orchestrator (safety → plan → execute → compose → audit)
    planner     rule-based + LLM planners
    tools       controlled tool functions (fixed parameterised SQL)
    sql_guard   5-layer read-only SQL validation / execution
    nl2sql      rule-based text-to-SQL for offline mode
    rag         ingest, chunk, hybrid retrieval, cited answers
    analytics   forecasting + anomaly detection (pandas)
    safety      clinical-advice / credential boundary + PII redaction
    llm         optional Anthropic / OpenAI-compatible providers
    evaluate    RAG / SQL / safety evaluation harness
"""
__version__ = "1.0.0"


def register_pharmaiq(app, db_path, low_stock=10, expiry_days=30):
    from .routes import register
    register(app, db_path, low_stock, expiry_days)

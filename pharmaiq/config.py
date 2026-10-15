"""
PharmaIQ configuration.

Every setting can be overridden with an environment variable, so the same code
runs fully offline (default) or against a hosted / local LLM without edits.
"""
import os
from dataclasses import dataclass
from datetime import date


def today() -> date:
    """Current date. Can be pinned with PHARMAIQ_TODAY=YYYY-MM-DD (used by tests/evals)."""
    override = os.environ.get("PHARMAIQ_TODAY")
    if override:
        return date.fromisoformat(override)
    return date.today()


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # ── LLM ──────────────────────────────────────────────────────────
    provider: str          # offline | anthropic | openai
    api_key: str
    model: str
    base_url: str          # only for openai-compatible servers (OpenAI, Ollama, LM Studio, Groq…)
    llm_timeout: float
    llm_max_rows: int      # how many result rows may be shown to the LLM (0 = aggregates only)
    # ── RAG ──────────────────────────────────────────────────────────
    embeddings: str        # tfidf | sentence-transformers
    embed_model: str
    chunk_words: int
    chunk_overlap: int
    upload_max_mb: int
    # ── SQL safety ───────────────────────────────────────────────────
    max_rows: int
    sql_timeout: float
    # ── Business rules (kept identical to app.py) ────────────────────
    low_stock_threshold: int
    expiry_warn_days: int
    results_ttl_hours: int


def get_settings(low_stock_threshold=None, expiry_warn_days=None) -> Settings:
    provider = os.environ.get("PHARMAIQ_LLM_PROVIDER", "offline").strip().lower()
    api_key = (os.environ.get("PHARMAIQ_API_KEY")
               or (os.environ.get("ANTHROPIC_API_KEY") if provider == "anthropic" else "")
               or (os.environ.get("OPENAI_API_KEY") if provider == "openai" else "")
               or "")
    default_model = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}.get(provider, "")
    default_base = {"openai": "https://api.openai.com/v1"}.get(provider, "")
    return Settings(
        provider=provider,
        api_key=api_key,
        model=os.environ.get("PHARMAIQ_MODEL", default_model),
        base_url=os.environ.get("PHARMAIQ_BASE_URL", default_base).rstrip("/"),
        llm_timeout=_float("PHARMAIQ_LLM_TIMEOUT", 30),
        llm_max_rows=_int("PHARMAIQ_LLM_MAX_ROWS", 15),
        embeddings=os.environ.get("PHARMAIQ_EMBEDDINGS", "tfidf").strip().lower(),
        embed_model=os.environ.get("PHARMAIQ_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        chunk_words=_int("PHARMAIQ_CHUNK_WORDS", 140),
        chunk_overlap=_int("PHARMAIQ_CHUNK_OVERLAP", 30),
        upload_max_mb=_int("PHARMAIQ_UPLOAD_MAX_MB", 5),
        max_rows=_int("PHARMAIQ_MAX_ROWS", 200),
        sql_timeout=_float("PHARMAIQ_SQL_TIMEOUT", 3.0),
        low_stock_threshold=low_stock_threshold if low_stock_threshold is not None
        else _int("PHARMAIQ_LOW_STOCK", 10),
        expiry_warn_days=expiry_warn_days if expiry_warn_days is not None
        else _int("PHARMAIQ_EXPIRY_DAYS", 30),
        results_ttl_hours=_int("PHARMAIQ_RESULTS_TTL_HOURS", 48),
    )

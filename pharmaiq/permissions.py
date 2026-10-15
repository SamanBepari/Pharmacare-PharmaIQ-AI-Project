"""
Role-aware data access for PharmaIQ.

This is the single source of truth for:
  * which tables / columns each role may read through the AI layer,
  * which rows are visible (row-level filters),
  * which AI pages / tools each role may use.

The `users` table (password hashes!) and `sqlite_*` internals are NEVER exposed.
"""

MEDICINE_COLUMNS = {
    "id", "ref_no", "company_name", "medicine_type", "medicine_name", "lot_no",
    "issue_date", "exp_date", "uses", "side_effect", "precautions", "dosage",
    "price", "product_qty", "barcode", "created_at", "updated_at",
}
SALES_COLUMNS = {
    "id", "medicine_id", "medicine_name", "quantity", "unit_price",
    "total_price", "sold_by", "sale_date",
}
# ip_address / user_id are intentionally excluded from the AI view of the logs.
LOG_COLUMNS = {"id", "username", "action", "details", "created_at"}

ROLE_SCHEMA = {
    "admin": {
        "medicines": MEDICINE_COLUMNS,
        "sales": SALES_COLUMNS,
        "activity_logs": LOG_COLUMNS,
    },
    "pharmacist": {
        "medicines": MEDICINE_COLUMNS,
        "sales": SALES_COLUMNS,
    },
    "staff": {
        "medicines": MEDICINE_COLUMNS,
        "sales": SALES_COLUMNS,        # but only their OWN rows (see ROW_FILTERS)
    },
}

# role -> {table: column that must equal the logged-in username}
ROW_FILTERS = {
    "staff": {"sales": "sold_by"},
}

ROLE_RANK = {"staff": 1, "pharmacist": 2, "admin": 3}

PAGE_ROLES = {
    "chat": {"admin", "pharmacist", "staff"},
    "knowledge": {"admin", "pharmacist", "staff"},
    "knowledge_upload": {"admin", "pharmacist"},
    "knowledge_delete": {"admin"},
    "insights": {"admin", "pharmacist"},
    "audit": {"admin"},
    "evaluation": {"admin"},
}

TABLE_DESCRIPTIONS = {
    "medicines": "Inventory. One row per medicine batch. medicine_type is the CATEGORY "
                 "(Tablet, Capsule, Syrup, Injection…). product_qty is units in stock. "
                 "exp_date / issue_date are ISO dates (YYYY-MM-DD).",
    "sales": "One row per sale. sale_date is 'YYYY-MM-DD HH:MM:SS'. medicine_id references medicines.id.",
    "activity_logs": "Audit of user actions in the application.",
}


def schema_for(role: str) -> dict:
    """Return {table: set(columns)} visible to `role` (unknown role → nothing)."""
    return {t: set(c) for t, c in ROLE_SCHEMA.get(role, {}).items()}


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum, 99)


def visible_doc_roles(role: str) -> list:
    """Which kb_documents.min_role values a given role may read."""
    return [r for r, rank in ROLE_RANK.items() if rank <= ROLE_RANK.get(role, 0)]


def can_access_page(role: str, page: str) -> bool:
    return role in PAGE_ROLES.get(page, set())


def schema_prompt(role: str) -> str:
    """Compact schema description given to the LLM (only what the role may see)."""
    lines = []
    for table, cols in schema_for(role).items():
        lines.append(f"TABLE {table}({', '.join(sorted(cols))})  -- {TABLE_DESCRIPTIONS.get(table, '')}")
    if role in ROW_FILTERS:
        for t in ROW_FILTERS[role]:
            lines.append(f"NOTE: {t} rows are restricted to the current user's own records.")
    return "\n".join(lines)

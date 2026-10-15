"""
SQL guard — the safety core of PharmaIQ's text-to-SQL.

Nothing the LLM (or the rule engine) produces is ever executed directly. Every
statement goes through FIVE independent layers; an attack has to defeat all of them:

  1. Lexical validation   – tokenises the SQL (strings/identifiers/comments aware),
                            allows exactly ONE statement that starts with SELECT/WITH,
                            rejects comments, DDL/DML keywords, PRAGMA, ATTACH, sqlite_*
                            internals, schema prefixes (main./temp.), unknown functions.
  2. Authorizer           – SQLite's own `set_authorizer` hook vets every table/column
                            read at compile time against the role's allow-list.
  3. Row-level filters    – for restricted roles the base table is shadowed by a TEMP VIEW
                            that only exposes that user's rows (e.g. staff → own sales).
  4. Read-only connection – opened with mode=ro (fallback: PRAGMA query_only=ON).
  5. Resource limits      – row cap (fetchmany) and a wall-clock timeout via a
                            progress handler.
"""
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import permissions

MAX_SQL_LENGTH = 2500
MAX_TOKENS = 700

FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "ATTACH",
    "DETACH", "PRAGMA", "VACUUM", "REINDEX", "ANALYZE", "BEGIN", "COMMIT", "ROLLBACK",
    "SAVEPOINT", "RELEASE", "TRIGGER", "EXPLAIN", "TRUNCATE", "UPSERT", "INTO",
    "LOAD_EXTENSION", "RECURSIVE",
}

ALLOWED_FUNCTIONS = {
    # aggregates
    "count", "sum", "avg", "min", "max", "total", "group_concat",
    # scalar
    "round", "abs", "coalesce", "ifnull", "nullif", "iif", "lower", "upper",
    "length", "trim", "ltrim", "rtrim", "substr", "substring", "instr", "printf",
    "typeof", "cast",
    # dates
    "date", "datetime", "julianday", "strftime", "time",
    # SQLite reports the LIKE / GLOB operators to the authorizer as function calls
    "like", "glob", "likely", "unlikely", "likelihood",
}

# Words that may legitimately be followed by "(" but are not function calls.
NON_FUNCTION_WORDS = {
    "IN", "AND", "OR", "NOT", "ON", "FROM", "AS", "SELECT", "WHERE", "VALUES", "OVER",
    "WHEN", "THEN", "ELSE", "CASE", "EXISTS", "JOIN", "USING", "BY", "HAVING", "UNION",
    "ALL", "INTERSECT", "EXCEPT", "WITH", "BETWEEN", "LIKE", "GLOB", "IS", "ASC", "DESC",
    "DISTINCT", "LIMIT", "OFFSET", "ORDER", "GROUP", "END", "NULL", "LEFT", "INNER",
    "CROSS", "OUTER", "NATURAL", "RECURSIVE", "COLLATE", "ESCAPE", "ANY", "SOME",
}


class SQLValidationError(Exception):
    """Raised when SQL is rejected. `code` is machine readable, message is user safe."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class QueryResult:
    columns: list
    rows: list
    truncated: bool
    sql: str
    params: dict
    tables_used: list = field(default_factory=list)
    columns_used: list = field(default_factory=list)
    elapsed_ms: int = 0


# ──────────────────────────────────────────────────────────────────────
#  Layer 1 – lexical validation
# ──────────────────────────────────────────────────────────────────────
def tokenize(sql: str):
    """Very small SQL scanner. Returns [(kind, text)] with kind in
    word | str | qident | num | param | punct. Raises on comments / bad quoting."""
    tokens = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if sql.startswith("--", i) or sql.startswith("/*", i):
            raise SQLValidationError("comment", "SQL comments are not allowed.")
        if ch == "'":
            j = i + 1
            while True:
                if j >= n:
                    raise SQLValidationError("syntax", "Unterminated string literal.")
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            tokens.append(("str", sql[i:j + 1]))
            i = j + 1
            continue
        if ch in '"`[':
            close = {'"': '"', "`": "`", "[": "]"}[ch]
            j = sql.find(close, i + 1)
            if j == -1:
                raise SQLValidationError("syntax", "Unterminated quoted identifier.")
            tokens.append(("qident", sql[i + 1:j]))
            i = j + 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] in "_$"):
                j += 1
            tokens.append(("word", sql[i:j]))
            i = j
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            j = i + 1
            while j < n and (sql[j].isdigit() or sql[j] == "."):
                j += 1
            tokens.append(("num", sql[i:j]))
            i = j
            continue
        if ch == ":":
            m = re.match(r":[A-Za-z_][A-Za-z0-9_]*", sql[i:])
            if not m:
                raise SQLValidationError("syntax", "Invalid parameter.")
            tokens.append(("param", m.group(0)))
            i += len(m.group(0))
            continue
        if ch in "?@$":
            raise SQLValidationError("syntax", "Only named parameters like :name are allowed.")
        if ch in "(),.*+-/%<>=!|;":
            # two-char operators
            two = sql[i:i + 2]
            if two in ("<=", ">=", "<>", "!=", "||", "=="):
                tokens.append(("punct", two))
                i += 2
            else:
                tokens.append(("punct", ch))
                i += 1
            continue
        raise SQLValidationError("syntax", f"Unexpected character {ch!r}.")
    return tokens


def validate_sql(sql: str) -> str:
    """Lexical checks. Returns the cleaned SQL (single trailing ';' removed)."""
    if not isinstance(sql, str) or not sql.strip():
        raise SQLValidationError("empty", "No SQL was provided.")
    sql = sql.strip()
    if len(sql) > MAX_SQL_LENGTH:
        raise SQLValidationError("too_long", "The query is too long.")
    tokens = tokenize(sql)
    if len(tokens) > MAX_TOKENS:
        raise SQLValidationError("too_long", "The query is too complex.")

    # one statement only; a trailing ';' is tolerated
    while tokens and tokens[-1] == ("punct", ";"):
        tokens.pop()
        sql = sql.rstrip().rstrip(";").rstrip()
    if any(t == ("punct", ";") for t in tokens):
        raise SQLValidationError("multi_statement", "Only a single SQL statement is allowed.")
    if not tokens:
        raise SQLValidationError("empty", "No SQL was provided.")

    first = tokens[0]
    if first[0] != "word" or first[1].upper() not in ("SELECT", "WITH"):
        raise SQLValidationError("not_select", "Only read-only SELECT queries are allowed.")

    for idx, (kind, text) in enumerate(tokens):
        if kind == "word":
            up = text.upper()
            if up in FORBIDDEN_KEYWORDS:
                raise SQLValidationError("forbidden_keyword", f"The keyword {up} is not allowed.")
            if text.lower().startswith("sqlite_"):
                raise SQLValidationError("internal_table", "Access to SQLite internals is not allowed.")
            nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
            if nxt == ("punct", "(") and up not in NON_FUNCTION_WORDS:
                if text.lower() not in ALLOWED_FUNCTIONS:
                    raise SQLValidationError("function", f"The function {text}() is not allowed.")
        if kind in ("word", "qident"):
            low = text.lower()
            if low.startswith("sqlite_"):
                raise SQLValidationError("internal_table", "Access to SQLite internals is not allowed.")
            nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
            if low in ("main", "temp") and nxt == ("punct", "."):
                raise SQLValidationError("schema_prefix", "Schema-qualified names are not allowed.")
    return sql


# ──────────────────────────────────────────────────────────────────────
#  Layers 2–5 – restricted, read-only execution
# ──────────────────────────────────────────────────────────────────────
def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class ReadOnlySession:
    """A read-only SQLite connection locked down for one user/role.

        with ReadOnlySession(db_path, role="staff", username="jane") as s:
            result = s.execute("SELECT medicine_name FROM medicines WHERE product_qty < :n", {"n": 10})
    """

    def __init__(self, db_path, role, username="", max_rows=200, timeout_s=3.0):
        self.db_path = str(db_path)
        self.role = role
        self.username = username or ""
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self.schema = permissions.schema_for(role)
        self._used_tables, self._used_columns, self._denied = set(), set(), []
        self._deadline = None
        self.conn = self._open()

    # -- lifecycle -------------------------------------------------------
    def _open(self):
        path = Path(self.db_path).resolve()
        conn = None
        try:
            conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.Error:
            if conn:
                conn.close()
            conn = sqlite3.connect(str(path), timeout=5)
        # Layer 3 — row-level filters via TEMP views that shadow the base tables
        for table, col in permissions.ROW_FILTERS.get(self.role, {}).items():
            conn.execute(
                f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} "
                f"WHERE {col} = {_sql_literal(self.username)}"
            )
        # Layer 4 — read-only
        conn.execute("PRAGMA query_only = ON")
        # Layer 2 — authorizer
        conn.set_authorizer(self._authorizer)
        # Layer 5 — timeout
        conn.set_progress_handler(self._progress, 20000)
        return conn

    def close(self):
        try:
            self.conn.set_authorizer(None)
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- hooks -----------------------------------------------------------
    def _progress(self):
        if self._deadline is not None and time.monotonic() > self._deadline:
            return 1  # abort
        return 0

    def _authorizer(self, action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            allowed = self.schema.get(table)
            if allowed is None:
                self._denied.append((table, None))
                return sqlite3.SQLITE_DENY
            if column and column not in allowed and column != "rowid":
                self._denied.append((table, column))
                return sqlite3.SQLITE_DENY
            self._used_tables.add(table)
            if column:
                self._used_columns.add(f"{table}.{column}")
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").lower() in ALLOWED_FUNCTIONS:
                return sqlite3.SQLITE_OK
            self._denied.append(("function", arg2))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _describe_denied(table, column):
        if table == "function":
            return f"{column}()"
        return table if column is None else f"{table}.{column}"

    # -- public API ------------------------------------------------------
    def execute(self, sql: str, params: dict = None) -> QueryResult:
        params = params or {}
        clean = validate_sql(sql)
        self._used_tables.clear()
        self._used_columns.clear()
        self._denied.clear()
        start = time.monotonic()
        self._deadline = start + self.timeout_s
        try:
            cur = self.conn.execute(clean, params)
            columns = [d[0] for d in (cur.description or [])]
            fetched = cur.fetchmany(self.max_rows + 1)
        except sqlite3.DatabaseError as exc:
            msg = str(exc).lower()
            if "not authorized" in msg or self._denied:
                what = ", ".join(self._describe_denied(t, c) for t, c in self._denied[:4]) or "that data"
                raise SQLValidationError(
                    "not_authorized",
                    f"Your role ({self.role}) is not allowed to read {what}.") from exc
            if "interrupted" in msg:
                raise SQLValidationError("timeout", "The query took too long and was stopped.") from exc
            raise SQLValidationError("sql_error", f"The query could not be run: {exc}") from exc
        except sqlite3.Warning as exc:
            raise SQLValidationError("sql_error", str(exc)) from exc
        finally:
            self._deadline = None
        truncated = len(fetched) > self.max_rows
        rows = [list(r) for r in fetched[: self.max_rows]]
        return QueryResult(
            columns=columns, rows=rows, truncated=truncated, sql=clean, params=dict(params),
            tables_used=sorted(self._used_tables), columns_used=sorted(self._used_columns),
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    def query_df(self, sql: str, params: dict = None):
        import pandas as pd
        res = self.execute(sql, params)
        return pd.DataFrame(res.rows, columns=res.columns)

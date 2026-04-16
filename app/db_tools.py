from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any

from langchain_core.tools import tool


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
MAX_SQL_CHARS = 5000
MAX_SQL_ROWS = 100
MAX_VM_STEPS = 1_500_000
LOGGER = logging.getLogger(__name__)
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "what",
    "when",
    "where",
    "which",
    "who",
    "how",
    "after",
    "before",
    "between",
    "pilot",
    "issue",
    "customer",
}


def _open_connection(db_path: str) -> sqlite3.Connection:
    # Open in read-only mode to enforce least privilege at the DB layer.
    db_uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(db_uri, check_same_thread=False, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _to_fts_query(raw: str) -> str:
    tokens = _keyword_tokens(raw)
    deduped: list[str] = []
    for token in tokens:
        if len(token) >= 3 and token not in deduped:
            deduped.append(token)
    if not deduped:
        return ""
    # Use AND to keep results precise while avoiding FTS parse issues.
    return " AND ".join(f'"{token}"' for token in deduped[:12])


def _keyword_tokens(raw: str) -> list[str]:
    tokens = TOKEN_RE.findall(raw.lower())
    return [t for t in tokens if len(t) >= 3 and t not in STOPWORDS]


def _assert_read_only_sql(query: str) -> None:
    if len(query) > MAX_SQL_CHARS:
        raise ValueError(f"Query is too long (max {MAX_SQL_CHARS} characters).")

    stripped = query.strip().rstrip(";").strip()
    if ";" in stripped:
        raise ValueError("Multiple statements are not allowed.")
    if "--" in stripped or "/*" in stripped or "*/" in stripped:
        raise ValueError("SQL comments are not allowed.")

    q = stripped.lower()
    blocked = (
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "replace",
        "truncate",
        "attach",
        "detach",
        "vacuum",
        "pragma",
        "load_extension",
        "reindex",
        "analyze",
    )
    if any(re.search(rf"\b{token}\b", q) for token in blocked):
        raise ValueError("Only read-only SELECT queries are allowed.")
    if not q.startswith("select"):
        raise ValueError("Query must start with SELECT.")


def _normalize_sql(query: str) -> str:
    return query.strip().rstrip(";").strip()


@contextmanager
def _vm_step_guard(conn: sqlite3.Connection):
    steps = {"count": 0}

    def progress_handler() -> int:
        steps["count"] += 1
        if steps["count"] > MAX_VM_STEPS:
            return 1
        return 0

    conn.set_progress_handler(progress_handler, 1000)
    try:
        yield
    finally:
        conn.set_progress_handler(None, 0)


def build_db_tools(db_path: str) -> list:
    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(
            f"SQLite database not found at {db_path}. Set APP_SQLITE_PATH correctly."
        )

    @tool
    def list_tables() -> list[str]:
        """List available SQLite user tables."""
        started = perf_counter()
        with _open_connection(db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
        table_names = [r["name"] for r in rows]
        LOGGER.info("tool=list_tables latency_ms=%d tables=%d", int((perf_counter() - started) * 1000), len(table_names))
        return table_names

    @tool
    def find_customers(name_query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Find customer names (supports fuzzy partial matching)."""
        started = perf_counter()
        lim = max(1, min(limit, 100))
        phrase = name_query.strip().lower()
        tokens = _keyword_tokens(name_query)
        with _open_connection(db_path) as conn:
            rows = conn.execute(
                """
                SELECT customer_id, name, region, country, industry, account_health, notes
                FROM customers
                ORDER BY name
                """,
            ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            hay_name = (item.get("name") or "").lower()
            hay_region = (item.get("region") or "").lower()
            hay_country = (item.get("country") or "").lower()
            hay_notes = (item.get("notes") or "").lower()
            score = 0

            if phrase and phrase in hay_name:
                score += 80
            if phrase and phrase in hay_notes:
                score += 20

            for token in tokens:
                if token in hay_name:
                    score += 20
                if token in hay_region or token in hay_country:
                    score += 8
                if token in hay_notes:
                    score += 5

            if score > 0 or not phrase:
                item["match_score"] = score
                item.pop("notes", None)
                scored.append(item)

        scored.sort(key=lambda x: (x.get("match_score", 0), x.get("name", "")), reverse=True)
        payload = scored[:lim]
        LOGGER.info(
            "tool=find_customers latency_ms=%d query=%s rows=%d",
            int((perf_counter() - started) * 1000),
            name_query.strip(),
            len(payload),
        )
        return payload

    @tool
    def search_artifacts(search_query: str, limit: int = 15) -> list[dict[str, Any]]:
        """Semantic-ish keyword search over artifacts using SQLite FTS."""
        started = perf_counter()
        if not search_query.strip():
            return [{"error": "search_query cannot be empty"}]
        lim = max(1, min(limit, 50))
        fts_query = _to_fts_query(search_query)
        if not fts_query:
            return [{"error": "search_query has no usable tokens"}]
        with _open_connection(db_path) as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT
                        a.artifact_id,
                        a.title,
                        a.created_at,
                        snippet(artifacts_fts, -1, '[', ']', ' ... ', 16) AS snippet
                    FROM artifacts_fts
                    JOIN artifacts a ON a.artifact_id = artifacts_fts.rowid
                    WHERE artifacts_fts MATCH ?
                    ORDER BY bm25(artifacts_fts)
                    LIMIT ?
                    """,
                    (fts_query, lim),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                return [{"error": f"FTS search error: {exc}"}]
        payload = [dict(r) for r in rows]
        LOGGER.info(
            "tool=search_artifacts latency_ms=%d query_chars=%d rows=%d",
            int((perf_counter() - started) * 1000),
            len(search_query),
            len(payload),
        )
        return payload

    @tool
    def get_customer_artifacts(customer_query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent artifacts for customer name/alias matches."""
        started = perf_counter()
        lim = max(1, min(limit, 50))
        candidates = find_customers.invoke({"name_query": customer_query, "limit": 5})
        if not candidates:
            return [{"error": f"No customer candidates found for '{customer_query}'"}]

        customer_ids = [c["customer_id"] for c in candidates if c.get("customer_id")]
        if not customer_ids:
            return [{"error": f"No customer IDs found for '{customer_query}'"}]

        placeholders = ",".join("?" for _ in customer_ids)
        with _open_connection(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    c.name AS customer_name,
                    a.artifact_id,
                    a.title,
                    a.artifact_type,
                    a.created_at,
                    a.summary,
                    substr(a.content_text, 1, 900) AS content_excerpt
                FROM artifacts a
                JOIN customers c ON c.customer_id = a.customer_id
                WHERE a.customer_id IN ({placeholders})
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                [*customer_ids, lim],
            ).fetchall()

        payload = [dict(r) for r in rows]
        LOGGER.info(
            "tool=get_customer_artifacts latency_ms=%d query=%s rows=%d",
            int((perf_counter() - started) * 1000),
            customer_query,
            len(payload),
        )
        return payload

    @tool
    def filter_artifacts(required_terms: list[str], customer_query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Filter artifacts that contain all required terms, optionally scoped by fuzzy customer match."""
        started = perf_counter()
        terms = [t.strip().lower() for t in required_terms if t and t.strip()]
        if not terms:
            return [{"error": "required_terms must include at least one term"}]

        lim = max(1, min(limit, 50))
        with _open_connection(db_path) as conn:
            if customer_query.strip():
                candidates = find_customers.invoke({"name_query": customer_query, "limit": 5})
                customer_ids = [c["customer_id"] for c in candidates if c.get("customer_id")]
                if not customer_ids:
                    return [{"error": f"No customer candidates found for '{customer_query}'"}]
                placeholders = ",".join("?" for _ in customer_ids)
                base_sql = f"""
                    SELECT
                        c.name AS customer_name,
                        a.artifact_id,
                        a.title,
                        a.created_at,
                        a.artifact_type,
                        a.summary,
                        substr(a.content_text, 1, 900) AS content_excerpt
                    FROM artifacts a
                    JOIN customers c ON c.customer_id = a.customer_id
                    WHERE a.customer_id IN ({placeholders})
                """
                params: list[Any] = [*customer_ids]
            else:
                base_sql = """
                    SELECT
                        c.name AS customer_name,
                        a.artifact_id,
                        a.title,
                        a.created_at,
                        a.artifact_type,
                        a.summary,
                        substr(a.content_text, 1, 900) AS content_excerpt
                    FROM artifacts a
                    JOIN customers c ON c.customer_id = a.customer_id
                    WHERE 1=1
                """
                params = []

            for term in terms:
                base_sql += " AND lower(a.content_text) LIKE ?"
                params.append(f"%{term}%")

            base_sql += " ORDER BY a.created_at DESC LIMIT ?"
            params.append(lim)
            rows = conn.execute(base_sql, params).fetchall()

        payload = [dict(r) for r in rows]
        LOGGER.info(
            "tool=filter_artifacts latency_ms=%d terms=%d customer_query=%s rows=%d",
            int((perf_counter() - started) * 1000),
            len(terms),
            customer_query,
            len(payload),
        )
        return payload

    @tool
    def find_customer_by_issue_signals(
        exact_date: str, required_terms: list[str], limit: int = 5
    ) -> list[dict[str, Any]]:
        """Find likely customers for issue questions using exact date + required terms in artifacts."""
        started = perf_counter()
        date = exact_date.strip()
        terms = [t.strip().lower() for t in required_terms if t and t.strip()]
        lim = max(1, min(limit, 20))
        if not date:
            return [{"error": "exact_date is required (example: 2026-02-20)."}]
        if not terms:
            return [{"error": "required_terms must include at least one term."}]

        with _open_connection(db_path) as conn:
            sql = """
                SELECT
                    c.customer_id,
                    c.name AS customer_name,
                    count(*) AS matching_artifacts,
                    max(a.created_at) AS latest_artifact_at,
                    group_concat(a.title, ' || ') AS matching_titles
                FROM artifacts a
                JOIN customers c ON c.customer_id = a.customer_id
                WHERE lower(a.content_text) LIKE ?
            """
            params: list[Any] = [f"%{date.lower()}%"]
            for term in terms:
                sql += " AND lower(a.content_text) LIKE ?"
                params.append(f"%{term}%")
            sql += """
                GROUP BY c.customer_id, c.name
                ORDER BY matching_artifacts DESC, latest_artifact_at DESC
                LIMIT ?
            """
            params.append(lim)
            rows = conn.execute(sql, params).fetchall()

        payload = [dict(r) for r in rows]
        LOGGER.info(
            "tool=find_customer_by_issue_signals latency_ms=%d date=%s terms=%d rows=%d",
            int((perf_counter() - started) * 1000),
            date,
            len(terms),
            len(payload),
        )
        return payload

    @tool
    def describe_table(table_name: str) -> dict[str, Any]:
        """Return schema and sample rows for one table."""
        started = perf_counter()
        if not IDENTIFIER_RE.match(table_name):
            return {"error": "Invalid table name format."}
        quoted = f'"{table_name}"'
        with _open_connection(db_path) as conn:
            schema_rows = conn.execute(f"PRAGMA table_info({quoted})").fetchall()
            if not schema_rows:
                return {"error": f"Unknown table: {table_name}"}
            sample_rows = conn.execute(f"SELECT * FROM {quoted} LIMIT 5").fetchall()

        schema = [
            {
                "name": row["name"],
                "type": row["type"],
                "notnull": bool(row["notnull"]),
                "pk": bool(row["pk"]),
            }
            for row in schema_rows
        ]
        samples = [dict(r) for r in sample_rows]
        LOGGER.info(
            "tool=describe_table latency_ms=%d table=%s sample_rows=%d",
            int((perf_counter() - started) * 1000),
            table_name,
            len(samples),
        )
        return {"table": table_name, "schema": schema, "sample_rows": samples}

    @tool
    def run_sql(query: str) -> list[dict[str, Any]]:
        """Run a read-only SELECT query and return up to 100 rows."""
        started = perf_counter()
        normalized = _normalize_sql(query)
        try:
            _assert_read_only_sql(normalized)
        except ValueError as exc:
            return [{"error": str(exc)}]
        wrapped = f"SELECT * FROM ({normalized}) LIMIT {MAX_SQL_ROWS}"
        with _open_connection(db_path) as conn:
            try:
                with _vm_step_guard(conn):
                    rows = conn.execute(wrapped).fetchall()
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc).lower():
                    return [{"error": "Query exceeded safety limits. Please simplify the query."}]
                return [{"error": f"SQL error: {exc}"}]

        payload = [dict(r) for r in rows]
        LOGGER.info(
            "tool=run_sql latency_ms=%d rows=%d query_chars=%d",
            int((perf_counter() - started) * 1000),
            len(payload),
            len(normalized),
        )
        return payload

    return [
        list_tables,
        find_customers,
        get_customer_artifacts,
        filter_artifacts,
        find_customer_by_issue_signals,
        search_artifacts,
        describe_table,
        run_sql,
    ]

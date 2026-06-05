"""SELECT-only SQL validation for text-to-SQL workflows."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


FORBIDDEN_KEYWORDS = {
    "alter",
    "attach",
    "create",
    "delete",
    "detach",
    "drop",
    "exec",
    "insert",
    "merge",
    "pragma",
    "replace",
    "truncate",
    "update",
    "vacuum",
}


def _strip_comments(sql: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", sql or "", flags=re.DOTALL)
    return re.sub(r"--[^\r\n]*", " ", without_block)


def _mask_string_literals(sql: str) -> str:
    chars: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    chars.extend("  ")
                    i += 2
                    continue
                quote = None
            chars.append(" ")
        elif ch in {"'", '"'}:
            quote = ch
            chars.append(" ")
        else:
            chars.append(ch)
        i += 1
    return "".join(chars)


def _contains_multiple_statements(sql: str) -> bool:
    masked = _mask_string_literals(sql).strip()
    if masked.endswith(";"):
        masked = masked[:-1]
    return ";" in masked


def _ai_visibility(connection: sqlite3.Connection | None) -> tuple[set[str], set[str]]:
    if connection is None:
        return set(), set()
    try:
        rows = connection.execute(
            "SELECT table_name, is_ai_visible FROM table_metadata"
        ).fetchall()
    except sqlite3.Error:
        return set(), set()
    visible = {str(row["table_name"]) for row in rows if int(row["is_ai_visible"] or 0) == 1}
    hidden = {str(row["table_name"]) for row in rows if int(row["is_ai_visible"] or 0) == 0}
    return visible, hidden


def _referenced_tables(sql: str) -> set[str]:
    masked = _mask_string_literals(sql)
    cte_names = {
        match.group(1)
        for match in re.finditer(
            r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(",
            masked,
            flags=re.IGNORECASE,
        )
    }
    names: set[str] = set()
    for match in re.finditer(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_\.]*)", masked, flags=re.IGNORECASE):
        name = match.group(1).split(".")[-1]
        if name not in cte_names:
            names.add(name)
    return names


def _apply_limit(sql: str, default_limit: int, max_limit: int) -> tuple[str, bool, str]:
    normalized = sql.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].strip()
    masked = _mask_string_literals(normalized).lower()
    matches = list(re.finditer(r"\blimit\s+(\d+)\b", masked))
    if not matches:
        return f"{normalized} LIMIT {int(default_limit)}", True, "Default LIMIT applied."
    last = matches[-1]
    limit_value = int(last.group(1))
    if limit_value <= max_limit:
        return normalized, False, "OK"
    limited = normalized[: last.start(1)] + str(int(max_limit)) + normalized[last.end(1) :]
    return limited, False, f"LIMIT reduced to {int(max_limit)}."


def validate_sql(
    sql: str,
    *,
    connection: sqlite3.Connection | None = None,
    default_limit: int = 100,
    max_limit: int = 1000,
) -> dict[str, Any]:
    """Validate one SQLite read query before execution."""
    if not isinstance(sql, str) or not sql.strip():
        return {"ok": False, "sql": sql, "message": "SQL must be a non-empty string.", "limit_applied": False}

    comments = re.findall(r"/\*(.*?)\*/", sql, flags=re.DOTALL) + re.findall(r"--([^\r\n]*)", sql)
    for comment in comments:
        if ";" in comment or _has_forbidden_keyword(comment):
            return {
                "ok": False,
                "sql": sql,
                "message": "SQL comments cannot contain hidden statements or dangerous keywords.",
                "limit_applied": False,
            }

    clean = _strip_comments(sql).strip()
    if _contains_multiple_statements(clean):
        return {"ok": False, "sql": clean, "message": "Multiple SQL statements are not allowed.", "limit_applied": False}
    if not re.match(r"^\s*(select|with)\b", clean, flags=re.IGNORECASE):
        return {"ok": False, "sql": clean, "message": "Only SELECT or WITH queries are allowed.", "limit_applied": False}

    keyword = _has_forbidden_keyword(clean)
    if keyword:
        return {"ok": False, "sql": clean, "message": f"Forbidden SQL keyword: {keyword.upper()}.", "limit_applied": False}

    visible_tables, hidden_tables = _ai_visibility(connection)
    referenced_tables = _referenced_tables(clean)
    hidden = sorted(referenced_tables & hidden_tables)
    if hidden:
        return {"ok": False, "sql": clean, "message": "Table is not AI-visible: " + ", ".join(hidden), "limit_applied": False}
    if visible_tables:
        not_visible = sorted(referenced_tables - visible_tables)
        if not_visible:
            return {
                "ok": False,
                "sql": clean,
                "message": "Table is not AI-visible: " + ", ".join(not_visible),
                "limit_applied": False,
            }

    bounded_sql, limit_applied, message = _apply_limit(clean, default_limit, max_limit)
    return {"ok": True, "sql": bounded_sql, "message": message, "limit_applied": limit_applied}


def _has_forbidden_keyword(text: str) -> str | None:
    masked = _mask_string_literals(text).lower()
    for keyword in sorted(FORBIDDEN_KEYWORDS):
        if re.search(rf"\b{re.escape(keyword)}\b", masked):
            return keyword
    return None

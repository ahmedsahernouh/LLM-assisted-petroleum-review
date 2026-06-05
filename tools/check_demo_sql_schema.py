#!/usr/bin/env python
"""Check the demo Text-to-SQL schema context and SQL guard."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_petroleum_review.database import build_demo_database, build_schema_context
from llm_petroleum_review.sql_guard import validate_sql
from llm_petroleum_review.text_to_sql import generate_sql


QUESTIONS = [
    "List wells with coordinates",
    "How many wells are there by status?",
    "How many wells are there by reservoir?",
    "Which wells have production context?",
    "List available layers and grids",
    "Show timeline events",
    "What schema is available?",
]


def _mask_strings(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", " ", str(sql or ""))


def _referenced_columns(sql: str) -> dict[str, set[str]]:
    masked = _mask_strings(sql)
    table_match = re.search(r"\bfrom\s+([A-Za-z_][A-Za-z0-9_]*)", masked, flags=re.IGNORECASE)
    if not table_match:
        return {}
    table_name = table_match.group(1)
    select_match = re.search(r"\bselect\s+(.*?)\s+\bfrom\b", masked, flags=re.IGNORECASE | re.DOTALL)
    if not select_match:
        return {}
    select_list = select_match.group(1)
    if "*" in select_list:
        return {}
    columns: set[str] = set()
    for raw in select_list.split(","):
        token = raw.strip()
        aggregate_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*\((.*?)\)", token)
        if aggregate_match:
            token = aggregate_match.group(1).strip()
        token = re.split(r"\s+as\s+|\s+", token, flags=re.IGNORECASE)[0]
        token = token.split(".")[-1].strip()
        if token and token != "COUNT(*)" and not token.isdigit():
            columns.add(token)
    return {table_name: columns}


def main() -> int:
    connection = build_demo_database()
    context = build_schema_context(connection)
    context_columns = {
        str(table["table_name"]): {
            str(column["column_name"])
            for column in table.get("columns", [])
            if isinstance(column, dict)
        }
        for table in context.get("tables", [])
        if isinstance(table, dict)
    }

    failures: list[str] = []
    for table_name, declared_columns in context_columns.items():
        actual = {
            str(row["name"])
            for row in connection.execute(f'PRAGMA table_info("{table_name.replace(chr(34), chr(34) + chr(34))}")')
        }
        missing = sorted(declared_columns - actual)
        omitted = sorted(actual - declared_columns)
        if missing:
            failures.append(f"{table_name}: context declares missing SQLite columns: {', '.join(missing)}")
        if omitted:
            failures.append(f"{table_name}: context omits SQLite columns: {', '.join(omitted)}")

    for question in QUESTIONS:
        candidate = generate_sql(question, context)
        sql = str(candidate.get("sql") or "")
        validation = validate_sql(sql, connection=connection)
        if not validation.get("ok"):
            failures.append(f"{question}: generated SQL rejected: {validation.get('message')}")
            continue
        for table_name, columns in _referenced_columns(sql).items():
            declared = context_columns.get(table_name)
            if declared is None:
                failures.append(f"{question}: generated SQL references non-context table {table_name}")
                continue
            unknown = sorted(columns - declared)
            if unknown:
                failures.append(f"{question}: generated SQL uses undeclared columns on {table_name}: {', '.join(unknown)}")

    blocked = [
        "SELECT * FROM audit_log",
        "SELECT * FROM table_metadata",
        "SELECT * FROM column_metadata",
        "DROP TABLE wells",
        "SELECT 1; SELECT 2",
        "SELECT 1 /* DELETE FROM wells */",
    ]
    for sql in blocked:
        validation = validate_sql(sql, connection=connection)
        if validation.get("ok"):
            failures.append(f"unsafe SQL unexpectedly passed: {sql}")

    cte_validation = validate_sql(
        "WITH x AS (SELECT well_name FROM v_well_locations) SELECT * FROM x",
        connection=connection,
    )
    if not cte_validation.get("ok"):
        failures.append(f"safe CTE query was rejected: {cte_validation.get('message')}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: demo schema context, generated SQL, and SQL guard are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

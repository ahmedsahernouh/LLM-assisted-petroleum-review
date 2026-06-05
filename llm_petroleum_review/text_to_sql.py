"""Deterministic text-to-SQL demo flow with optional LLM extension points."""

from __future__ import annotations

import sqlite3
from typing import Any

from .database import build_schema_context
from .sql_guard import validate_sql


def generate_sql(question: str, schema_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate a conservative SQL candidate for common petroleum-review questions."""
    text = str(question or "").lower()
    warnings = ["deterministic_fallback_used"]

    if "coordinate" in text or "location" in text:
        return {
            "sql": "SELECT well_name, x_utm, y_utm, crs FROM v_well_locations ORDER BY well_name",
            "reasoning": "Well coordinate questions map to the safe well-location view.",
            "warnings": warnings,
        }
    if "status" in text:
        return {
            "sql": "SELECT status, COUNT(*) AS well_count FROM v_well_locations GROUP BY status ORDER BY well_count DESC",
            "reasoning": "Status questions aggregate wells by status.",
            "warnings": warnings,
        }
    if "reservoir" in text:
        return {
            "sql": "SELECT reservoir, COUNT(*) AS well_count FROM v_well_locations GROUP BY reservoir ORDER BY well_count DESC",
            "reasoning": "Reservoir questions aggregate wells by reservoir.",
            "warnings": warnings,
        }
    if "production" in text:
        return {
            "sql": "SELECT well_name, date_start, date_end, metrics FROM v_production_entities ORDER BY well_name",
            "reasoning": "Production availability questions use compact production context entities.",
            "warnings": warnings,
        }
    if "layer" in text or "grid" in text:
        return {
            "sql": "SELECT display_name, layer_type, category, description FROM v_layer_catalog ORDER BY display_name",
            "reasoning": "Layer and grid questions map to the safe layer catalog.",
            "warnings": warnings,
        }
    if "timeline" in text or "event" in text:
        return {
            "sql": "SELECT event_name, event_date, category, description FROM v_timeline_events ORDER BY event_date",
            "reasoning": "Timeline questions map to compact event context.",
            "warnings": warnings,
        }
    return {
        "sql": "SELECT table_name, display_name, description FROM v_schema_tables ORDER BY table_name",
        "reasoning": "Fallback lists AI-visible schema options instead of inventing SQL.",
        "warnings": warnings + ["question_not_mapped_to_specific_domain_pattern"],
    }


def execute_validated_sql(connection: sqlite3.Connection, sql: str) -> dict[str, Any]:
    validation = validate_sql(sql, connection=connection)
    if not validation["ok"]:
        return {"ok": False, "validation": validation, "columns": [], "rows": [], "row_count": 0}
    cursor = connection.execute(validation["sql"])
    rows = [dict(row) for row in cursor.fetchall()]
    columns = [item[0] for item in cursor.description or []]
    return {
        "ok": True,
        "validation": validation,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }


def summarize_result(question: str, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "The generated SQL did not pass validation and was not executed."
    row_count = int(result.get("row_count") or 0)
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if row_count == 0:
        return "The validated SQL returned no rows."
    samples = []
    for row in rows[:5]:
        samples.append(", ".join(f"{column}={row.get(column)}" for column in columns[:4]))
    parts = [
        f"The validated SQL returned {row_count} row(s).",
        "Columns: " + ", ".join(columns) + ".",
    ]
    if samples:
        parts.append("Sample rows: " + " | ".join(samples) + ".")
    parts.append("This summary is limited to the SQL result and does not add unsupported petroleum interpretation.")
    return "\n".join(parts)


def answer_question(connection: sqlite3.Connection, question: str) -> dict[str, Any]:
    """Generate SQL, validate it, execute it, and summarize the returned rows."""
    schema_context = build_schema_context(connection)
    candidate = generate_sql(question, schema_context)
    result = execute_validated_sql(connection, candidate["sql"])
    return {
        "question": question,
        "schema_context": schema_context,
        "generated_sql": candidate["sql"],
        "reasoning": candidate["reasoning"],
        "warnings": candidate["warnings"],
        "result": result,
        "answer": summarize_result(question, result),
    }

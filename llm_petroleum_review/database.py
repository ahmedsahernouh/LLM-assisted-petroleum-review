"""Build a small SQLite petroleum-review database from public demo context."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTEXT_DIR = ROOT / "data" / "y1_ai_context"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def connect_memory() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def build_demo_database(context_dir: Path | str = DEFAULT_CONTEXT_DIR) -> sqlite3.Connection:
    """Create an in-memory SQLite database from the included Y1 demo context."""
    context_path = Path(context_dir)
    wells = _load_json(context_path / "wells_summary.json")
    layers = _load_json(context_path / "layers_summary.json")
    production = _load_json(context_path / "production_summary.json")
    screens = _load_json(context_path / "screen_contexts.json")
    timeline = _load_json(context_path / "timeline_summary.json")

    connection = connect_memory()
    _create_schema(connection)
    _load_wells(connection, wells)
    _load_layers(connection, layers)
    _load_production_entities(connection, production)
    _load_screens(connection, screens)
    _load_timeline(connection, timeline)
    _seed_metadata(connection)
    connection.commit()
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE wells (
            well_name TEXT PRIMARY KEY,
            normalized_name TEXT,
            x_utm REAL,
            y_utm REAL,
            crs TEXT,
            status TEXT,
            reservoir TEXT,
            well_type TEXT,
            spud_date TEXT,
            last_production TEXT,
            short_description TEXT
        );

        CREATE TABLE layers (
            layer_id TEXT PRIMARY KEY,
            display_name TEXT,
            layer_type TEXT,
            category TEXT,
            description TEXT,
            data_quality TEXT
        );

        CREATE TABLE production_entities (
            well_name TEXT PRIMARY KEY,
            has_production_summary INTEGER,
            date_start TEXT,
            date_end TEXT,
            metrics TEXT
        );

        CREATE TABLE timeline_events (
            event_name TEXT,
            event_date TEXT,
            category TEXT,
            description TEXT
        );

        CREATE TABLE screens (
            screen_id TEXT PRIMARY KEY,
            display_name TEXT,
            description TEXT
        );

        CREATE TABLE audit_log (
            event_time TEXT,
            event_text TEXT
        );

        CREATE TABLE table_metadata (
            table_name TEXT PRIMARY KEY,
            display_name TEXT,
            description TEXT,
            grain TEXT,
            is_ai_visible INTEGER
        );

        CREATE TABLE column_metadata (
            table_name TEXT,
            column_name TEXT,
            description TEXT,
            unit TEXT,
            is_ai_visible INTEGER,
            PRIMARY KEY (table_name, column_name)
        );

        CREATE VIEW v_well_locations AS
        SELECT well_name, x_utm, y_utm, crs, status, reservoir, well_type, spud_date, last_production
        FROM wells;

        CREATE VIEW v_layer_catalog AS
        SELECT layer_id, display_name, layer_type, category, description
        FROM layers;

        CREATE VIEW v_production_entities AS
        SELECT well_name, has_production_summary, date_start, date_end, metrics
        FROM production_entities;

        CREATE VIEW v_timeline_events AS
        SELECT event_name, event_date, category, description
        FROM timeline_events;

        CREATE VIEW v_schema_tables AS
        SELECT table_name, display_name, description
        FROM table_metadata
        WHERE is_ai_visible = 1;
        """
    )


def _field_value(well: dict[str, Any], label: str) -> str:
    hover = well.get("map_hover_info") if isinstance(well.get("map_hover_info"), dict) else {}
    fields = hover.get("fields") if isinstance(hover.get("fields"), list) else []
    for field in fields:
        if isinstance(field, dict) and str(field.get("label", "")).lower() == label.lower():
            return str(field.get("value") or "")
    return ""


def _load_wells(connection: sqlite3.Connection, wells_payload: dict[str, Any]) -> None:
    for well in wells_payload.get("wells", []):
        if not isinstance(well, dict):
            continue
        coordinates = well.get("coordinates") if isinstance(well.get("coordinates"), dict) else {}
        connection.execute(
            """
            INSERT INTO wells
            (well_name, normalized_name, x_utm, y_utm, crs, status, reservoir, well_type, spud_date, last_production, short_description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                well.get("well_name"),
                well.get("normalized_name"),
                coordinates.get("x"),
                coordinates.get("y"),
                coordinates.get("crs"),
                _field_value(well, "STATUS"),
                _field_value(well, "RESERVOIR"),
                _field_value(well, "TYPE"),
                _field_value(well, "Spud_date"),
                _field_value(well, "Last production"),
                well.get("short_description"),
            ),
        )


def _load_layers(connection: sqlite3.Connection, layers_payload: dict[str, Any]) -> None:
    for layer in layers_payload.get("layers", []):
        if isinstance(layer, dict):
            connection.execute(
                "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?)",
                (
                    layer.get("layer_id"),
                    layer.get("display_name"),
                    layer.get("layer_type"),
                    layer.get("category"),
                    layer.get("description"),
                    layer.get("data_quality"),
                ),
            )


def _load_production_entities(connection: sqlite3.Connection, production_payload: dict[str, Any]) -> None:
    metrics = ", ".join(production_payload.get("metrics_available", []))
    date_range = production_payload.get("date_range") if isinstance(production_payload.get("date_range"), dict) else {}
    for well_name in production_payload.get("entities", []):
        connection.execute(
            "INSERT INTO production_entities VALUES (?, ?, ?, ?, ?)",
            (well_name, 1, date_range.get("start"), date_range.get("end"), metrics),
        )


def _load_screens(connection: sqlite3.Connection, screens_payload: dict[str, Any]) -> None:
    for screen in screens_payload.get("screens", []):
        if isinstance(screen, dict):
            connection.execute(
                "INSERT INTO screens VALUES (?, ?, ?)",
                (screen.get("screen_id"), screen.get("display_name"), screen.get("description")),
            )


def _load_timeline(connection: sqlite3.Connection, timeline_payload: dict[str, Any]) -> None:
    events = timeline_payload.get("events")
    if not isinstance(events, list):
        return
    for event in events:
        if isinstance(event, dict):
            connection.execute(
                "INSERT INTO timeline_events VALUES (?, ?, ?, ?)",
                (
                    event.get("event_name") or event.get("name"),
                    event.get("event_date") or event.get("date"),
                    event.get("category"),
                    event.get("description"),
                ),
            )


def _seed_metadata(connection: sqlite3.Connection) -> None:
    tables = [
        ("v_well_locations", "Well locations", "Safe view of well names, coordinates, status, reservoir, and dates.", "one row per well", 1),
        ("v_layer_catalog", "Layer catalog", "Safe view of map and grid layers available in the petroleum review app.", "one row per layer", 1),
        ("v_production_entities", "Production entities", "Safe view of wells with compact production context availability.", "one row per producing well name", 1),
        ("v_timeline_events", "Timeline events", "Safe view of compact field and well timeline events.", "one row per event", 1),
        ("v_schema_tables", "AI-visible schema tables", "Safe view listing tables and views available for generated SQL.", "one row per AI-visible table or view", 1),
        ("wells", "Raw wells", "Source well table used to build the safe well view.", "one row per well", 0),
        ("audit_log", "Audit log", "Operational trace table hidden from AI-generated SQL.", "one row per audit event", 0),
        ("table_metadata", "Table metadata", "Internal semantic metadata table.", "one row per table", 0),
        ("column_metadata", "Column metadata", "Internal semantic column metadata table.", "one row per table column", 0),
    ]
    connection.executemany("INSERT INTO table_metadata VALUES (?, ?, ?, ?, ?)", tables)

    columns = [
        ("v_well_locations", "well_name", "Well identifier.", None, 1),
        ("v_well_locations", "x_utm", "Easting coordinate.", "m", 1),
        ("v_well_locations", "y_utm", "Northing coordinate.", "m", 1),
        ("v_well_locations", "crs", "Coordinate reference system label.", None, 1),
        ("v_well_locations", "status", "Well status from configured map hover fields.", None, 1),
        ("v_well_locations", "reservoir", "Reservoir name from configured map hover fields.", None, 1),
        ("v_well_locations", "well_type", "Well type from configured map hover fields.", None, 1),
        ("v_well_locations", "spud_date", "Spud date from configured map hover fields.", None, 1),
        ("v_well_locations", "last_production", "Last production date from configured map hover fields.", None, 1),
        ("v_layer_catalog", "layer_id", "Stable layer identifier.", None, 1),
        ("v_layer_catalog", "display_name", "Layer display name.", None, 1),
        ("v_layer_catalog", "layer_type", "Layer type such as well, basemap, or grid.", None, 1),
        ("v_layer_catalog", "category", "Layer category used by the review app.", None, 1),
        ("v_layer_catalog", "description", "Human-readable layer description.", None, 1),
        ("v_production_entities", "well_name", "Well with compact production context.", None, 1),
        ("v_production_entities", "has_production_summary", "Whether compact production context is available.", None, 1),
        ("v_production_entities", "date_start", "Start of available production context date range.", None, 1),
        ("v_production_entities", "date_end", "End of available production context date range.", None, 1),
        ("v_production_entities", "metrics", "Comma-separated production metrics available in the compact context.", None, 1),
        ("v_timeline_events", "event_name", "Timeline event name.", None, 1),
        ("v_timeline_events", "event_date", "Timeline event date.", None, 1),
        ("v_timeline_events", "category", "Timeline event category.", None, 1),
        ("v_timeline_events", "description", "Timeline event description.", None, 1),
        ("v_schema_tables", "table_name", "AI-visible table or view name.", None, 1),
        ("v_schema_tables", "display_name", "Human-readable table display name.", None, 1),
        ("v_schema_tables", "description", "Short table description.", None, 1),
    ]
    connection.executemany("INSERT INTO column_metadata VALUES (?, ?, ?, ?, ?)", columns)


def _actual_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    escaped = str(table_name).replace('"', '""')
    try:
        rows = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    except sqlite3.Error:
        return []
    return [str(row["name"]) for row in rows]


def build_schema_context(connection: sqlite3.Connection) -> dict[str, Any]:
    """Return compact schema context for prompt construction."""
    table_rows = connection.execute(
        "SELECT * FROM table_metadata WHERE is_ai_visible = 1 ORDER BY table_name"
    ).fetchall()
    tables = []
    for table in table_rows:
        columns = connection.execute(
            """
            SELECT column_name, description, unit
            FROM column_metadata
            WHERE table_name = ? AND is_ai_visible = 1
            ORDER BY column_name
            """,
            (table["table_name"],),
        ).fetchall()
        metadata_by_column = {str(row["column_name"]): row for row in columns}
        actual_columns = _actual_columns(connection, str(table["table_name"]))
        column_names = actual_columns or [str(row["column_name"]) for row in columns]
        tables.append(
            {
                "table_name": table["table_name"],
                "display_name": table["display_name"],
                "description": table["description"],
                "grain": table["grain"],
                "columns": [
                    {
                        "column_name": column_name,
                        "description": (
                            metadata_by_column[column_name]["description"]
                            if column_name in metadata_by_column
                            else f"SQLite column {column_name}."
                        ),
                        "unit": metadata_by_column[column_name]["unit"] if column_name in metadata_by_column else None,
                    }
                    for column_name in column_names
                ],
            }
        )
    return {
        "database_purpose": "Petroleum review demo database for safe text-to-SQL over wells, layers, production context, and timeline events.",
        "tables": tables,
        "safe_query_rules": [
            "Only SELECT and WITH statements are allowed.",
            "Multiple statements, SQL comments with hidden statements, and write/admin keywords are rejected.",
            "Queries receive a default LIMIT when missing.",
            "Tables marked is_ai_visible=0 are hidden from generated SQL.",
        ],
    }

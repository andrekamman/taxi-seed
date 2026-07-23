"""DuckDB SQL builder and atomic parquet writer."""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

from taxi_normalize.planner import ColumnAction, Plan


def _quote(name: str) -> str:
    """Double-quote an identifier, escaping embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


def _sql_lit(value) -> str:
    """Render a Python scalar as a SQL literal (None->NULL, numbers bare, strings quoted)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _action_sql(action: ColumnAction) -> str:
    """Produce a single SELECT-list expression for one column action."""
    if action.action == "passthrough":
        return _quote(action.source_column)
    if action.action == "rename":
        src = _quote(action.source_column)
        tgt = _quote(action.target_column)
        if action.cast_to:
            return f"CAST({src} AS {action.cast_to}) AS {tgt}"
        return f"{src} AS {tgt}"
    if action.action == "cast":
        src = _quote(action.source_column)
        tgt = _quote(action.target_column)
        if action.source_column == action.target_column:
            return f"CAST({src} AS {action.cast_to}) AS {tgt}"
        return f"CAST({src} AS {action.cast_to}) AS {tgt}"
    if action.action == "null_fill":
        tgt = _quote(action.target_column)
        return f"NULL::{action.target_type} AS {tgt}"
    if action.action == "value_map":
        src = _quote(action.source_column)
        tgt = _quote(action.target_column)
        whens = " ".join(
            f"WHEN CAST({src} AS VARCHAR) = {_sql_lit(str(k))} THEN {_sql_lit(v)}"
            for k, v in action.value_map.items()
        )
        # NULLs pass through; a mapped value converts to its code; an UNMAPPED
        # non-null value raises by default (rather than silently becoming NULL) so
        # the loss surfaces and the human extends the value_map — honoring the
        # project's "data loss is an error" rule instead of quietly dropping data.
        # A mapping may opt into on_unmapped: null to discard unmapped values instead.
        if action.value_map_unmapped == "null":
            els = "NULL"
        else:
            els = (
                "error('value_map: unmapped value in " + action.source_column.replace("'", "''")
                + ": ' || CAST(" + src + " AS VARCHAR))"
            )
        return (
            f"CAST(CASE WHEN {src} IS NULL THEN NULL {whens} ELSE {els} END "
            f"AS {action.target_type}) AS {tgt}"
        )
    raise ValueError(f"Unknown action type: {action.action!r}")


def _tmp_path_for(final_path: Path) -> Path:
    return final_path.with_suffix(".tmp.parquet")


def build_transform_sql(plan: Plan, input_path: Path, output_path: Path) -> str:
    """Return the full COPY (...) TO ... SQL statement for one file's transform."""
    select_list = ",\n    ".join(_action_sql(a) for a in plan.actions)
    tmp_path = _tmp_path_for(output_path)
    return (
        "COPY (\n"
        f"  SELECT\n    {select_list}\n"
        f"  FROM read_parquet('{input_path}')\n"
        f") TO '{tmp_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def execute_transform(
    conn: duckdb.DuckDBPyConnection,
    plan: Plan,
    input_path: Path,
    output_path: Path,
) -> None:
    """Run the plan and atomically place the result at output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_path_for(output_path)
    sql = build_transform_sql(plan, input_path, output_path)
    conn.execute(sql)
    os.replace(tmp_path, output_path)  # atomic on POSIX

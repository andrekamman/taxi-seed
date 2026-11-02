"""Step 7: say what exists now, so the run's value is visible without digging."""
from __future__ import annotations

from pathlib import Path

from taxi_mssql import mssql, sql
from taxi_mssql.config import Settings


def loaded_rows(settings: Settings, *, runner=None) -> dict[str, int]:
    """{data_type: rows} from taxi-seed's _load_manifest.

    Empty when the manifest does not exist yet -- nothing has been loaded, which
    is a normal first-run state, not an error.
    """
    try:
        out = mssql.run(settings, sql.rows_per_type(settings.schema),
                        database=settings.database, runner=runner)
    except mssql.MssqlError:
        return {}
    rows: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            rows[parts[0]] = int(parts[1])
    return rows


def table_rows(settings: Settings, data_type: str, *, runner=None) -> int | None:
    """Live committed row count for one trip type, or None if it can't be read.

    None rather than an exception: this is called from the heartbeat thread, and
    a progress line must never be able to kill a load that is going fine.
    """
    try:
        raw = mssql.scalar(settings, sql.loaded_table_rows(settings.schema, data_type),
                           database=settings.database, runner=runner)
    except mssql.MssqlError:
        return None
    if raw is None or not raw.isdigit():
        return None
    return int(raw)


def table_rows_total(settings: Settings, data_types, *, runner=None) -> int | None:
    """Committed rows across every selected trip type, or None.

    None if any part is unknown: a partial sum would be a wrong number, not a
    smaller one, and the heartbeat would render it as real progress.
    """
    total = 0
    seen = False
    for data_type in data_types:
        rows = table_rows(settings, data_type, runner=runner)
        if rows is None:
            return None
        total += rows
        seen = True
    return total if seen else None


def _count(root: Path, data_type: str) -> int:
    d = root / data_type
    return len(list(d.rglob("*.parquet"))) if d.is_dir() else 0


def _human(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def render(settings: Settings, *, backup_path: Path | None = None,
           rows_by_type: dict[str, int] | None = None) -> str:
    lines = ["", f"=== {settings.data_dir} ==="]
    for data_type in settings.types:
        raw = _count(settings.data_dir / "raw", data_type)
        norm = _count(settings.raw_normalized_dir, data_type)
        line = f"  {data_type:6} {raw:5} raw, {norm:5} normalized month file(s)"
        if rows_by_type and data_type in rows_by_type:
            line += f", {rows_by_type[data_type]:,} row(s) loaded"
        lines.append(line)

    lines.append(f"  database  {settings.database} "
                 f"(mdf {settings.mdf_path}, ldf {settings.ldf_path})")

    if backup_path is not None and backup_path.exists():
        lines.append(f"  backup    {backup_path.name} "
                     f"({_human(backup_path.stat().st_size)})")
        lines.append(f"            {backup_path.parent}")
    else:
        lines.append("  backup    none")
    return "\n".join(lines)

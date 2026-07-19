"""Map DuckDB/parquet column types to SQL Server types."""

import re


class TypeMappingError(Exception):
    """Raised when a DuckDB type has no SQL Server equivalent."""
    pass


# Direct mappings (case-insensitive lookup)
_TYPE_MAP = {
    "BIGINT": "BIGINT",
    "INTEGER": "INT",
    "INT": "INT",
    "INT32": "INT",
    "DOUBLE": "FLOAT",
    "FLOAT": "REAL",
    "REAL": "REAL",
    "VARCHAR": "NVARCHAR(MAX)",
    "TEXT": "NVARCHAR(MAX)",
    "STRING": "NVARCHAR(MAX)",
    "TIMESTAMP": "DATETIME2",
    "TIMESTAMP_NS": "DATETIME2",
    "TIMESTAMP_S": "DATETIME2",
    "TIMESTAMP_MS": "DATETIME2",
    "TIMESTAMP WITH TIME ZONE": "DATETIMEOFFSET",
    "TIMESTAMP_TZ": "DATETIMEOFFSET",
    "BOOLEAN": "BIT",
    "BOOL": "BIT",
    "DATE": "DATE",
    "SMALLINT": "SMALLINT",
    "INT16": "SMALLINT",
    "TINYINT": "TINYINT",
    "INT8": "TINYINT",
    "HUGEINT": "DECIMAL(38,0)",
}


def map_duckdb_to_mssql(duckdb_type: str) -> str:
    """Map a DuckDB column type to the equivalent SQL Server type.

    Raises TypeMappingError for unmapped types.
    """
    normalized = duckdb_type.strip().upper()

    # Check direct mapping
    if normalized in _TYPE_MAP:
        return _TYPE_MAP[normalized]

    # Handle DECIMAL(p,s) pattern
    match = re.match(r"DECIMAL\((\d+),\s*(\d+)\)", normalized)
    if match:
        return f"DECIMAL({match.group(1)},{match.group(2)})"

    raise TypeMappingError(
        f"No SQL Server mapping for DuckDB type: {duckdb_type!r}. "
        f"Add it to the type mapping or adjust the column config."
    )

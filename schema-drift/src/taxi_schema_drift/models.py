from dataclasses import dataclass
from pathlib import Path


@dataclass
class ColumnInfo:
    """Information about a column in a schema."""

    name: str
    dtype: str

    def __hash__(self):
        return hash((self.name, self.dtype))

    def __eq__(self, other):
        return self.name == other.name and self.dtype == other.dtype


@dataclass
class ColumnRename:
    """Represents a detected column rename."""

    old_col: ColumnInfo
    new_col: ColumnInfo
    confidence: float  # 0.0 to 1.0
    data_verified: bool | None = None  # None = not checked, True = verified, False = rejected
    verification_details: str = ""


@dataclass
class SchemaChange:
    """Represents a schema change between two time periods."""

    period_from: str
    period_to: str
    columns_added: list[ColumnInfo]
    columns_removed: list[ColumnInfo]
    columns_type_changed: list[tuple[ColumnInfo, ColumnInfo]]
    columns_renamed: list[ColumnRename]
    file_from: Path | None = None
    file_to: Path | None = None

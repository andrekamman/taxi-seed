import pytest

from taxi_shared.type_mapping import map_duckdb_to_mssql, TypeMappingError


def test_basic_types():
    assert map_duckdb_to_mssql("BIGINT") == "BIGINT"
    assert map_duckdb_to_mssql("INTEGER") == "INT"
    assert map_duckdb_to_mssql("DOUBLE") == "FLOAT"
    assert map_duckdb_to_mssql("FLOAT") == "REAL"
    assert map_duckdb_to_mssql("VARCHAR") == "NVARCHAR(MAX)"
    assert map_duckdb_to_mssql("TIMESTAMP") == "DATETIME2"
    assert map_duckdb_to_mssql("BOOLEAN") == "BIT"
    assert map_duckdb_to_mssql("DATE") == "DATE"
    assert map_duckdb_to_mssql("SMALLINT") == "SMALLINT"
    assert map_duckdb_to_mssql("TINYINT") == "TINYINT"
    assert map_duckdb_to_mssql("HUGEINT") == "DECIMAL(38,0)"


def test_decimal_with_precision():
    assert map_duckdb_to_mssql("DECIMAL(10,2)") == "DECIMAL(10,2)"
    assert map_duckdb_to_mssql("DECIMAL(18,4)") == "DECIMAL(18,4)"


def test_timestamp_with_timezone():
    assert map_duckdb_to_mssql("TIMESTAMP WITH TIME ZONE") == "DATETIMEOFFSET"
    assert map_duckdb_to_mssql("TIMESTAMP_TZ") == "DATETIMEOFFSET"


def test_int16_int8_aliases():
    assert map_duckdb_to_mssql("INT16") == "SMALLINT"
    assert map_duckdb_to_mssql("INT8") == "TINYINT"


def test_case_insensitive():
    assert map_duckdb_to_mssql("bigint") == "BIGINT"
    assert map_duckdb_to_mssql("Varchar") == "NVARCHAR(MAX)"


def test_unknown_type_raises():
    with pytest.raises(TypeMappingError, match="GEOMETRY"):
        map_duckdb_to_mssql("GEOMETRY")

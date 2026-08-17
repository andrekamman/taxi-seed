import duckdb
import pytest

from taxi_loader.cli import (
    discover_month_files, guard_month_mode, main, parse_args, resolve_input_dir,
)
from taxi_loader.connection import LoaderError
from taxi_loader.reconcile import APPEND, RELOAD, SKIP, YearPlan


def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.data_type is None
    assert ns.host == "localhost"
    assert ns.port == 1433
    assert ns.database == "taxi"
    assert ns.schema == "dbo"
    assert ns.user == "sa"
    assert ns.input_dir is None
    assert ns.data_dir is None
    assert ns.flush_rows == 100000
    assert ns.full_refresh is False
    assert ns.dry_run is False


def test_discover_month_files(normalized_family):
    conn = duckdb.connect(":memory:")
    months = discover_month_files(conn, normalized_family, "yellow")
    got = sorted((m.year, m.month, m.source_row_count) for m in months)
    assert got == [(2023, 1, 3), (2023, 2, 4), (2024, 1, 5)]


def test_missing_password_is_exit_2(monkeypatch, normalized_family):
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    rc = main(["yellow", "--input-dir", str(normalized_family)])
    assert rc == 2


def test_bad_schema_is_exit_2(monkeypatch, normalized_family):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    rc = main(["yellow", "--schema", "bad-schema", "--input-dir", str(normalized_family)])
    assert rc == 2


def test_invalid_data_type_is_rejected():
    with pytest.raises(SystemExit) as exc:
        parse_args(["yello"])
    assert exc.value.code == 2


def test_resolve_input_dir_precedence():
    # explicit --input-dir wins
    assert resolve_input_dir("/x/norm", "/base") == "/x/norm"
    # else derive from --data-dir
    assert resolve_input_dir(None, "/base") == "/base/raw-normalized"
    # else default
    assert resolve_input_dir(None, None) == "raw-normalized"


# --- --year / --month filters, for parallel month-shard loading -------------

def test_parse_args_accepts_year_and_month():
    ns = parse_args(["fhvhv", "--year", "2019", "--month", "3"])
    assert (ns.year, ns.month) == (2019, 3)


def test_parse_args_defaults_year_and_month_to_none():
    ns = parse_args([])
    assert ns.year is None and ns.month is None


def test_month_requires_year():
    """A bare --month would silently load that month of every year."""
    with pytest.raises(SystemExit):
        parse_args(["fhvhv", "--month", "3"])


def test_year_and_month_filter_discovery(normalized_family):
    conn = duckdb.connect(":memory:")
    months = discover_month_files(conn, normalized_family, "yellow",
                                  year=2023, month=2)
    assert [(m.year, m.month) for m in months] == [(2023, 2)]


def test_year_filter_alone_keeps_every_month_of_that_year(normalized_family):
    conn = duckdb.connect(":memory:")
    months = discover_month_files(conn, normalized_family, "yellow", year=2023)
    assert sorted(m.month for m in months) == [1, 2]


def test_no_filter_keeps_everything(normalized_family):
    conn = duckdb.connect(":memory:")
    assert len(discover_month_files(conn, normalized_family, "yellow")) == 3


def test_month_mode_refuses_a_year_needing_reload():
    """RELOAD drops the year table -- fatal while sibling months load into it."""
    plans = [YearPlan(2019, RELOAD, [])]
    with pytest.raises(LoaderError, match="integrity"):
        guard_month_mode(plans, month=3)


def test_month_mode_allows_append_and_skip():
    plans = [YearPlan(2019, APPEND, []), YearPlan(2020, SKIP, [])]
    assert guard_month_mode(plans, month=3) is None


def test_without_month_mode_a_reload_plan_is_left_alone():
    assert guard_month_mode([YearPlan(2019, RELOAD, [])], month=None) is None

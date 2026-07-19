"""Smoke tests that verify schema_drift package structure and CLI wiring.

These tests do not exercise the analyzer against real parquet data — they
verify that the split package still exposes the expected public surface and
that the CLI entry point is invocable.
"""
import subprocess
import sys


def test_public_imports_available():
    """Every module in the split package must import cleanly."""
    from schema_drift import models, similarity, stats, renames, analyze, report, cli
    # Concrete names that MUST remain importable from their target modules.
    assert models.ColumnInfo is not None
    assert models.ColumnRename is not None
    assert models.SchemaChange is not None
    assert callable(similarity.column_name_similarity)
    assert callable(similarity.types_compatible)
    assert callable(stats.get_column_stats)
    assert callable(renames.detect_renames)
    assert callable(renames.detect_renames_by_data)
    assert callable(analyze.analyze_data_type)
    assert callable(report.generate_report)
    assert callable(cli.main)


def test_cli_help_runs():
    """schema-drift --help must exit 0 with usage output."""
    result = subprocess.run(
        [sys.executable, "-m", "schema_drift.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "schema drift" in result.stdout.lower()


def test_cli_missing_data_dir_exits_nonzero():
    """CLI must exit nonzero when given a data dir that doesn't exist."""
    result = subprocess.run(
        [sys.executable, "-m", "schema_drift.cli",
         "--data-dir", "/tmp/definitely_does_not_exist_xyz"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr.lower()

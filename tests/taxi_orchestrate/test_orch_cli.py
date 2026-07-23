import taxi_orchestrate.cli as cli
from taxi_orchestrate.cli import DATA_TYPES, main, parse_args


def test_parse_defaults():
    ns = parse_args([])
    assert ns.data_type is None
    assert ns.skip_download is False and ns.download_only is False and ns.load is False
    assert ns.host == "localhost" and ns.port == 1433
    assert ns.database == "taxi" and ns.schema == "dbo" and ns.user == "sa"
    assert ns.flush_rows == 100000 and ns.full_refresh is False
    assert ns.sample is None and ns.recent is None and ns.dry_run is False


def test_bad_type_is_usage_error():
    import pytest
    with pytest.raises(SystemExit) as e:
        parse_args(["yello"])
    assert e.value.code == 2


def test_download_only_with_load_is_error(capsys):
    rc = main(["--download-only", "--load"])
    assert rc == 2


def test_load_without_password_is_exit_2(monkeypatch):
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    rc = main(["yellow", "--skip-download", "--load"])
    assert rc == 2


def test_dry_run_invokes_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.stages, "run", lambda *a, **k: calls.append(a) or 0)
    rc = main(["yellow", "--dry-run"])
    assert rc == 0
    assert calls == []
    assert "yellow" in capsys.readouterr().out

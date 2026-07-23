import taxi_orchestrate.cli as cli


def _fake_run(codes):
    """Return a run() stub that yields exit codes keyed by the stage token in cmd."""
    def _run(cmd, cwd, extra_env=None):
        joined = " ".join(cmd)
        if "download_taxi_data.sh" in joined:
            stage = "download"
        elif "taxi_normalize.cli" in joined:
            stage = "normalize"
        else:
            stage = "load"
        # data type is the last non-flag token for our stub purposes
        dtype = cmd[-1] if not cmd[-1].startswith("-") else "?"
        return codes.get((stage, dtype), codes.get(stage, 0))
    return _run


def test_default_runs_download_then_normalize(monkeypatch, capsys):
    seen = []
    def _run(cmd, cwd, extra_env=None):
        seen.append("download" if "download_taxi_data.sh" in " ".join(cmd)
                    else "normalize" if "taxi_normalize.cli" in " ".join(cmd) else "load")
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = main_ok = cli.main(["yellow"])
    assert rc == 0
    assert seen == ["download", "normalize"]   # no load by default


def test_needs_review_halts_before_load(monkeypatch):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    def _run(cmd, cwd, extra_env=None):
        j = " ".join(cmd)
        if "taxi_normalize.cli" in j:
            return 1          # needs review
        if "taxi_loader.cli" in j:
            raise AssertionError("load must not run after needs-review")
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = cli.main(["yellow", "--load"])
    assert rc == 1            # needs review, nothing failed


def test_load_conn_error_aborts_remaining_loads(monkeypatch):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    loads = []
    def _run(cmd, cwd, extra_env=None):
        j = " ".join(cmd)
        if "taxi_loader.cli" in j:
            loads.append(cmd[cmd.index("taxi_loader.cli") + 1] if False else cmd[3])
            return 2          # conn error on first load
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = cli.main(["--skip-download", "--load"])   # all four types
    assert rc == 2
    assert len(loads) == 1    # aborted after the first conn error


def test_skip_download_runs_normalize_only(monkeypatch):
    seen = []
    def _run(cmd, cwd, extra_env=None):
        seen.append("download" if "download_taxi_data.sh" in " ".join(cmd)
                    else "normalize" if "taxi_normalize.cli" in " ".join(cmd) else "load")
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = cli.main(["green", "--skip-download"])
    assert rc == 0 and seen == ["normalize"]

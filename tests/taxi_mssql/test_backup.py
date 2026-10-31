import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from taxi_mssql import backup, mssql
from taxi_mssql.config import from_env

UTC = timezone.utc
T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def settings_for(tmp_path):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path)})


def sqlcmd_output(value, rows=1):
    """What sqlcmd -h -1 -W really writes: the value, a blank line, and the
    row-count trailer that -h -1 does not suppress. Feeding a bare token here is
    what hid `scalar()` returning the string "NULL" straight into
    datetime.fromisoformat."""
    return f"{value}\n\n({rows} rows affected)\n"


class Recorder:
    """Answers scalar queries from a script of raw values, formatted the way
    sqlcmd formats them, and records every statement."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []

    def __call__(self, cmd, env, stdin_text):
        if "-Q" in cmd:
            self.statements.append(cmd[cmd.index("-Q") + 1])
        out = sqlcmd_output(self.answers.pop(0)) if self.answers else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def make_bak(settings, when: datetime, name="taxi-sql_taxi_FULL_20260801_120000.bak"):
    d = settings.backup_dir / "taxi-sql" / "taxi" / "FULL"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"bak")
    os.utime(p, (when.timestamp(), when.timestamp()))
    return p


# --- the staleness rule ---

def test_no_backup_is_always_stale():
    assert backup.is_stale(T0, None) is True


def test_no_backup_and_nothing_loaded_is_still_stale():
    """The invariant: a successful run always leaves a .bak, even for an empty DB."""
    assert backup.is_stale(None, None) is True


def test_backup_newer_than_the_last_load_is_current():
    assert backup.is_stale(T0, T0 + timedelta(minutes=1)) is False


def test_load_newer_than_the_backup_is_stale():
    """The died-before-backup case: a run loaded rows then crashed. The next run
    loads nothing, so 'did this run load anything?' would never back up again."""
    assert backup.is_stale(T0, T0 - timedelta(minutes=1)) is True


def test_nothing_ever_loaded_but_a_backup_exists_is_current():
    assert backup.is_stale(None, T0) is False


def test_equal_timestamps_are_not_stale():
    assert backup.is_stale(T0, T0) is False


# --- discovery ---

def test_newest_backup_picks_the_latest_of_several(tmp_path):
    s = settings_for(tmp_path)
    make_bak(s, T0 - timedelta(hours=2), "a.bak")
    newest = make_bak(s, T0, "b.bak")
    assert backup.newest_backup(s) == newest


def test_newest_backup_is_none_when_absent(tmp_path):
    assert backup.newest_backup(settings_for(tmp_path)) is None


def test_backup_mtime_is_utc_aware(tmp_path):
    s = settings_for(tmp_path)
    p = make_bak(s, T0)
    assert backup.backup_mtime(p).tzinfo is not None


# --- manifest reading ---

def test_max_loaded_at_parses_iso_as_utc(tmp_path):
    rec = Recorder(["2026-08-01T12:00:00.000"])
    got = backup.max_loaded_at(settings_for(tmp_path), runner=rec)
    assert got == T0


def test_max_loaded_at_is_none_when_the_manifest_table_is_missing(tmp_path):
    """The query itself guards on OBJECT_ID and returns SQL NULL, which
    mssql.scalar maps to None -- no error, no string-matching required.

    The runner emits what sqlcmd emits, trailer and all: reading only the first
    line of "NULL\\n\\n(1 rows affected)" yields the *string* "NULL", which is
    truthy and reaches datetime.fromisoformat as an untyped ValueError."""
    assert backup.max_loaded_at(settings_for(tmp_path), runner=Recorder(["NULL"])) is None


def test_max_loaded_at_propagates_a_genuine_sql_failure(tmp_path):
    """Regression pin: a real sqlcmd failure (connection reset, permissions,
    etc.) must raise, not be silently reinterpreted as 'nothing loaded' --
    that would make a stale backup look current and skip the write it needs."""
    def failing(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 1, "", "Login failed for user")

    with pytest.raises(mssql.MssqlError):
        backup.max_loaded_at(settings_for(tmp_path), runner=failing)


def test_max_loaded_at_is_none_when_the_manifest_is_empty(tmp_path):
    assert backup.max_loaded_at(settings_for(tmp_path), runner=Recorder(["NULL"])) is None


# --- the step ---

def test_run_backs_up_when_stale(tmp_path):
    s = settings_for(tmp_path)
    rec = Recorder(["2026-08-01T12:00:00.000"])  # loaded, no .bak on disk
    assert backup.run(s, runner=rec) is True
    assert "DatabaseBackup" in "\n".join(rec.statements)


def _failing_backup_runner(settings, bad_bak_at, name="bad.bak"):
    """Answers the manifest query, then fails the backup the way Ola does when
    RESTORE VERIFYONLY fails: the .bak has already been written, and only the
    cleanup step is skipped."""
    def runner(cmd, env, stdin_text):
        statement = cmd[cmd.index("-Q") + 1] if "-Q" in cmd else ""
        if "DatabaseBackup" in statement:
            make_bak(settings, bad_bak_at, name)
            return subprocess.CompletedProcess(
                cmd, 1, "", "Msg 3013: VERIFY DATABASE is terminating abnormally.")
        return subprocess.CompletedProcess(
            cmd, 0, sqlcmd_output("2026-08-01T12:00:00.000"), "")
    return runner


def test_a_failed_verify_leaves_no_bak_behind_to_poison_the_next_run(tmp_path):
    """Ola only skips cleanup on a verify failure -- the unverified .bak stays,
    with an mtime newer than MAX(loaded_at), so the *next* run would report
    'backup: current', exit 0, and offer a corrupt file as the deliverable."""
    s = settings_for(tmp_path)
    good = make_bak(s, T0 - timedelta(hours=2), "good.bak")
    runner = _failing_backup_runner(s, T0 + timedelta(hours=1))

    with pytest.raises(mssql.MssqlError):
        backup.run(s, runner=runner)

    assert good.exists(), "the previous verified backup must survive"
    assert [p.name for p in s.backup_dir.rglob("*.bak")] == ["good.bak"]
    # And the next run therefore still sees itself as stale.
    assert backup.is_stale(T0, backup.backup_mtime(backup.newest_backup(s))) is True


def test_a_failed_verify_with_no_previous_backup_leaves_nothing(tmp_path):
    s = settings_for(tmp_path)
    runner = _failing_backup_runner(s, T0 + timedelta(hours=1))

    with pytest.raises(mssql.MssqlError):
        backup.run(s, runner=runner)

    assert list(s.backup_dir.rglob("*.bak")) == []


def test_run_backs_up_an_empty_database_with_no_manifest(tmp_path):
    """The spec's supported first-run state: no _load_manifest, no .bak. The
    NULL scalar must not escape as an untyped ValueError from fromisoformat."""
    s = settings_for(tmp_path)
    rec = Recorder(["NULL"])

    assert backup.run(s, runner=rec) is True
    assert "DatabaseBackup" in "\n".join(rec.statements)


def test_run_skips_when_current(tmp_path):
    s = settings_for(tmp_path)
    make_bak(s, T0 + timedelta(hours=1))
    rec = Recorder(["2026-08-01T12:00:00.000"])
    assert backup.run(s, runner=rec) is False
    assert "DatabaseBackup" not in "\n".join(rec.statements)

import re
import subprocess

import pytest

from taxi_mssql import ola, sql
from taxi_mssql.config import from_env

SETTINGS = from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": "/data"})


class Recorder:
    """Captures the -i file's CONTENT at call time: ola.run deletes the temp
    file before returning, so reading the path afterwards would fail."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []
        self.file_contents = []

    def __call__(self, cmd, env, stdin_text):
        if "-Q" in cmd:
            self.statements.append(cmd[cmd.index("-Q") + 1])
        if "-i" in cmd:
            self.file_contents.append(open(cmd[cmd.index("-i") + 1]).read())
        out = self.answers.pop(0) if self.answers else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def vendored(tmp_path):
    p = tmp_path / "MaintenanceSolution.sql"
    p.write_text("DECLARE @CreateJobs nvarchar(max) = 'Y'\n"
                 "DECLARE @BackupDirectory nvarchar(max) = NULL\nGO\n")
    return p


def test_skips_when_already_installed(tmp_path):
    rec = Recorder(["1"])
    installed = ola.run(SETTINGS, script=vendored(tmp_path), runner=rec)
    assert installed is False
    assert rec.file_contents == []


def test_installs_a_patched_copy_when_absent(tmp_path):
    rec = Recorder(["0"])
    installed = ola.run(SETTINGS, script=vendored(tmp_path), runner=rec)

    assert installed is True
    assert len(rec.file_contents) == 1
    assert "DECLARE @CreateJobs nvarchar(max) = 'N'" in rec.file_contents[0]
    assert (
        "DECLARE @BackupDirectory nvarchar(max) = N'/data/mssql/backup'"
        in rec.file_contents[0]
    )


def test_the_vendored_file_is_left_untouched(tmp_path):
    script = vendored(tmp_path)
    before = script.read_text()
    ola.run(SETTINGS, script=script, runner=Recorder(["0"]))
    assert script.read_text() == before


def test_is_installed_reads_the_object_id_probe():
    rec = Recorder(["1"])
    assert ola.is_installed(SETTINGS, runner=rec) is True
    assert "dbo.DatabaseBackup" in rec.statements[0]


def test_run_raises_a_typed_error_when_the_script_is_missing(tmp_path):
    """A broken package build (e.g. the wheel's force-included sql/
    directory silently dropped) must fail with a typed, exit-2 error naming
    the missing path -- not a bare FileNotFoundError that the CLI's
    exception-to-exit-code mapping (Task 11) doesn't know about."""
    missing = tmp_path / "does-not-exist" / "MaintenanceSolution.sql"
    with pytest.raises(ola.OlaScriptMissingError, match=re.escape(str(missing))):
        ola.run(SETTINGS, script=missing, runner=Recorder(["0"]))


def test_prepare_maintenance_solution_patches_the_real_vendored_file():
    """This is the check that would have caught the SET-vs-DECLARE mismatch:
    run the actual patcher against the actual downloaded script, not a
    hand-written fixture that can silently drift from upstream's real
    syntax."""
    text = ola.VENDORED_SCRIPT.read_text()
    patched = sql.prepare_maintenance_solution(text, "/data/mssql/backup")
    assert "DECLARE @CreateJobs nvarchar(max)          = 'N'" in patched
    assert (
        "DECLARE @BackupDirectory nvarchar(max)     = N'/data/mssql/backup'"
        in patched
    )

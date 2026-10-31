import shutil
import subprocess
from collections import namedtuple

import pytest

from taxi_mssql import provision
from taxi_mssql.config import from_env

GB = 1024 ** 3
MB = 1024 ** 2

DiskUsage = namedtuple("DiskUsage", "total used free")


@pytest.fixture(autouse=True)
def ample_free_disk(monkeypatch):
    """provision.run now checks real free space before creating a database.

    Unit tests exercise sizes the sandbox's actual free disk cannot back --
    that gap is exactly the bug Finding 2 exists to catch -- so default every
    test to abundant free space. The tests that exercise the check itself
    override this explicitly.
    """
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: DiskUsage(10_000 * GB, 0, 10_000 * GB))


def settings_for(tmp_path, **env):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path), **env})


def write_parquet(tmp_path, data_type, size):
    d = tmp_path / "raw-normalized" / data_type / "2015"
    d.mkdir(parents=True, exist_ok=True)
    # Sparse file: sizing.parquet_bytes only reads stat().st_size, never the
    # contents, so a hole-punched file gives the same result as materializing
    # `size` zero bytes without the multi-GB in-memory allocation.
    path = d / "a.parquet"
    with path.open("wb") as f:
        f.truncate(size)


def sqlcmd_output(value, rows=1):
    """Real sqlcmd -h -1 -W output: the value, a blank line, and the row-count
    trailer that -h -1 does not suppress."""
    return f"{value}\n\n({rows} rows affected)\n"


class Recorder:
    """Answers scalar queries from a script of raw values, formatted the way
    sqlcmd formats them, and records every statement."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []

    def __call__(self, cmd, env, stdin_text):
        sql = cmd[cmd.index("-Q") + 1] if "-Q" in cmd else ""
        self.statements.append(sql)
        out = sqlcmd_output(self.answers.pop(0)) if self.answers else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_creates_a_presized_database_when_absent(tmp_path):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 4 * GB)
    rec = Recorder(["0"])  # database_exists -> 0

    created = provision.run(s, runner=rec)

    assert created is True
    joined = "\n".join(rec.statements)
    assert "CREATE DATABASE [taxi]" in joined
    assert "SIZE = 20480MB" in joined      # 4 GB x 3.0 = 12 -> 20 GB
    assert "SIZE = 5120MB" in joined       # log default: 25% of 20 GB = 5 GB
    assert "SET RECOVERY SIMPLE" in joined


def test_creates_a_small_presized_database_for_a_small_run(tmp_path):
    """The TAXI_RECENT=1 smoke path implies well under 10 GB of data -- the old
    flat 10 GB floor demanded a 10 GB mdf (and a flat 16 GB ldf) regardless."""
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 128 * MB)  # x 3.0 default factor ~= 384 MB
    rec = Recorder(["0"])

    provision.run(s, runner=rec)

    joined = "\n".join(rec.statements)
    assert "SIZE = 1024MB" in joined       # rounds up to 1 GB, not 10 GB
    assert "SIZE = 10240MB" not in joined
    assert "SIZE = 16384MB" not in joined  # log follows the small mdf down too


def test_creation_warns_that_the_allocation_may_run_silently_for_a_long_time(
    tmp_path, capsys
):
    """Instant file initialization on SQL Server on Linux is unconfirmed (spec:
    open risk, deferred to the first integration run). If the files are
    zero-filled, CREATE DATABASE produces no output for a long time -- the user
    needs a documented reason to believe it is not hung."""
    s = settings_for(tmp_path, MSSQL_DATA_SIZE_GB="200")
    rec = Recorder(["0"])

    provision.run(s, runner=rec)

    out = capsys.readouterr().out
    assert "216 GB" in out            # 200 GB data + 16 GB log
    assert "not hung" in out
    assert "instant file initialization" in out


def test_explicit_size_wins_over_the_estimate(tmp_path):
    s = settings_for(tmp_path, MSSQL_DATA_SIZE_GB="500")
    write_parquet(tmp_path, "yellow", 4 * GB)
    rec = Recorder(["0"])

    provision.run(s, runner=rec)

    assert "SIZE = 512000MB" in "\n".join(rec.statements)


def test_sizing_counts_only_selected_types(tmp_path):
    s = settings_for(tmp_path, TAXI_TYPES="yellow")
    write_parquet(tmp_path, "yellow", 4 * GB)
    write_parquet(tmp_path, "green", 40 * GB)
    rec = Recorder(["0"])

    provision.run(s, runner=rec)

    assert "SIZE = 20480MB" in "\n".join(rec.statements)


def make_existing_files(settings):
    """The mdf/ldf a previous run left on the bind mount."""
    settings.mssql_data_dir.mkdir(parents=True, exist_ok=True)
    settings.mssql_log_dir.mkdir(parents=True, exist_ok=True)
    settings.mdf_path.write_bytes(b"mdf")
    settings.ldf_path.write_bytes(b"ldf")


def test_attaches_when_the_files_survive_but_the_server_forgot_the_database(tmp_path):
    """`docker compose down` destroys master while the mdf/ldf survive on the
    bind mount. Creating over them fails with Msg 5170 on every run forever."""
    s = settings_for(tmp_path)
    make_existing_files(s)
    write_parquet(tmp_path, "yellow", 4 * GB)
    rec = Recorder(["0"])  # database_exists -> 0

    assert provision.run(s, runner=rec) is True

    joined = "\n".join(rec.statements)
    assert "FOR ATTACH" in joined
    assert "SIZE =" not in joined            # never re-size an existing file
    assert "SET RECOVERY SIMPLE" in joined


def test_creates_rather_than_attaches_when_no_mdf_is_on_disk(tmp_path):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 4 * GB)
    rec = Recorder(["0"])

    provision.run(s, runner=rec)

    joined = "\n".join(rec.statements)
    assert "FOR ATTACH" not in joined
    assert "SIZE = 20480MB" in joined


def test_skips_creation_when_the_database_exists(tmp_path):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 1 * GB)
    rec = Recorder(["1", "10240"])  # exists -> 1, allocated 10 GB

    created = provision.run(s, runner=rec)

    assert created is False
    assert "CREATE DATABASE" not in "\n".join(rec.statements)


def test_warns_when_the_existing_file_is_too_small(tmp_path, capsys):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 40 * GB)  # implies 120 GB
    rec = Recorder(["1", "10240"])              # allocated 10 GB

    provision.run(s, runner=rec)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "autogrow" in out.lower()
    assert "MSSQL_DATA_SIZE_GB" in out


def test_the_autogrowth_remedy_drops_the_database_before_deleting_the_files(
    tmp_path, capsys
):
    """Deleting the mdf/ldf while the server still has the database registered
    leaves it RECOVERY_PENDING: the next run takes the skip branch and then dies
    querying sys.database_files against a database it cannot open. The message
    has to name the step that actually works."""
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 40 * GB)
    rec = Recorder(["1", "10240"])

    provision.run(s, runner=rec)

    assert "DROP DATABASE [taxi]" in capsys.readouterr().out


def test_stays_quiet_when_the_existing_file_is_ample(tmp_path, capsys):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 1 * GB)   # 1 GB x 3.0 = 3 GB implied
    rec = Recorder(["1", "102400"])             # allocated 100 GB

    provision.run(s, runner=rec)

    assert "WARNING" not in capsys.readouterr().out


def test_stays_quiet_when_the_allocation_cannot_be_read(tmp_path, capsys):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 40 * GB)
    rec = Recorder(["1", "NULL"])

    provision.run(s, runner=rec)

    assert "WARNING" not in capsys.readouterr().out


def test_raises_when_the_disk_cannot_fit_the_planned_allocation(tmp_path, monkeypatch):
    """The real failure this guards against: a 128 MB smoke run on a host with
    18 GB free, where the old flat 10 GB + 16 GB sizing demanded 26 GB and SQL
    Server died mid-CREATE DATABASE with a bare OS error. Catch it before
    sqlcmd is even invoked, with a message naming both levers."""
    s = settings_for(tmp_path, MSSQL_DATA_SIZE_GB="100", MSSQL_LOG_SIZE_GB="20")
    rec = Recorder(["0"])  # database_exists -> 0
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: DiskUsage(50 * GB, 40 * GB, 10 * GB))

    with pytest.raises(provision.InsufficientDiskSpaceError) as exc_info:
        provision.run(s, runner=rec)

    message = str(exc_info.value)
    assert "MSSQL_DATA_SIZE_GB" in message
    assert "MSSQL_LOG_SIZE_GB" in message
    assert "121" in message   # required: 100 GB data + 20 GB log + 1 GB headroom
    assert "10.0" in message  # free
    assert "CREATE DATABASE" not in "\n".join(rec.statements)


def test_does_not_raise_when_the_disk_has_just_enough_room(tmp_path, monkeypatch):
    s = settings_for(tmp_path, MSSQL_DATA_SIZE_GB="10", MSSQL_LOG_SIZE_GB="1")
    rec = Recorder(["0"])
    # 10 GB + 1 GB + 1 GB headroom (sizing.DISK_HEADROOM_MB) = 12 GB required.
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: DiskUsage(20 * GB, 8 * GB, 12 * GB))

    created = provision.run(s, runner=rec)

    assert created is True
    assert "CREATE DATABASE [taxi]" in "\n".join(rec.statements)


def test_disk_space_is_not_checked_on_the_attach_path(tmp_path, monkeypatch):
    """Attaching an existing mdf/ldf allocates nothing new -- there is nothing
    for the free-space check to protect against, so it must not run there."""
    s = settings_for(tmp_path)
    make_existing_files(s)
    write_parquet(tmp_path, "yellow", 4 * GB)
    rec = Recorder(["0"])  # database_exists -> 0
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: DiskUsage(1 * GB, 1 * GB, 0))  # no free space at all

    assert provision.run(s, runner=rec) is True
    assert "FOR ATTACH" in "\n".join(rec.statements)


def test_apply_memory_cap_uses_the_override(tmp_path):
    sent = []

    def runner(cmd, env, stdin_text):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    s = settings_for(tmp_path, MSSQL_MAX_MEMORY_MB="20000")
    assert provision.apply_memory_cap(s, runner=runner,
                                      meminfo=lambda: 32076) == 20000
    assert any("20000" in " ".join(c) for c in sent)


def test_apply_memory_cap_derives_from_host_ram_when_unset(tmp_path):
    s = settings_for(tmp_path)
    assert provision.apply_memory_cap(
        s, runner=lambda *a: subprocess.CompletedProcess([], 0, "", ""),
        meminfo=lambda: 32076) == 24057


def test_apply_memory_cap_skips_when_host_ram_is_unreadable(tmp_path):
    """Leave SQL Server's own default alone rather than guess."""
    s = settings_for(tmp_path)
    assert provision.apply_memory_cap(
        s, runner=lambda *a: subprocess.CompletedProcess([], 0, "", ""),
        meminfo=lambda: None) is None

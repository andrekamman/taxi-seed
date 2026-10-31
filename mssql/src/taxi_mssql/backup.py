"""Step 6: take a verified compressed FULL backup, but only when stale."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from taxi_mssql import mssql, sql
from taxi_mssql.config import Settings


def newest_backup(settings: Settings) -> Path | None:
    """The most recent .bak anywhere under the Ola directory root.

    Searched recursively rather than at a fixed path because Ola's layout is
    <Directory>/<@@SERVERNAME>/<database>/<type>/, and the server name is the
    container hostname.
    """
    if not settings.backup_dir.is_dir():
        return None
    baks = list(settings.backup_dir.rglob("*.bak"))
    if not baks:
        return None
    return max(baks, key=lambda p: p.stat().st_mtime)


def backup_mtime(path: Path) -> datetime | None:
    if path is None:
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def max_loaded_at(settings: Settings, *, runner=None) -> datetime | None:
    """Newest loaded_at in taxi-seed's _load_manifest, as an aware UTC datetime.

    Returns None when the table does not exist yet (nothing has ever been
    loaded) or when it is empty. The missing-table case is expressed in the
    query itself (an OBJECT_ID guard that returns SQL NULL), not by catching
    mssql.MssqlError -- that exception covers any non-zero sqlcmd exit, so
    catching it here would also swallow a genuine failure (a dropped
    connection, a permissions error) and misreport it as "nothing loaded",
    which would make a stale backup look current. A real failure propagates.
    """
    raw = mssql.scalar(settings, sql.max_loaded_at(settings.schema),
                       database=settings.database, runner=runner)
    if not raw:
        return None
    # loaded_at is written with SYSUTCDATETIME(); style 126 gives naive ISO-8601.
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def is_stale(loaded_at: datetime | None,
             newest_backup_mtime: datetime | None) -> bool:
    """Should this run take a backup?

    No .bak at all -> always, so a successful run never leaves the deliverable
    set incomplete. Otherwise back up only when a load is newer than the backup;
    that is what catches a previous run that loaded rows and then died before
    backing up.
    """
    if newest_backup_mtime is None:
        return True
    if loaded_at is None:
        return False
    return loaded_at > newest_backup_mtime


def _discard_backups_newer_than(settings: Settings,
                                baseline: datetime | None) -> list[Path]:
    """Delete every .bak that appeared during a failed backup attempt.

    Ola's DatabaseBackup, when RESTORE VERIFYONLY fails, only skips *cleanup* --
    it leaves the unverified .bak on disk with a fresh mtime. Staleness is an
    mtime comparison with no notion of "did this file verify", so left in place
    that file makes the next run print "backup: current", exit 0, and present a
    corrupt backup as the deliverable. Removing it restores the invariant that
    the newest .bak on disk is one that verified.
    """
    if not settings.backup_dir.is_dir():
        return []
    removed: list[Path] = []
    for path in sorted(settings.backup_dir.rglob("*.bak")):
        if baseline is not None and backup_mtime(path) <= baseline:
            continue
        try:
            path.unlink()
        except OSError as e:
            # Never let cleanup replace the MssqlError that is being re-raised:
            # that error is the real diagnosis, and an OSError escaping here is
            # untyped and would bypass cli.main's exit-code mapping.
            print(f"backup: WARNING could not remove unverified {path}: {e}")
            continue
        removed.append(path)
    return removed


def run(settings: Settings, *, runner=None) -> bool:
    """Back up when stale. Returns True when a backup was taken."""
    loaded_at = max_loaded_at(settings, runner=runner)
    existing = newest_backup(settings)
    existing_mtime = backup_mtime(existing)

    if not is_stale(loaded_at, existing_mtime):
        print(f"backup: current ({existing.name}), skipping")
        return False

    print(f"backup: taking a verified compressed FULL backup to {settings.backup_dir}")
    try:
        mssql.run(settings,
                  sql.database_backup(settings.database, str(settings.backup_dir)),
                  database="master", runner=runner)
    except mssql.MssqlError:
        for path in _discard_backups_newer_than(settings, existing_mtime):
            print(f"backup: removed unverified {path.name}")
        raise
    return True

# Taxi seeder container

**Date:** 2026-08-01
**Status:** Approved — partly superseded, see the note below

!!! note "Written in taxi-lab; adopted here 2026-11-01"
    This spec designed the seeder while it lived in `taxi-lab`, and moved here with the
    code (issues #24 and #35). It is kept as the record of *why* the container is shaped
    this way; the reasoning about the ephemeral `master`, the bind mount, the
    `SQLCMDPASSWORD` hazard and the exit-code contract all still hold.

    Three things it describes are no longer true. The image no longer curls the curated
    mapping YAMLs from `raw.githubusercontent.com` at a pinned `TAXI_SEED_REF` — they
    live in this repo now and are copied in. It no longer installs taxi-seed from PyPI —
    the build context is this repo. And the tool is `taxi-mssql`, not `taxi-seeder`.
**Repo:** `taxi-lab`

## Problem

Producing a usable SQL Server copy of the NYC TLC taxi dataset is currently a
manual sequence: mirror the parquet, normalize it, create a database, load it,
back it up. Every step is individually resumable in `taxi-seed`, but nothing
ties them together, and the database-shaping steps that matter for a
distributable artifact — presized files on a known path, page compression, a
compressed verified backup in a conventional layout — have no home at all.

This spec defines a Docker Compose stack that turns the whole chain into one
command against one folder. Point it at a folder, run it, and it advances the
state of that folder toward a complete deliverable set. Point it at the same
folder again and it picks up wherever it left off.

## Deliverables

Everything lands under one host folder, bind-mounted at `/data`:

| Artifact | Path | Produced by |
|---|---|---|
| Raw parquet mirror | `raw/<type>/<year>/` | `taxi-download` |
| Normalized parquet | `raw-normalized/<type>/<year>/` | `normalize` |
| Presized data file | `mssql/data/taxi.mdf` | seeder |
| Presized log file | `mssql/log/taxi_log.ldf` | seeder |
| Compressed verified backup | `mssql/backup/taxi-sql/taxi/FULL/*.bak` | Ola Hallengren `DatabaseBackup` |

The mdf/ldf are listed as deliverables in their own right: they are the
warm-start path for a future run that wants the loaded database without
restoring a backup.

## Architecture

Two services sharing one bind mount.

```
services:
  mssql:    mcr.microsoft.com/mssql/server:2022-latest
            hostname: taxi-sql            # pins @@SERVERNAME
            user: root                    # bind-mount friendliness
            environment: ACCEPT_EULA, MSSQL_SA_PASSWORD, MSSQL_PID=Developer
            volumes: ${TAXI_DATA_DIR}:/data         # no volume on /var/opt/mssql --
                                                     # master is deliberately ephemeral
            healthcheck: sqlcmd -Q "SELECT 1"      # password via SQLCMDPASSWORD

  seeder:   build: ./seeder
            depends_on: mssql (condition: service_healthy)
            volume: ${TAXI_DATA_DIR}:/data
            working_dir: /data
            restart: "no"                 # runs once to completion, then exits
```

### Why two containers rather than one

Considered and rejected: a single image built `FROM` the SQL Server image with
Python added, running `sqlservr` in the background from the entrypoint. It
matches the "one container" framing literally, but makes the stack own process
supervision for two long-lived services and forces a full image rebuild to
patch either side. Compose gets the same one-command experience
(`docker compose up`) while each image keeps one job and SQL Server stays
vendor-supported.

Also rejected: a seeder-only image pointed at an external SQL Server. It cannot
place mdf/ldf on the mounted folder or presize them unless that server shares
the filesystem, which removes a core requirement.

### Volume layout

`/data` is passed directly to `--data-dir`, so `raw/` and `raw-normalized/`
follow `taxi-seed`'s own conventions rather than invented ones.

```
<host folder>/                    ->  /data
  raw/                            parquet mirror
  raw-normalized/                 normalized parquet
  normalize/mappings/             curated mappings (see "Mappings")
  mssql/
    data/taxi.mdf
    log/taxi_log.ldf
    backup/                       Ola @Directory root
      taxi-sql/taxi/FULL/
        taxi-sql_taxi_FULL_20260801_193312.bak
```

### Container-specific decisions

**Pinned hostname.** Ola's folder structure is
`<Directory>/<@@SERVERNAME>/<database>/<backup type>/`. In Docker,
`@@SERVERNAME` derives from the container hostname, which is random per
`docker run`. Unpinned, every run would write to a new directory and cleanup
would never find the previous backup. `hostname: taxi-sql` makes the path
stable.

**`user: root` on mssql.** The image runs as uid 10001, which cannot write into
a fresh bind-mount subdirectory it does not own. The seeder cannot pre-`chown`
those directories because Compose starts mssql first. Running mssql as root is
Microsoft's documented answer for bind mounts. The README documents
`chown -R 10001:0 <host folder>` as the alternative for anyone who prefers not
to.

**Developer edition.** Backup compression is unavailable in Express, and a
compressed backup is a requirement. Data compression is edition-independent
since SQL Server 2016 SP1, so that part imposes no constraint.

**Ola scripts baked at build time**, pinned to a specific release, installed
with `@CreateJobs = 'N'`. SQL Agent does not run in the container, so job
creation would fail noisily and pointlessly.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TAXI_DATA_DIR` | — | Host folder to bind-mount at `/data`. Required. |
| `MSSQL_PASSWORD` | — | SA password; also consumed by `taxi-load`. Required. |
| `TAXI_TYPES` | `yellow,green,fhv,fhvhv` | Types to mirror, normalize, and load. |
| `TAXI_RECENT` | unset | When set to N, passes `--recent N`; unset means full history. |
| `TAXI_DATA_FACTOR` | `3.0` | Multiplier from normalized parquet bytes to mdf size. |
| `MSSQL_DATA_SIZE_GB` | unset | Explicit mdf size; bypasses the estimate entirely. |
| `MSSQL_LOG_SIZE_GB` | unset | Explicit ldf size; bypasses the default (25% of mdf, floored at 1 GB, capped at 16 GB). |
| `MSSQL_FILEGROWTH_MB` | `1024` | Autogrowth increment for both files. |
| `MSSQL_HOST` | `mssql` | SQL Server host; the Compose service name. |
| `MSSQL_PORT` | `1433` | SQL Server port. |
| `MSSQL_DATABASE` | `taxi` | Database to create and load. |
| `MSSQL_SCHEMA` | `dbo` | Schema holding the loaded tables. |
| `MSSQL_USER` | `sa` | SQL login. |

`MSSQL_DATABASE` and `MSSQL_SCHEMA` are interpolated into T-SQL, so they are
validated as plain identifiers (`[A-Za-z_][A-Za-z0-9_]*`) in `config.from_env`,
at startup. `taxi-db` is legal in SQL Server and rejected here; the alternative
is a run that mirrors for hours and then fails in provisioning. `MSSQL_USER` is
not validated that way — it only ever reaches `sqlcmd -U` and `taxi-load
--user`, never a SQL string, and real logins may contain characters that pattern
rejects.

There is deliberately no year-range variable. `taxi-download` exposes only
`data_type`, `--recent N`, and `--data-dir` — no year filter exists upstream,
and inventing one in `taxi-lab` would mean post-hoc pruning of a mirror the
downloader believes it owns.

The default is the complete dataset: all four types, full history. First run is
long (hours) and produces a large database; every subsequent run is
incremental.

## Run sequence

The seeder is a Python CLI (`taxi_seeder.cli`) that runs seven steps in order
and exits. It invokes `taxi-seed`'s CLIs as subprocesses rather than importing
them, coupling to the documented interface and exit codes rather than to
internals.

```
1. preflight   validate env; create /data subdirs; seed mappings; poll a real
               SQL login until it answers
2. mirror      per type in TAXI_TYPES:
               taxi-run <type> --data-dir /data        (deliberately no --load)
3. provision   if DB_ID('taxi') IS NULL:
                 if taxi.mdf already exists: CREATE DATABASE ... FOR ATTACH
                 else: size from raw-normalized -> CREATE DATABASE
                 then: SET RECOVERY SIMPLE
               else: emit the autogrowth warning if warranted
4. ola         if OBJECT_ID('dbo.DatabaseBackup') IS NULL:
               run MaintenanceSolution.sql against master
5. load        per type:
               taxi-load <type> --data-dir /data --host mssql --database taxi
6. backup      if stale: EXECUTE dbo.DatabaseBackup ...
7. report      print current state: parquet months, normalized months, rows
               per type, backup path and size
```

Step 2 omits `--load` specifically so step 3 can interpose. `taxi-run --load`
would create the database itself via `ensure_database`, which issues a bare
`CREATE DATABASE [taxi]` with server-default file paths and sizes — no
`FILENAME`, no `SIZE`. Splitting the pipeline is the only way to control file
placement and presizing.

## Idempotency

Every step decides whether to skip by looking at something real: a file on disk, a
row in a table, an object in the database. The seeder keeps no state file of its
own, so its view always matches what is actually there.

| Step | Skip condition | Owned by |
|---|---|---|
| download | stop-on-local incremental catch-up | `taxi-download` |
| normalize | existing normalized output | `normalize` |
| provision | `DB_ID('taxi') IS NOT NULL` | seeder |
| ola | `OBJECT_ID('dbo.DatabaseBackup') IS NOT NULL` | seeder |
| load | per-year manifest reconciliation | `taxi-load` |
| backup | newest `.bak` newer than `MAX(loaded_at)` | seeder |

### Backup staleness

`dbo._load_manifest` carries `loaded_at DATETIME2`, written with
`SYSUTCDATETIME()` as each month completes. The test is:

```
back up  if  no .bak exists
         or  MAX(loaded_at) > mtime(newest .bak)          -- both UTC
```

The naive alternative — "did this run load anything?" — has a permanent failure
mode: a run that loads rows and then dies before backing up leaves a database
whose next run loads nothing and therefore never backs up. Comparing against
the manifest closes that hole exactly.

**Invariant:** after a successful run, a verified `.bak` exists that is no older
than the newest loaded month. When no `.bak` exists at all one is taken
unconditionally, including for an empty database, so the deliverable set is
never partial.

## Database provisioning

### Sizing

```
bytes  = sum of size(raw-normalized/<type>/) for type in TAXI_TYPES
raw    = bytes * TAXI_DATA_FACTOR
mdf    = raw rounded up to the next 1 GB, if raw < 10 GB
         raw rounded up to the next 10 GB, otherwise
         (or MSSQL_DATA_SIZE_GB when set, used verbatim)
         floored at 1 GB either way, so a tiny or empty mirror still gets a
         usable, nonzero file
ldf    = min(16 GB, max(1 GB, 25% of mdf))
         (or MSSQL_LOG_SIZE_GB when set, used verbatim)
growth = MSSQL_FILEGROWTH_MB on both files
```

Sizing is computed at run time, never hardcoded. Step 2 completes before step
3, so `raw-normalized/` physically holds exactly the data about to be loaded and
the measurement reflects whatever `TAXI_TYPES` / `TAXI_RECENT` were set. Only
the selected types are summed: the folder may still hold types from an earlier,
broader run, and only `TAXI_TYPES` reaches the database.

**`TAXI_DATA_FACTOR = 3.0` is an estimate, not a measurement.** It must be
calibrated against the first full run and this spec updated with the observed
ratio. Parquet is itself compressed and the target tables are page-compressed,
so the ratio is far narrower than an uncompressed comparison would suggest.
Autogrowth covers an underestimate; `MSSQL_DATA_SIZE_GB` covers a known answer.

**Data granularity scales down for small runs.** A flat 10 GB granularity with
a one-granule floor meant *every* run -- including the `TAXI_RECENT=1` smoke
path's ~128 MB of parquet -- demanded a 10 GB mdf regardless of how little data
it actually held. Below a 10 GB implied size, rounding uses 1 GB granularity
instead, so a 128 MB smoke run gets roughly a 1 GB mdf; at or above 10 GB
implied, rounding is unchanged. The floor is always 1 GB, never 0.

**The log defaults to a fraction of the data size, capped where the old flat
default sat.** Under SIMPLE recovery with `TABLOCK` bulk loads the log
truncates on checkpoint and never tracks data volume, so scaling it linearly to
the data would waste hundreds of GB on a large database -- 16 GB was always
meant as a generous ceiling on the default, not a floor or a target. Left
unset, `MSSQL_LOG_SIZE_GB` now defaults to 25% of the mdf size, floored at 1 GB
and capped at 16 GB: a full-history run still lands at exactly 16 GB, and a
small run's ldf shrinks along with its mdf instead of demanding 16 GB flat. An
explicit `MSSQL_LOG_SIZE_GB` is still honoured verbatim, same as
`MSSQL_DATA_SIZE_GB`.

### Free space check

Before issuing `CREATE DATABASE`, provisioning compares the planned mdf + ldf
allocation (plus a small fixed headroom) against `shutil.disk_usage` on
`TAXI_DATA_DIR`. If it will not fit, the run stops with a typed, exit-2 error
naming the required size, the free size, and the two levers
(`MSSQL_DATA_SIZE_GB` / `MSSQL_LOG_SIZE_GB`, or a bigger disk) rather than
handing the operator a bare SQL Server OS error mid-`CREATE DATABASE`. The
check only runs on the create path -- attaching an existing mdf/ldf allocates
nothing new, so there is nothing to check.

### DDL

```sql
CREATE DATABASE [taxi]
  ON      (NAME = taxi,     FILENAME = '/data/mssql/data/taxi.mdf',
           SIZE = <mdf>, FILEGROWTH = <growth>)
  LOG ON  (NAME = taxi_log, FILENAME = '/data/mssql/log/taxi_log.ldf',
           SIZE = <ldf>, FILEGROWTH = <growth>);
ALTER DATABASE [taxi] SET RECOVERY SIMPLE;
```

### Warm start: attach rather than create

The mdf/ldf are deliverables in their own right, and they live on the bind mount
— but the system databases that *record* them (`master`, `msdb`) live in
`/var/opt/mssql`, inside the container. There is deliberately no volume there:
`master` is left to reset on every container start, and provisioning's attach
branch is what keeps the user database safe across that reset — not
persistence. A named volume was tried and reverted; see below.

Every start therefore begins with a fresh `master`, so `DB_ID('taxi')` is
always NULL at the top of provisioning — there is no warm case where it is
not. When the mdf already exists at `mssql/data/taxi.mdf` (the common case:
any run after the first), provisioning attaches rather than creates:

```sql
CREATE DATABASE [taxi]
  ON (FILENAME = '/data/mssql/data/taxi.mdf'),
     (FILENAME = '/data/mssql/log/taxi_log.ldf')
  FOR ATTACH;
```

This makes attach the *normal* warm-start path, exercised on every restart —
not a rare `down -v` edge case. Without it, `CREATE DATABASE` fails with Msg
5170 ("cannot create file … because it already exists") and keeps failing on
every subsequent run, since nothing about the state changes. The only escape
would be deleting the files and reloading from parquet.

**Why not persist `master` instead.** A named volume on `/var/opt/mssql` was
the first fix tried, so `master` would survive `docker compose down` and the
attach branch would only need to cover `down -v`. It was reverted because
persisting `master` traded one bug for two:

- **A moved `TAXI_DATA_DIR` broke.** A persisted `master` still records the
  old path, so provisioning found `DB_ID('taxi')` non-NULL, took the skip
  branch, and failed opaquely reading file sizes of a database it could not
  open at the new path — *after* the mirror step had already run, which on a
  full dataset is hours of wasted work.
- **A rotated `MSSQL_PASSWORD` silently never took effect.** SQL Server only
  consumes `MSSQL_SA_PASSWORD` on first initialization of `/var/opt/mssql`.
  With a persisted `master`, every start after the first is not a first
  initialization, so a changed password left the old one live in the
  persisted system databases and the healthcheck never cleared.

With no volume, every start *is* a first initialization: a moved data
directory is just an mdf the fresh `master` has never heard of and attaches,
and a rotated password is always consumed. The one cost is that Ola's
MaintenanceSolution reinstalls on every start rather than only the first —
`ola.run` already guards on `OBJECT_ID('dbo.DatabaseBackup') IS NULL` and is
idempotent, so this is a few seconds, not a real cost. `dbo._load_manifest` —
the table resumability and backup-staleness both depend on — lives in the
`taxi` database, not `master`, so it survives the attach untouched.

SIMPLE recovery: this is a rebuildable seed database whose real source of truth
is the parquet, so point-in-time recovery has no meaning. Bulk loads stay
minimally logged and the ldf stays small and stable across runs.

### Autogrowth warning

When step 3 is skipped because the database exists, the seeder still computes
the implied size and compares it against the mdf's actual allocated size from
`sys.database_files`. If implied exceeds allocated by more than 25%, it warns
with both numbers and the remedy.

This exists because sizing runs *only* at creation. The awkward path is a first
run with `TAXI_RECENT=3` followed later by a full-history run: the database is
never re-presized and autogrows to several hundred GB in 1 GB increments,
producing exactly the file fragmentation presizing was meant to avoid. The
warning turns a silent performance cliff into a visible one.

Remedies, both documented: set `MSSQL_DATA_SIZE_GB` on the first run when the
eventual scope is already known, or re-presize — `DROP DATABASE [taxi]`, *then*
delete the mdf and ldf, then re-run, so the next run finds no database, no files
to attach, re-presizes from the now-complete `raw-normalized/`, and reloads from
parquet.

The order matters and the warning states it. Deleting the files while the server
still has the database registered leaves it `RECOVERY_PENDING`: `DB_ID('taxi')`
is non-NULL, so provisioning takes the skip branch and then fails querying
`sys.database_files` on a database that cannot be opened.

### Instant file initialization — open risk

Presizing a multi-hundred-GB mdf is fast only if SQL Server skips zero-filling
the file first. Windows offers this as instant file initialization, through the
`SE_MANAGE_VOLUME_NAME` privilege. On SQL Server on Linux the behavior depends on
the host filesystem, and this spec does **not** confirm it either way. If SQL
Server does zero-fill, first-run provisioning of a large mdf can take a very long
time.

**The risk stays open.** The integration suite in
`tests/taxi_seeder/test_integration_compose.py` now runs against a live Docker
daemon, but it runs with `TAXI_RECENT=1` — roughly 128 MB of parquet, and roughly
a 1 GB mdf. That size is far too small to show the difference. A run at
full-history scale is what will answer the question.

Until then, provisioning stays loud about it. `provision.run` prints the total
size it is about to allocate, and warns that the single `CREATE DATABASE` produces
no output and may run for a long time. A user watching a still terminal therefore
knows the run is alive. That `sqlcmd` call has no timeout, on purpose: a timeout
whose correct value is unknown would abort legitimate work.

If a full-scale run shows that allocation is slow, the fallback stands: a modest
initial size plus 1 GB autogrowth. That surrenders some fragmentation benefit and
nothing else. Log files are zero-filled on every platform, which is a further
argument for keeping the ldf's default capped instead of scaling it with a large
mdf.

## Page compression

Tables are page-compressed. This requires a change in `taxi-seed`, because
`taxi-seed` owns the DDL: `taxi_shared.sql_generator.generate_create_table_sql`
emits a plain `CREATE TABLE`, and `taxi-load` creates each `<type>_<year>` table
lazily at load time — on the append path via `_ensure_table`, and on the reload
path via an unconditional `DROP TABLE` plus recreate.

**Upstream change:** `generate_create_table_sql` appends
`WITH (DATA_COMPRESSION = PAGE)` unconditionally. No flag, no CLI plumbing.
`taxi-lab`'s seeder image then pins `taxi-seed>=<that release>`.

Two consequences to hold deliberately:

- It changes behavior for every `taxi-seed` user, not only this container.
- It also applies to `dbo._load_manifest`, which is built through the same
  function. Page-compressing a small bookkeeping table is harmless, but it is a
  consequence of the choice rather than an intent of it.

Alternatives rejected:

- **Post-load `ALTER TABLE ... REBUILD`** in `taxi-lab`. Requires no upstream
  change and is correct whatever produced the table, but data lands uncompressed
  first, so peak mdf demand becomes the uncompressed size plus rebuild working
  space — roughly 3-4x the compressed result, which defeats presizing.
- **Pre-creating compressed tables** in `taxi-lab` before `taxi-load` runs.
  Duplicates `taxi-seed`'s type mapping, and the reload path's `DROP TABLE`
  silently reverts a year to uncompressed — a trap sprung precisely by
  `--full-refresh`.

## Backup

```sql
EXECUTE dbo.DatabaseBackup
  @Databases   = 'taxi',
  @Directory   = '/data/mssql/backup',
  @BackupType  = 'FULL',
  @Compress    = 'Y',
  @CheckSum    = 'Y',
  @Verify      = 'Y',
  @CleanupTime = 1,
  @CleanupMode = 'AFTER_BACKUP';
```

`AFTER_BACKUP` cleanup means the previous backup is deleted only once the new
one is written and verified. There is never a window with zero good backups —
which a `BEFORE_BACKUP` cleanup, or a literal overwrite, would both open.

**A backup that fails verification is deleted by the seeder.** Ola's response to
a failed `RESTORE VERIFYONLY` is to skip *cleanup*, not to remove the bad `.bak`
— which therefore stays on disk with a fresh mtime. Since staleness is an mtime
comparison with no notion of "did this verify", that file would make the next
run print "backup: current" and exit 0 offering a corrupt deliverable. So the
backup step records the newest `.bak` before it starts and, if the backup fails,
removes anything newer than that before re-raising. The previous verified backup
is never touched.

`@CleanupTime = 1` deletes backups older than one hour, so the steady state is a
single `.bak`. Precisely: two runs less than an hour apart leave two files
briefly, until the next run ages the older one out. This is a deliberate
trade — the alternative, `@CleanupTime = 0`, is how Ola spells "delete
everything", and combined with any cleanup ordering it is the one setting that
can leave nothing behind.

## Mappings

`normalize` resolves its curated mappings from a hardcoded relative path
(`normalize/src/taxi_normalize/cli.py:62`):

```python
mapping_path = Path("normalize") / "mappings" / f"{data_type}.yaml"
```

There is no flag and no env var, and `normalize/mappings/` is not in the wheel —
`[tool.hatch.build.targets.wheel]` ships only the `src/` packages. A
PyPI-installed `normalize` therefore works only when the current working
directory contains `normalize/mappings/*.yaml`.

The seeder controls CWD rather than working around the path:

1. The image bakes pristine copies of the four YAMLs at `/opt/taxi-seed/mappings/`.
2. Preflight copies them to `/data/normalize/mappings/` if absent.
3. Everything runs with `WORKDIR /data`.

All data paths are absolute via `--data-dir /data`, so nothing else is affected.

Mappings live on the volume rather than in the image because `normalize` amends
them when it encounters schema drift. Edits inside an image layer die with the
container, so drift would re-halt every run. On the volume they persist, and can
be inspected when a run exits `1` requesting drift acknowledgment.

`taxi-run`'s `find_repo_root` walks up looking for a directory containing both
`pyproject.toml` and `normalize/mappings`; finding neither, it falls back to
CWD, which is `/data`. This is its documented fallback, not a workaround.

## Error handling

Each step maps failure onto `taxi-seed`'s existing convention:

| Code | Meaning | Examples |
|---|---|---|
| 0 | clean | everything current, or advanced successfully |
| 1 | needs review | normalize halted on unacknowledged schema drift |
| 2 | operational failure | SQL Server unreachable, `CREATE DATABASE` failed, the data volume cannot fit the planned allocation, backup failed verification |

The mapping is step-aware, because the child CLIs do not agree on what `1`
means. `taxi-run` returns `1` only for normalize's schema-drift halt, which is
exactly this table's "needs review". `taxi-load` returns `1` for *failed
mid-load* — taxi-seed's own orchestrator classifies that as `PARTIAL` and maps
it to `2`. The seeder does the same: a mid-load failure exits `2`, so a
transient operational fault is never reported as blocked-on-a-human when a plain
re-run is the remedy. The child's own exit code is still printed.

A step exiting `1` or `2` stops the run there. No type is loaded whose normalize
halted; no database is backed up whose load failed. Partial progress is always
safe because every step is independently resumable — the next run resumes from
what is actually on disk and in the manifest, not from a recorded intention.

One case deserves a note. When `@Verify='Y'` fails, the previous good backup is
still on disk, because cleanup is `AFTER_BACKUP`. The run exits `2` and destroys
nothing.

## Repo layout

```
taxi-lab/
  pyproject.toml            two packages, one env; extras: [k6], [seeder]
  README.md
  docs/specs/
  k6-loadtest/              migrated from taxi-seed (see below)
    README.md  build_k6.sh  config.sample.yaml
    src/k6_loadtest/{__init__,config,data_export,k6_generator,preprocess}.py
  seeder/
    Dockerfile  docker-compose.yml
    src/taxi_seeder/{cli,preflight,provision,ola,backup,report}.py
    sql/                    pinned Ola MaintenanceSolution.sql
```

The seeder is split one module per step. Modules compute and return; a thin
execution layer runs what they produce. `provision.py` calculates sizes and
emits DDL as a string; it does not execute it. This keeps the logic that carries
the risk testable without a database.

## Testing

**Unit** — the pure logic, which is where the risk concentrates:

- size calculation, rounding (both the 1 GB and 10 GB granularities and their
  switchover), `MSSQL_DATA_SIZE_GB` / `MSSQL_LOG_SIZE_GB` overrides, the log's
  default fraction/floor/cap, per-type summing
- free-space arithmetic: required-vs-free, independent of the filesystem
- backup staleness: no `.bak`; `.bak` newer than `MAX(loaded_at)`; `.bak` older
  than `MAX(loaded_at)` (the died-before-backup case); NULL manifest
- autogrowth warning threshold, above and below 25%
- generated DDL strings

**Integration** — one test running the real Compose stack with
`TAXI_TYPES=yellow TAXI_RECENT=1`, small enough to finish in minutes:

1. First run: assert the full deliverable set exists — raw parquet, normalized
   parquet, mdf at the expected size, ldf, one verified `.bak` in the Ola path,
   and that the loaded tables report `PAGE` compression in `sys.partitions`.
2. Second run against the same folder: assert it is a near no-op — no new
   backup, no rows loaded, exit 0.

The second run is the actual test of the resumability premise, and is the one
that would catch a regression in any of the six skip conditions.

## Out of scope

- k6 load-testing migration. Same repo, no shared code or runtime; tracked
  separately.
- Any year-range filtering (`--years` upstream in `taxi-download`).
- Restoring the backup anywhere, or publishing it.
- Log backups, or any recovery model other than SIMPLE.
- Multi-database or multi-server seeding.

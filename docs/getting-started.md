# Getting Started

This tutorial walks a data engineer from a clean laptop to normalized parquet on disk in about 10 minutes on the yellow-only "happy path". Optional sections at the end cover schema-drift and normalize; each adds a couple of minutes. By the time you finish, you'll have three months of NYC TLC yellow taxi data downloaded, verified, and passed through the normalizer.

The commands below assume a POSIX shell (bash or zsh) and a working internet connection. Everything runs locally — no cloud accounts, no credentials, no external services. If any step fails, the error output should point you at the relevant guide; the "Where to next" section at the bottom lists all of them.

## Prerequisites

- Python 3.12 or 3.13.
- [uv](https://github.com/astral-sh/uv) installed.
- curl (present on macOS and most Linux; on Windows install [Git for Windows](https://gitforwindows.org/) and run everything in Git Bash).
- ~300 MB free disk (`--recent 3 yellow` is ~200 MB; venv + wheels are ~100 MB).
- Optional: [DuckDB CLI](https://duckdb.org/docs/installation/) for the "look at what you got" step (not required; you can do the same via `uv run python -c "..."`).

!!! warning "WSL users"
    If you're running on WSL2, your data will land on the Linux filesystem — which grows the WSL2 VHDX on your Windows C: drive and does NOT shrink when files are deleted. Point downloads at a Windows path with `OUTPUT_DIR=/mnt/c/Users/$USER/taxi-data` instead. See the [Downloader guide's WSL section](guides/downloader.md#windows-wsl) for full details.

## 1. Clone and install

```bash
git clone https://github.com/andrekamman/taxi-seed.git
cd taxi
uv sync --extra test
```

The `--extra test` flag pulls in pytest alongside the runtime dependencies so you can verify the install by running the suite in the next step. If you'd rather skip that, plain `uv sync` will install just the runtime.

`uv sync` reads `pyproject.toml` + `uv.lock` and materializes the exact locked versions into `.venv/`. If you already have a system Python but no `uv`, the [uv install script](https://github.com/astral-sh/uv#installation) is a single-line `curl | sh` (or `brew install uv` on macOS). uv also manages the interpreter itself, so you don't need to pre-install Python 3.12 or 3.13 — it'll fetch a compatible build the first time you run `uv sync`.

!!! tip "Building docs locally"
    Prefer to preview these docs while reading them? `uv sync --extra docs` installs mkdocs + the Material theme, and `uv run --extra docs mkdocs serve` boots the site on `http://127.0.0.1:8000/` with live reload as you edit.

## 2. Verify install

```bash
uv run --extra test pytest -q
```

Expected output:

```text
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 0.94s
```

The exact test count grows as the suite grows; what matters is that there are no failures. The first invocation of `uv run` also builds the virtualenv (`.venv/`), so expect a one-time delay of 5–20 seconds while wheels are resolved and installed. Subsequent invocations reuse the cached environment.

## 3. Download three recent months of yellow

```bash
./downloader/download_taxi_data.sh --recent 3 yellow
```

Expected output:

```text
Checking for corrupt parquet files...
  No corrupt files found

NYC TLC Trip Data Downloader
Generating URL list up to: 2026-06

Downloading recent 3 months (yellow)...
Will walk back through older not-yet-published months, and stop
at the first locally-existing file (assumes prior runs are caught up).

--- yellow: looking for 3 recent months ---
  Trying yellow_tripdata_2026-06.parquet...
    Not published yet, trying older month
  Trying yellow_tripdata_2026-05.parquet...
    Saved to yellow/2026/yellow_tripdata_2026-05.parquet
  Trying yellow_tripdata_2026-04.parquet...
    Saved to yellow/2026/yellow_tripdata_2026-04.parquet
  Trying yellow_tripdata_2026-03.parquet...
    Saved to yellow/2026/yellow_tripdata_2026-03.parquet
  Downloaded 3 new file(s) for yellow (walked back 4 month(s))

Download complete!
Files saved to: raw/<type>/<year>/
```

Here's what happened: the walker started at the previous month (2026-06 in this example), found it wasn't published yet, then walked backward and downloaded the three most recent published months. Total: ~200 MB in ~1–2 min on a typical connection.

The `--recent N` flag differs from `--from` / `--to`: it always anchors to "today minus one month" and walks backward until it either finds `N` new files or hits a locally-existing file (which signals it can stop, because prior runs already caught up to that point). That makes it the right flag for a nightly cron; use `--from YYYY-MM --to YYYY-MM` when you want an explicit window instead.

The downloader also skips corrupt local files by silently overwriting them, and it validates each new download by opening it with a lightweight parquet reader before declaring it saved. The [Downloader guide](guides/downloader.md) covers the WAF classifier that distinguishes "not published yet" from "throttled" from "hard 4xx" — a distinction that matters when you're building automation on top.

!!! warning "Windows / WSL disk usage"
    On WSL2 the raw parquet lands on the Linux filesystem and permanently grows your WSL2 VHDX. See the [Downloader guide](guides/downloader.md#windows-wsl) for the `OUTPUT_DIR` workaround.

## 4. Look at what you got

**Find the files:**

```bash
find raw/yellow -name '*.parquet'
```

Expected output:

```text
raw/yellow/2026/yellow_tripdata_2026-03.parquet
raw/yellow/2026/yellow_tripdata_2026-04.parquet
raw/yellow/2026/yellow_tripdata_2026-05.parquet
```

**Check disk usage:**

```bash
du -sh raw/yellow
```

Expected: `200M	raw/yellow` (or similar, depending on the specific months).

**Peek at the schema via DuckDB (no CLI needed):**

```bash
uv run python -c "
import duckdb, glob
f = sorted(glob.glob('raw/yellow/**/*.parquet', recursive=True))[-1]
print(f)
print('---')
for r in duckdb.execute(f\"DESCRIBE SELECT * FROM '{f}'\").fetchall():
    print(r)
"
```

Expected output (truncated):

```text
raw/yellow/2026/yellow_tripdata_2026-05.parquet
---
('VendorID', 'INTEGER', 'YES', None, None, None)
('tpep_pickup_datetime', 'TIMESTAMP', 'YES', None, None, None)
('tpep_dropoff_datetime', 'TIMESTAMP', 'YES', None, None, None)
('passenger_count', 'BIGINT', 'YES', None, None, None)
('trip_distance', 'DOUBLE', 'YES', None, None, None)
('RatecodeID', 'BIGINT', 'YES', None, None, None)
('store_and_fwd_flag', 'VARCHAR', 'YES', None, None, None)
...
```

Each tuple is `(column_name, type, nullable, key, default, extra)`. Note that `passenger_count` is `BIGINT` here — in older files (pre-2015) it was `INTEGER`, which is the sort of transition the schema-drift tool detects and the normalizer resolves.

If you installed the DuckDB CLI, the equivalent one-liner is:

```bash
duckdb -c "DESCRIBE SELECT * FROM 'raw/yellow/2026/yellow_tripdata_2026-05.parquet'"
```

Same output, tabulated. Either approach works.

## 5. (Optional) Run schema-drift

Schema-drift walks the full parquet family for a taxi type and reports each transition where columns were added, removed, renamed, or type-changed. Even on our tiny 3-month sample it's a good smoke test.

```bash
uv run schema-drift --types yellow
```

Expected output (excerpt):

```text
================================================================================
SCHEMA DRIFT REPORT
================================================================================
Taxi type: yellow
Files scanned: 3
Transitions detected: 0

--------------------------------------------------------------------------------
(Illustrative excerpt from a full multi-year run)
--------------------------------------------------------------------------------
Transition: 2015-06 -> 2015-07
  Added columns:
    - improvement_surcharge (DOUBLE)
  Renamed columns (high confidence):
    - pickup_longitude -> PULocationID  [type change: DOUBLE -> BIGINT]
  Unresolved:
    - dropoff_latitude (removed; no obvious rename target — needs human review)
================================================================================
```

Read the report top-to-bottom: each transition block corresponds to one month-to-month schema change. Look for high-confidence rename pairs (those are safe to accept), and pay special attention to the "Unresolved" section — those items need a human to decide whether the column was dropped, renamed to something the heuristic missed, or something else.

Rename detection uses a combination of positional overlap (adjacent-in-file-order), type compatibility, and name similarity — so `pickup_longitude -> PULocationID` is flagged high-confidence only when the surrounding columns line up in a way that makes accidental co-occurrence unlikely. On real historical data the yellow trips schema went through three major transitions (2015-07, 2016-07, and the 2016 payment-column split); running `schema-drift --types yellow` against a full mirror surfaces all three.

The [Schema Drift guide](guides/schema-drift.md) covers the report format in detail and describes all three modes (report / mapping / diff).

## 6. (Optional) Normalize

Normalize rewrites historical parquet so every file matches the latest schema. It halts on data loss (lossy casts, dropped columns) unless you explicitly acknowledge each such change by filling in an `ack_date:` in the mapping file. On first run for a taxi type it bootstraps a mapping and asks you to review it — even when there's nothing to review.

**First run:**

```bash
uv run normalize yellow
```

Expected output:

```text
yellow: no mapping found — analyzed raw data and wrote normalize/mappings/yellow.yaml.
  Detected 0 drift transition(s) — see file header.
  0 SUGGESTED/TODO item(s) need your review.

Next steps:
  1. Review normalize/mappings/yellow.yaml
  2. Uncomment SUGGESTED entries you accept, delete the rest
  3. Fill in `ack_date:` for each TODO to acknowledge lossy casts or data loss
  4. Re-run: uv run normalize yellow
```

On a small `--recent 3` sample, all three months sit inside the stable-schema era (2015+), so there are no drift transitions and no `SUGGESTED`/`TODO` items to review. The tool still exits with status 3 the first time — it wants a human confirmation that the empty mapping is what you intended before it writes any output.

Open `normalize/mappings/yellow.yaml` in your editor if you're curious: the file header lists the detected transitions (none in this case), the target schema (the latest month), and a `mappings:` block containing one entry per transition. On a full multi-year run this file is where you'd uncomment accepted rename suggestions and fill in `ack_date:` fields for anything lossy. See the [Normalize guide](guides/normalize.md) for the full anatomy.

**Second run** (re-running is the confirmation):

```bash
uv run normalize yellow
```

Expected output:

```text
yellow: 3 file(s) normalized, 0 skipped (already present).
```

**Verify:**

```bash
find raw-normalized/yellow -name '*.parquet'
```

Expected: three normalized files under `raw-normalized/yellow/2026/`.

On this tiny recent sample the tool is essentially a passthrough — the target schema equals the raw schema, so files copy over unchanged. In production, `raw-normalized/` is the layer downstream tools should point at: the K6 load test reads from it, and the DuckDB `httpfs` recipes in the cookbook query it. Rerunning normalize is idempotent — files already present in `raw-normalized/` are skipped, so nightly cron jobs stay cheap.

The [Normalize guide](guides/normalize.md) walks through the real-world case where drift exists and the mapping is nontrivial (renames, type coercions, dropped columns, and the `ack_date:` gate).

## Where to next

- **[Downloader guide](guides/downloader.md)** — WAF classifier, disk sizing table, WSL warning explained in detail.
- **[Schema Drift guide](guides/schema-drift.md)** — three modes, how rename detection works, programmatic API.
- **[Normalize guide](guides/normalize.md)** — three-state model, bootstrap+amend semantics, two worked examples.
- **[Cookbook](cookbook.md)** — nightly cron, DuckDB `httpfs`, corporate proxy, populating a fresh dev SQL Server.
- **[Architecture](architecture.md)** — the four-tool DAG and the core design principles.

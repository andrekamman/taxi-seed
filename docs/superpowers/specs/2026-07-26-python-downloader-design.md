# Python downloader (`taxi_download`) — design spec (2026-07-26)

**Goal:** Replace the 488-line bash downloader (`downloader/download_taxi_data.sh`) with a Python
package `taxi_download` that ships in the wheel and is driven by a `taxi-download` console script.
This makes taxi-seed's download stage installable and runnable from a plain `pip install taxi-seed`
(no repo checkout, no `bash`/`curl` shell-out), and easier to explain and maintain.

**Piece 2 of 3** (k6 removal ✅ → **Python downloader** → docs sweep). Ships as a PR into `dev`.

---

## Decisions locked (from brainstorming)

- **New runtime dependency: `httpx>=0.27`** (deps become `duckdb`, `pyyaml`, `httpx`). Chosen for a
  clean streaming/timeout/redirect API over hand-rolled `urllib`.
- **Clean reimplementation**, not a 1:1 bash port: keep the essential domain behavior, drop the
  bash-isms.
- **Package** `downloader/src/taxi_download/` → console script **`taxi-download`**; added to the wheel
  `packages` list. The orchestrator invokes it via `python -m taxi_download.cli` (like normalize/load).

---

## Behavior to preserve (from the bash script)

- **CloudFront URL scheme:** `https://d37ci6vzurychx.cloudfront.net/trip-data/<type>_tripdata_<year>-<mm>.parquet`.
- **Four types + per-type start dates:** `yellow` 2009-01, `green` 2013-08, `fhv` 2015-01, `fhvhv` 2019-02.
- **Full-history mode:** forward walk from the type's start date through the **previous** calendar month.
  Skip months whose file already exists locally. If a month 404s *after* data has been seen, treat it
  as end-of-series and stop that type; a 404 *before* any data is "pre-series", skip forward.
- **`--recent [N]` mode:** backward walk from the previous month; **only successful downloads count
  toward N**; stop early when an already-present local file is hit; cap total months examined at
  `MAX_LOOKBACK = 18`.
- **Output layout:** `<data-dir>/raw/<type>/<year>/<file>`; skip files that already exist.
- **PAR1 corrupt-file validation:** a valid parquet has magic bytes `PAR1` at both the first and last
  4 bytes. Run an **upfront cleanup pass** (scan `<data-dir>/raw`, delete any parquet failing the
  check) before downloading, and **re-validate each freshly downloaded file** (delete + treat as a
  failed fetch if invalid).
- **Rate-limit resilience:** TLC's CloudFront rate-limits and sometimes returns a `200` HTML intercept
  page instead of the parquet. Handle both.

## Design

### Package structure (`downloader/src/taxi_download/`)
- `__init__.py`
- `dates.py` — pure month arithmetic: `previous_month(today) -> (year, month)`; `START_DATES`;
  iterators `months_forward(start, end)` and `months_backward(start)`. No I/O.
- `download.py` — the fetch + validation + walker core (see below). Takes an injected `httpx.Client`
  and a `now`/`today` value so it is unit-testable without network or wall-clock.
- `cli.py` — argparse + `main(argv=None) -> int`.

### Constants (`download.py` / `dates.py`)
```python
BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"
DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
START_DATES = {"yellow": (2009, 1), "green": (2013, 8), "fhv": (2015, 1), "fhvhv": (2019, 2)}
MAX_LOOKBACK = 18
PARQUET_MAGIC = b"PAR1"
# backoff: base 30s, factor 3, capped at 3600s, up to MAX_RETRIES attempts
BACKOFF_BASE_S, BACKOFF_FACTOR, BACKOFF_CAP_S, MAX_RETRIES = 30, 3, 3600, 4
```

### Core functions (`download.py`)
- `filename(type, year, month) -> str` → `f"{type}_tripdata_{year}-{month:02d}.parquet"`.
- `url_for(type, year, month) -> str` → `f"{BASE_URL}/{filename(...)}"`.
- `target_path(raw_dir, type, year, month) -> Path` → `raw_dir/type/str(year)/filename(...)`.
- `is_valid_parquet(path) -> bool` → file ≥ 8 bytes and first 4 == last 4 == `PAR1`.
- `clean_corrupt(raw_dir) -> int` → scan `raw_dir.rglob("*.parquet")`, delete invalid, return count.
- `FetchResult = Enum("OK", "NOTFOUND", "RATELIMIT", "NETERROR")`.
- `fetch_one(client, url, dest) -> FetchResult` — stream the GET to a temp file; on `200` validate
  PAR1 (invalid → `RATELIMIT`, the intercept-page case); rename into place on success. Map status:
  `404`/`403(AccessDenied|NoSuchKey)` → `NOTFOUND`; `429`/`5xx`/`403(cloudfront/blocked)` → `RATELIMIT`;
  transport errors → `NETERROR`. **No retry here** — the caller owns retry/backoff.
- `download_month(client, type, year, month, raw_dir, sleeper) -> FetchResult` — existence-skip;
  else `fetch_one` with capped-exponential-backoff retry on `RATELIMIT`/`NETERROR` up to `MAX_RETRIES`,
  then give up (returns the last result). `sleeper` is an injected sleep fn (no-op in tests).
- `download_full(client, type, raw_dir, today, sleeper) -> int` — the forward walker (returns count).
- `download_recent(client, type, raw_dir, n, today, sleeper) -> int` — the backward walker.

### CLI (`cli.py`)
- `argparse`: positional `data_type` (optional, choices = the four types; omit = all four),
  `--recent [N]` (`nargs="?"`, `const=3`, i.e. default 3 months when N omitted — matching the bash
  default), `--data-dir DIR` (default `"."`).
- `main`: resolve `raw_dir = Path(data_dir) / "raw"`; `clean_corrupt(raw_dir)`; build an
  `httpx.Client` (follow redirects, sane timeout); for each selected type run `download_recent` or
  `download_full`; print per-type + total summary; return exit code.
- **Exit codes:** `0` success (including skip-only / nothing-new); `2` on bad args or when a
  rate-limit give-up prevented all progress for a requested type (so the orchestrator can halt). A
  transient single-month failure that still lets the walk continue is not fatal.

### Dropped from the bash version
- The separate two-phase HEAD/range **probe** (folded into a single GET + PAR1 validation).
- The exact `300/900/3600s` escalation → a clean capped exponential backoff.
- The **WSL-VHDX interactive warning**.
- The legacy **`OUTPUT_DIR`** env override (only `--data-dir` remains).

## Orchestrator integration (`orchestrator/src/taxi_orchestrate/`)
- `stages.py::build_download_cmd` — change from `["bash", str(repo_root/"downloader"/"download_taxi_data.sh"), …]`
  to `[sys.executable, "-m", "taxi_download.cli", …]`, preserving the arg surface: append `--recent`
  (+ `str(N)` when `N > 0`), the `data_type` when set, and `--data-dir <data_dir>`. The `repo_root`
  parameter is no longer needed for the download command (the module is importable) — keep the stage
  running with `cwd=repo_root` for consistency, but the command itself no longer references the repo.
- `cli.py::find_repo_root` — its marker `downloader/download_taxi_data.sh` is going away; change the
  marker to `pyproject.toml` **and** `normalize/mappings/` (still present in the repo; still needed as
  `cwd` so `normalize` finds its mappings).
- Update the existing `tests/taxi_orchestrate/test_stages.py` download-command assertions to the new
  `python -m taxi_download.cli` form.

## Packaging (`pyproject.toml`)
- Add `"httpx>=0.27"` to `[project.dependencies]`.
- Add `"downloader/src/taxi_download"` to `[tool.hatch.build.targets.wheel].packages`.
- Add `taxi-download = "taxi_download.cli:main"` to `[project.scripts]`.
- Regenerate `uv.lock` (`uv sync`).

## Removals
- Delete `downloader/download_taxi_data.sh`.
- Delete/replace `tests/downloader/test_output_dir.py` (it drives the bash script) with the new Python
  tests.
- `downloader/README.md` — update its pointer to the Python module (kept minimal; the full guide is
  authored in the docs sweep, Piece 3).

## Testing strategy
- **Pure units (no network):** `dates.py` (previous_month wrap, forward/backward iterators),
  `filename`/`url_for`/`target_path`, `is_valid_parquet` (good/short/bad-magic), `clean_corrupt`
  (deletes only invalid), and the `--data-dir` → `raw/` resolution.
- **Fetch/walker with a local stub HTTP server** (stdlib `http.server` on `127.0.0.1:0`, no real
  network): serve a tiny valid parquet (built with DuckDB `COPY … TO`) for a set of months, `404` for
  others, and one `429` to exercise a backoff-then-succeed path (with an injected no-op `sleeper`).
  Assert: correct files land under `raw/<type>/<year>/`, existing files are skipped, `--recent N`
  stops at the right count / on an existing file, and a persistent `429` yields the give-up exit code.
- **Determinism:** inject `today` and `sleeper` so walkers and backoff are wall-clock-free and instant.
- Full suite stays green; `taxi-run --dry-run` shows the new `python -m taxi_download.cli` command.

## Out of scope (explicit)
- **Packaging the normalize mappings** so a pip-installed `taxi-run` can normalize end-to-end — this is
  the separate **Piece 2.5** (mappings via `importlib.resources`). The downloader piece does not touch
  normalize.
- Parallel/concurrent downloads (TLC rate-limits; sequential-with-backoff is intentional).
- Re-authoring the downloader *guide* / broader docs (that's Piece 3).
- Any change to the `raw/` layout or the `--data-dir` semantics established earlier.

## Notes for the plan
- Keep `download.py` functions injectable (`client`, `today`, `sleeper`) so every branch is testable
  offline and instantly.
- The stub-server test builds its sample parquet with DuckDB (already a dep) to guarantee real PAR1
  magic bytes.
- Confirm `httpx` resolves cleanly in `uv.lock` and doesn't conflict with `duckdb`/`pyyaml`.
- The `--recent [N]` `const` default must be **3** to match the bash default when `N` is omitted.

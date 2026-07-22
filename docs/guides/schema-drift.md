# Schema Drift

The `schema-drift` tool analyzes a family of parquet files representing different time periods and reports how their schemas changed over time. It walks the files in chronological order, identifies the transition points where the schema actually changed, and at each transition classifies the deltas as columns added, columns removed, columns type-changed, or columns renamed. Rename detection uses a domain-aware heuristic (with an optional data-verification pass) or a purely data-driven mode for non-TLC datasets.

It is used two ways. As a standalone CLI it produces a human-readable text report — useful before you ingest historical TLC data so you know exactly what shape shifts to expect. Programmatically, `normalize bootstrap` calls the same analyzer to seed its mapping YAML with the tool's rename suggestions, which you then review and edit before running a full normalize.

## When to use it

Two scenarios drive most usage:

- **Before ingesting historical TLC data.** Know what schema changes you're going to hit before you write ingest code. The report tells you exactly which columns appear/disappear and at which month, so you can plan your ingest schema and write the right conditionals up-front instead of hitting DuckDB parse errors mid-load.
- **Before authoring a `normalize` mapping.** Run the tool, review the SUGGESTED renames, and decide which to accept. `normalize bootstrap` runs this analyzer for you and writes its suggestions into a mapping YAML, but running it standalone first lets you inspect the raw suggestions (and their confidences) without the bootstrap step overwriting anything.

## Prerequisites

Install dependencies with `uv sync` in the `schema-drift/` directory.

You need a parquet family: either the [downloader](downloader.md)'s `raw/` mirror or any directory of parquet files whose names follow the convention `<type>_tripdata_YYYY-MM.parquet`. That pattern is required — the analyzer extracts the period from the filename to order the files and label the transitions.

## Basic usage

```bash
# Default: analyze all four types (yellow, green, fhv, fhvhv) in raw/
uv run schema-drift

# One type only
uv run schema-drift --types yellow

# Write to a file instead of stdout
uv run schema-drift --output drift-report.txt
```

The default `--data-dir` is `raw/`. Pass `--data-dir some/other/dir` to point elsewhere. `--types` accepts any subset of `yellow green fhv fhvhv` (or, in generic mode, any subdirectory name). The report always includes a CROSS-TYPE SUMMARY that lists which columns appear in which type — even when you analyzed a single type, this section still renders (just with a single column of coverage).

## Three modes

The tool has three modes for detecting renames. Pick the right one based on your dataset and what you plan to do with the output.

**Default (taxi mode)** — Uses a built-in NYC-TLC-specific abbreviation dictionary (`amt→amount`, `pu→pickup`, `do→dropoff`, `tpep→` empty prefix, etc.) plus semantic categories (pickup vs dropoff, coordinate, location_id, amount, datetime) to detect renames by name similarity alone. Fast — no data sampling is performed. Best for TLC data specifically.

**`--generic`** — No domain knowledge. Detects renames purely by comparing column data between periods: null ratios, cardinality, min/max ranges, top values. Slower because it samples row data. Best for non-TLC datasets. The report explicitly marks every suggested rename as "requires human review".

**`--verify-data`** — Taxi mode plus a sampled data comparison to verify low-confidence rename candidates. Slower than default but faster than `--generic`. Best when you plan to trust the output programmatically — for example, feeding results into `normalize bootstrap` without hand-reviewing every suggestion.

Decision helper:

- TLC data + first look → default.
- Other dataset → `--generic`.
- TLC data + acting on the output → `--verify-data`.

Note: `--generic` already uses data verification, so combining it with `--verify-data` is redundant (the CLI prints a note and ignores the flag).

Runtime scales roughly with `(number of transitions) × (columns to verify) × (rows sampled)`. Default mode on a full TLC history finishes in seconds; `--verify-data` typically takes a minute or two; `--generic` on the same data can take several minutes because every added/removed pair triggers a data comparison.

## Reading the report

A trimmed example of the text output:

```text
================================================================================
SCHEMA DRIFT REPORT
================================================================================

────────────────────────────────────────────────────────────────────────────────
DATA TYPE: YELLOW
────────────────────────────────────────────────────────────────────────────────
Files analyzed: 214
Period range: 2009-01 to 2026-06
Total schema changes detected: 6

INITIAL SCHEMA (2009-01):
    vendor_name                VARCHAR
    Trip_Pickup_DateTime       TIMESTAMP
    Trip_Dropoff_DateTime      TIMESTAMP
    Passenger_Count            INTEGER
    Trip_Distance              DOUBLE
    Start_Lon                  DOUBLE
    Start_Lat                  DOUBLE
    ...

SCHEMA CHANGES:

  [2009-12] → [2010-01]
    ↔ Columns RENAMED:
        ↔ Trip_Pickup_DateTime → pickup_datetime (92% confidence) ✓ data verified
        ↔ Trip_Dropoff_DateTime → dropoff_datetime (92% confidence) ✓ data verified
        ↔ Start_Lon → pickup_longitude (78% confidence)
    + Columns ADDED:
        + rate_code (INTEGER)
    - Columns REMOVED:
        - vendor_name (VARCHAR)
    ~ Type CHANGED:
        ~ passenger_count: INTEGER → BIGINT

FINAL SCHEMA (2016-07): ...

================================================================================
CROSS-TYPE SUMMARY
================================================================================
Columns by data type coverage:
  pickup_datetime: fhv, fhvhv, green, yellow
  trip_distance: green, yellow
  ...
```

What each block means:

- **INITIAL SCHEMA** — the schema of the earliest file for this type. This is the baseline every later transition is measured against.
- **`[period_from] → [period_to]`** — a transition. Only files where the schema actually differs from the previous file appear here. If ten consecutive months share the same schema, you get one entry, not ten.
- **`↔ Columns RENAMED`** — the heuristic thinks these are the same logical column under a new name. Each line shows the confidence percentage; `✓ data verified` appears when `--verify-data` confirmed the match against actual row data (`✗ data mismatch` demotes the rename back into added/removed).
- **`+ ADDED` / `- REMOVED`** — genuinely new or gone. If a column got renamed, it appears in the rename block, not here.
- **`~ Type CHANGED`** — same column name, different DuckDB type. Watch for widening (INTEGER → BIGINT) versus type shifts that indicate a semantic change.
- **CROSS-TYPE SUMMARY** — for each column name seen anywhere, lists which data types have it. Useful for spotting which columns are shared across yellow/green/fhv/fhvhv versus which are type-specific.

## How rename detection works

### Semantic categories

Every column name is tagged with a set of semantic categories: `pickup`, `dropoff`, `coordinate`, `location_id`, `amount`, `datetime`. Columns whose categories are semantically opposite are never matched. A `pickup_datetime` will never be suggested as a rename of `dropoff_datetime` even if their data is statistically identical. This eliminates the most common false-positive class in the TLC data.

The category taxonomy lives in `similarity.py` alongside the abbreviation dictionary. Adding a new category (or a new opposite pair) is a small edit — useful if you're adapting the tool for a related domain.

### Name similarity

Two similarity signals combine into a name score: longest-common-subsequence similarity over the whole string, and token-based similarity after tokenization on `_`. Each token is lowercased and looked up in the abbreviation dictionary; unmapped tokens pass through unchanged.

Example: `pu_datetime` tokenizes to `[pu, datetime]` and expands to `[pickup, datetime]`. `tpep_pickup_datetime` tokenizes to `[tpep, pickup, datetime]` and expands to `[pickup, datetime]` (the `tpep` prefix maps to empty). Match: high name similarity.

### Data verification

For candidate pairs whose name similarity clears the threshold, the tool can compare column data across the transition: null ratios, cardinality, min/max ranges, top values. Agreement across dimensions boosts the confidence score; disagreement penalizes it or rejects the pair outright. In `--verify-data` mode, rejected pairs get demoted back into added/removed; in `--generic` mode, data comparison is the *only* signal.

### Confidence threshold

The default cutoff is 0.6. Anything below that shows in the report as an unresolved add/remove pair — the tool didn't have enough evidence to call it a rename, and a human needs to decide. Anything at or above 0.6 is emitted as a SUGGESTED rename with its confidence percentage and, when applicable, a `✓ data verified` / `✗ data mismatch` marker.

In practice, TLC renames tend to score in the 75–95% band. Anything lower is worth a manual look at the two candidate columns before you accept it as a rename in a `normalize` mapping.

## Programmatic API

For notebooks or pipeline code, call the analyzer directly:

```python
import duckdb
from pathlib import Path
from schema_drift.analyze import analyze_data_type

conn = duckdb.connect(":memory:")
result = analyze_data_type(
    conn=conn,
    data_dir=Path("raw"),
    data_type="yellow",
    verify_data=False,
    generic_mode=False,
    sample_size=5000,   # or 0 for full-scan verification
)

# result['changes'] is a list of SchemaChange objects at each transition
for change in result["changes"]:
    for r in change.columns_renamed:
        print(f"{change.period_from} → {change.period_to}: "
              f"{r.old_col.name} → {r.new_col.name} "
              f"(confidence {r.confidence:.0%})")
```

`analyze_data_type` returns a dict with `changes` (a list of `SchemaChange`), `schemas` (per-period schemas keyed by `YYYY-MM`), and metadata (`data_type`, `files_analyzed`, `generic_mode`). Each `SchemaChange` has `period_from`, `period_to`, `columns_added`, `columns_removed`, `columns_type_changed`, and `columns_renamed`. The `columns_renamed` list contains `ColumnRename` objects with `old_col`, `new_col`, `confidence` (0.0–1.0 float), and an optional `data_verified` boolean (`True`, `False`, or `None` if verification wasn't run).

To render the same text report you get from the CLI, pass the list of results to `generate_report`:

```python
from schema_drift.report import generate_report
print(generate_report([result]))
```

## Known limits

- **Heuristic, not proof.** Every SUGGESTED rename is a guess with a confidence score. Humans should review before accepting — especially for anything below 90%. The tool is designed to reduce the tedium of scanning 200+ files for schema changes, not to replace judgment about what a column represents.
- **Statistically-indistinguishable columns are hard.** When two columns share nearly-identical ranges and cardinalities (e.g., `pu_datetime` and `do_datetime` in some historical eras), data verification alone can't tell them apart. Semantic categories catch this for TLC data but not for arbitrary datasets in `--generic` mode. If you're running `--generic` on data with paired timestamps or paired coordinates, spot-check the suggestions.
- **Filename convention is required.** The tool assumes filenames encode the period as `<type>_tripdata_YYYY-MM.parquet`. Non-TLC datasets need to be renamed to match, or you need to adapt `extract_period` in `analyze.py`. The period is extracted as the final `_`-separated component of the filename stem.
- **Sampled verification can miss rare-value renames.** The default `sample_size=5000` catches the common case cheaply. Pass `sample_size=0` to the API for a full-scan verification when correctness matters more than speed — worth it if you're feeding results into a mapping that will be applied to years of historical data.
- **Only reports transitions, not every file.** If ten consecutive months share the same schema, the tool reports it once and moves on. This is a feature (readable output), but if you need a per-file inventory you'll need to iterate `find_parquet_files` yourself and call `get_parquet_schema` per file.

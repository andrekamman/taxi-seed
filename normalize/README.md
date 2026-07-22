# normalize

Rewrites historical TLC parquet files to conform to the latest schema, driven by a per-type curated YAML mapping. Data loss is a first-class error — the tool halts unless every discarded column or lossy cast has an explicit acknowledgment.

## Usage

```bash
# First time per data type: generate the mapping scaffold
uv run normalize bootstrap yellow

# Edit normalize/mappings/yellow.yaml — uncomment SUGGESTED renames, fill TODO ack_date fields

# Then run the normalizer
uv run normalize yellow

# Or run all four types
uv run normalize
```

Requires the parquet mirror produced by the [downloader](../downloader/README.md) at `raw/<type>/<year>/*.parquet`. Writes to `raw-normalized/<type>/<year>/*.parquet`.

## Mapping file

Every data type has a mapping at `normalize/mappings/<type>.yaml`. `ack_date` is the only required field for `lossy_casts:` and `acknowledged_data_loss:` entries; `ack_by` and `reason` are optional but recommended for git-history documentation.

```yaml
target: yellow_tripdata_2024-01.parquet
renames:
  pu_datetime: tpep_pickup_datetime
lossy_casts:
  passenger_count:
    from: DOUBLE
    to: BIGINT
    ack_date: 2026-07-21
acknowledged_data_loss:
  pickup_latitude:
    ack_date: 2026-07-21
```

## What runs automatically (no mapping needed)

- Columns always-null in historical data, missing from target → dropped
- Columns missing from historical data, present in target → filled with NULL
- Type widenings that cannot lose data (INT → BIGINT, VARCHAR(10) → VARCHAR(50), etc.)

## What triggers an error

- Historical column has non-null data, is missing from target, and has no `renames:` or `acknowledged_data_loss:` entry
- Type cast could lose data (range or precision) and there's no `lossy_casts:` entry with an `ack_date`

The error is a single consolidated report per data type — nothing is written if any item is unresolved.

## Options

- `--sample <N|N%>` on `bootstrap`: rows or percent sampled during schema-drift rename verification. Default `100%` (full scan). Reduce only for very large datasets; metadata-only checks and precision scans always full-scan regardless.

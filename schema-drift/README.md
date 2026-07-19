# schema-drift

Detects and reports schema changes across NYC TLC parquet files over time. Uses DuckDB to introspect parquet files, compares schemas at every transition point, and identifies added / removed / type-changed / renamed columns.

## Usage

```bash
# Analyze the default `raw/` directory across all four data types
uv run schema-drift

# Focus on specific types
uv run schema-drift --types yellow green

# Write the report to a file
uv run schema-drift --output drift-report.txt

# Use data-driven rename detection (no domain knowledge)
uv run schema-drift --generic

# Verify name-based renames with actual data (slower but more accurate)
uv run schema-drift --verify-data
```

Requires the parquet mirror produced by the [downloader](../downloader/README.md) at `raw/<type>/<year>/*.parquet`.

## Modes

- **Default (taxi mode)** — Uses NYC-TLC-specific abbreviations and semantic categories (pickup/dropoff, coordinates, location IDs, amounts, datetimes) to detect renames by name similarity.
- **`--generic`** — Ignores domain knowledge; detects renames purely by comparing column data (null ratios, cardinality, value ranges, top values). Slower; results require human review.
- **`--verify-data`** — In taxi mode, samples actual data to verify low-confidence rename candidates.

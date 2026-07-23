# Multi-source Renames + value_map Policies Design

**Date:** 2026-07-23
**Status:** Approved, ready for implementation planning
**Follow-up to:** the orchestrator sub-project (see `2026-07-23-orchestrator-design.md`), which introduced
`value_maps` and left several yellow 2009-2010 columns dropped that are actually recoverable.

## Motivation

Auto-curation drops yellow columns that a human can see are renames/value-maps, because two
normalizer limitations block them:

1. **Single-source renames only.** The planner inverts `renames` to one `{target: source}` dict, so
   two historical names for the same concept (e.g. `Trip_Pickup_DateTime` in 2009 and
   `pickup_datetime` in 2010, both → `tpep_pickup_datetime`) collide — one silently wins, the other
   is dropped as data-loss.
2. **value_map can't express NULL or "discard the rest."** Its values can't be `null`, and every
   unmapped value raises — so a vendor code we deliberately want to null (`DDS`), or a messy column
   where only some codes are valid (`rate_code`), can't be modeled.

This adds the two small features and completes yellow's mapping so only genuinely-removed columns
(the old lat/long TLC dropped) remain data-loss.

## Feature 1 — Multi-source rename

- `renames:` may contain **multiple `old: new` entries that share the same `new`** (target).
- **Planner:** for each target column reached by a rename, pick the source that is **present in the
  current raw file**. Build target → *list* of sources (not a single inverse). Per era only one
  source exists, so this is unambiguous in practice.
- **Ambiguity rule:** if, for one file, **more than one** source for a target is present **and** the
  target column itself is absent, raise a clear error (fail-loud; do not silently pick one). If the
  target column is present directly, it wins (Case A passthrough/cast) and the rename sources for it
  are ignored for that file.
- Behavior is unchanged for the existing single-source case.

## Feature 2 — value_map NULL values + unmapped policy

The `value_maps` YAML entry for a column may take either form:

- **Strict (unchanged default):** a flat `{source_value: target_value}` dict. Unmapped non-null
  values **raise** (current behavior). `target_value` may now be `null` (explicit → SQL NULL).
- **Policy form:** `{map: {source_value: target_value}, on_unmapped: null | error}`.
  `on_unmapped: null` converts any unmapped non-null value to NULL; `error` (the default) raises.

Semantics in the executor's `value_map` action:
- source `NULL` → `NULL` (always).
- mapped value → its target value (including explicit `null`).
- unmapped non-null → **raise** (strict / `on_unmapped: error`) or **`NULL`** (`on_unmapped: null`).

`load_mapping` parses both forms into `Mapping.value_maps` (the `{value: target}` dict) plus
`Mapping.value_map_unmapped` (`{column: "error" | "null"}`, default `"error"`). The planner threads
the policy onto the `value_map` `ColumnAction`; the executor renders the `ELSE` branch accordingly
(`error(...)` or `NULL`). SQL literals (including `NULL`) are rendered by `_sql_lit`.

## Yellow mapping completion (hand-authored)

Added to `normalize/mappings/yellow.yaml` (curate preserves hand-authored renames/value_maps):

- **Multi-source renames:**
  - `Trip_Pickup_DateTime → tpep_pickup_datetime`, `pickup_datetime → tpep_pickup_datetime`
  - `Trip_Dropoff_DateTime → tpep_dropoff_datetime`, `dropoff_datetime → tpep_dropoff_datetime`
  - `vendor_name → VendorID`, `vendor_id → VendorID`
  - (datetimes are VARCHAR→TIMESTAMP; the planner already treats that cast as safe.)
- **value_maps:**
  - `VendorID: {CMT: 1, VTS: 2, DDS: null}` (strict — a genuinely-new vendor still errors)
  - `store_and_fwd_flag: {"0.0": N, "1.0": Y}` (from DOUBLE `store_and_forward`; exact source-string
    form of the DOUBLE confirmed at implementation time)
  - `RatecodeID: {map: {"1":1, …, "6":6}, on_unmapped: null}` (valid codes kept, corrupt 2010 values
    → NULL) — applied to `rate_code → RatecodeID`.

`store_and_forward → store_and_fwd_flag` and `rate_code → RatecodeID` are single-source renames.

## Non-goals

- No general "fuzzy" or auto-discovered multi-source renames — multi-source entries are authored by
  a human in the mapping YAML; `taxi-curate-mappings` does not invent them.
- No change to `taxi-curate-mappings`' auto-acceptance beyond preserving these hand-authored entries
  (which it already does via `_mapping_to_dict`).
- Other data types (green/fhv/fhvhv) are unaffected — their mappings already normalize cleanly.

## Testing strategy

- **Planner (`tests/taxi_normalize`):** multi-source rename picks the present source per file; a file
  with two present sources (target absent) raises; direct target presence wins over rename sources.
- **Executor/mapping:** value_map value `null` → NULL; `on_unmapped: null` nulls an unmapped value
  while `error` (default) raises; both YAML forms parse.
- **End-to-end:** re-normalize yellow over the real `raw/yellow/` → exit 0, and **spot-check
  `raw-normalized/yellow/` for 2009 and 2010**: `tpep_pickup_datetime`/`tpep_dropoff_datetime` and
  `VendorID` are populated (not all-NULL), `store_and_fwd_flag` is `Y`/`N`, `RatecodeID` has valid
  codes with corrupt-source rows NULL.
- Full repo suite stays green (behavior unchanged for existing single-source / strict cases).

## Implementation sequence (for the plan)

1. Planner multi-source rename (target → list-of-sources; present-source selection; ambiguity error) + tests.
2. Mapping parse of both value_map forms (`value_maps` + `value_map_unmapped`; `null` values) + `_sql_lit(None)` → `NULL` + executor `ELSE` policy + tests.
3. Author yellow mapping additions; re-run `taxi-curate-mappings yellow` (preserves them) and re-normalize; spot-check the 2009-2010 columns; commit the mapping + refreshed `CURATION-REPORT.md`.

## Success criteria

- Two source names for one target both normalize (per era) instead of one being dropped.
- `value_maps` support `null` targets and an `on_unmapped: null` policy; strict mode still raises on surprises.
- Yellow re-normalizes to exit 0 with 2009-2010 pickup/dropoff datetimes, `VendorID` (CMT→1, VTS→2,
  DDS→NULL), `store_and_fwd_flag` (Y/N), and valid `RatecodeID` populated; only true removals (old
  lat/long, `__index_level_0__`) remain in `acknowledged_data_loss`.
- `uv run --extra test pytest` stays green.

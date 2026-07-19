from collections import defaultdict

from schema_drift.models import ColumnInfo


def format_schema_table(schema: list[ColumnInfo]) -> str:
    """Format a schema as a table."""
    if not schema:
        return "  (empty schema)"

    max_name_len = max(len(col.name) for col in schema)
    lines = []
    for col in schema:
        lines.append(f"    {col.name:<{max_name_len}}  {col.dtype}")
    return "\n".join(lines)


def generate_report(results: list[dict], output_format: str = "text") -> str:
    """Generate a comprehensive schema drift report."""
    lines = []

    # Check if any result used generic mode
    generic_mode = any(result.get("generic_mode", False) for result in results)

    lines.append("=" * 80)
    if generic_mode:
        lines.append("SCHEMA DRIFT REPORT (GENERIC MODE)")
        lines.append("=" * 80)
        lines.append("NOTE: Renames detected by data similarity - REQUIRES HUMAN REVIEW")
    else:
        lines.append("SCHEMA DRIFT REPORT")
        lines.append("=" * 80)
    lines.append("")

    for result in results:
        data_type = result["data_type"]
        files_analyzed = result["files_analyzed"]
        schemas = result["schemas"]
        changes = result["changes"]
        is_generic = result.get("generic_mode", False)

        lines.append(f"{'─' * 80}")
        lines.append(f"DATA TYPE: {data_type.upper()}")
        lines.append(f"{'─' * 80}")
        lines.append(f"Files analyzed: {files_analyzed}")
        if is_generic:
            lines.append("Mode: Generic (data-driven rename detection)")

        if not schemas:
            lines.append("No data files found.")
            lines.append("")
            continue

        # Show period range
        periods = sorted(schemas.keys())
        lines.append(f"Period range: {periods[0]} to {periods[-1]}")
        lines.append(f"Total schema changes detected: {len(changes)}")
        lines.append("")

        # Show initial schema
        first_period = periods[0]
        lines.append(f"INITIAL SCHEMA ({first_period}):")
        lines.append(format_schema_table(schemas[first_period]))
        lines.append("")

        # Show each change
        if changes:
            lines.append("SCHEMA CHANGES:")
            lines.append("")

            for change in changes:
                lines.append(f"  [{change.period_from}] → [{change.period_to}]")

                if change.columns_renamed:
                    if is_generic:
                        lines.append("    ? SUGGESTED RENAMES (requires human review):")
                    else:
                        lines.append("    ↔ Columns RENAMED:")
                    for rename in sorted(change.columns_renamed, key=lambda r: r.confidence, reverse=True):
                        type_note = ""
                        if rename.old_col.dtype != rename.new_col.dtype:
                            type_note = f" [type: {rename.old_col.dtype} → {rename.new_col.dtype}]"
                        confidence_pct = int(rename.confidence * 100)

                        # Add verification status if data was verified
                        verify_note = ""
                        if is_generic:
                            verify_note = " ← REVIEW"
                        elif rename.data_verified is True:
                            verify_note = " ✓ data verified"
                        elif rename.data_verified is False:
                            verify_note = " ✗ data mismatch"

                        lines.append(f"        ↔ {rename.old_col.name} → {rename.new_col.name} ({confidence_pct}% confidence){type_note}{verify_note}")

                        # Show verification details if available
                        if rename.verification_details and rename.data_verified is not None:
                            lines.append(f"            └─ {rename.verification_details}")

                if change.columns_added:
                    lines.append("    + Columns ADDED:")
                    for col in change.columns_added:
                        lines.append(f"        + {col.name} ({col.dtype})")

                if change.columns_removed:
                    lines.append("    - Columns REMOVED:")
                    for col in change.columns_removed:
                        lines.append(f"        - {col.name} ({col.dtype})")

                if change.columns_type_changed:
                    lines.append("    ~ Type CHANGED:")
                    for old_col, new_col in change.columns_type_changed:
                        lines.append(f"        ~ {old_col.name}: {old_col.dtype} → {new_col.dtype}")

                lines.append("")
        else:
            lines.append("No schema changes detected across all periods.")
            lines.append("")

        # Show final schema
        last_period = periods[-1]
        lines.append(f"FINAL SCHEMA ({last_period}):")
        lines.append(format_schema_table(schemas[last_period]))
        lines.append("")

        # Summary of unique schemas
        unique_schemas = []
        seen = set()
        for period in periods:
            schema_tuple = tuple((c.name, c.dtype) for c in schemas[period])
            if schema_tuple not in seen:
                seen.add(schema_tuple)
                unique_schemas.append(period)

        lines.append(f"Unique schema versions: {len(unique_schemas)}")
        if len(unique_schemas) > 1:
            lines.append(f"Schema version periods: {', '.join(unique_schemas)}")
        lines.append("")

    # Cross-type comparison
    lines.append("=" * 80)
    lines.append("CROSS-TYPE SUMMARY")
    lines.append("=" * 80)
    lines.append("")

    # Collect all unique column names across all data types
    all_columns = defaultdict(set)
    for result in results:
        for period, schema in result["schemas"].items():
            for col in schema:
                all_columns[col.name].add(result["data_type"])

    lines.append("Columns by data type coverage:")
    for col_name in sorted(all_columns.keys()):
        types = sorted(all_columns[col_name])
        lines.append(f"  {col_name}: {', '.join(types)}")

    return "\n".join(lines)

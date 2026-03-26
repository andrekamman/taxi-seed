"""CLI entry point for the K6 load test preprocessor."""

import argparse
import json
import sys
from pathlib import Path

from loadtest.config import load_config, validate_config
from loadtest.data_export import export_chunks, get_schema
from loadtest.type_mapping import map_duckdb_to_mssql
from loadtest.sql_generator import (
    generate_insert_sql,
    generate_update_sql,
    generate_delete_sql,
    generate_create_table_sql,
)
from loadtest.k6_generator import generate_manifest, generate_test_js


def run_preprocess(config_path: Path, output_dir: Path) -> None:
    """Run the full preprocessing pipeline."""
    config = load_config(config_path)
    validate_config(config)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_sources = config["data_sources"]
    targets = config["targets"]
    scenarios = config["scenarios"]

    ds_chunk_counts = {}
    ds_schemas = {}
    ds_modes = {}

    # Type mapping from synthetic config types to SQL Server types
    SYNTHETIC_TYPE_MAP = {
        "datetime": "DATETIME2",
        "int": "INT",
        "bigint": "BIGINT",
        "float": "FLOAT",
        "string": "NVARCHAR(MAX)",
        "bool": "BIT",
        "date": "DATE",
    }

    # Step 1: Export data sources
    for ds_name, ds_config in data_sources.items():
        mode = ds_config.get("mode", "parquet")
        ds_modes[ds_name] = mode

        if mode == "synthetic":
            print(f"Synthetic data source: {ds_name}")
            columns = ds_config["columns"]
            mapped_schema = {}
            for col_name, col_def in columns.items():
                syn_type = col_def["type"]
                if syn_type not in SYNTHETIC_TYPE_MAP:
                    raise ValueError(
                        f"Data source {ds_name!r}: unknown synthetic type {syn_type!r} "
                        f"for column {col_name!r}"
                    )
                mapped_schema[col_name] = SYNTHETIC_TYPE_MAP[syn_type]
            ds_schemas[ds_name] = mapped_schema
            ds_chunk_counts[ds_name] = 0
            print(f"  Columns: {', '.join(columns.keys())}")
        else:
            print(f"Exporting data source: {ds_name}")
            columns = ds_config["columns"]
            chunk_size = ds_config.get("chunk_size", 5000)
            max_rows = ds_config.get("max_rows")

            num_chunks = export_chunks(
                parquet_glob=ds_config["path"],
                columns=columns,
                chunk_size=chunk_size,
                output_dir=output_dir / "data" / ds_name,
                max_rows=max_rows,
            )
            ds_chunk_counts[ds_name] = num_chunks
            print(f"  Exported {num_chunks} chunks to data/{ds_name}/")

            # Write chunk index file
            chunk_files = [
                f"./data/{ds_name}/chunk_{i:04d}.json" for i in range(num_chunks)
            ]
            with open(output_dir / "data" / ds_name / "chunks.json", "w") as f:
                json.dump(chunk_files, f)

            # Get schema and map types
            parquet_schema = get_schema(ds_config["path"])
            mapped_schema = {}
            for mapped_name, source_name in columns.items():
                duckdb_type = parquet_schema[source_name]
                mapped_schema[mapped_name] = map_duckdb_to_mssql(duckdb_type)
            ds_schemas[ds_name] = mapped_schema

    # Step 2: Generate SQL and schema files per target
    schema_dir = output_dir / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)

    target_ds_map = {}
    for scenario in scenarios.values():
        target_name = scenario["target"]
        ds_name = scenario["data_source"]
        target_ds_map[target_name] = ds_name

    for target_name, ds_name in target_ds_map.items():
        target = targets[target_name]
        table = target["table"]
        schema = ds_schemas[ds_name]

        create_sql = generate_create_table_sql(table, schema)
        schema_file = schema_dir / f"{target_name}_{table}.sql"
        schema_file.write_text(create_sql)
        print(f"  Schema: {schema_file.name}")

    # Step 3: Generate scenario manifests
    scenarios_dir = output_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    for scenario_name, scenario_config in scenarios.items():
        target = targets[scenario_config["target"]]
        ds_name = scenario_config["data_source"]
        ds_config = data_sources[ds_name]
        schema = ds_schemas[ds_name]
        key_columns = ds_config["key_columns"]

        sql_templates = {
            "insert": generate_insert_sql(target["table"], schema),
            "update": generate_update_sql(target["table"], schema, key_columns),
            "delete": generate_delete_sql(target["table"], key_columns),
        }

        mode = ds_modes[ds_name]

        manifest = generate_manifest(
            scenario_name=scenario_name,
            target=target,
            data_source_name=ds_name,
            num_chunks=ds_chunk_counts[ds_name],
            ordering=scenario_config.get("ordering", "parallel"),
            workload=scenario_config["workload"],
            think_time=scenario_config["think_time"],
            sql_templates=sql_templates,
            column_order=list(ds_config["columns"].keys()),
            key_columns=key_columns,
            mode=mode,
            synthetic_columns=ds_config["columns"] if mode == "synthetic" else None,
        )

        manifest_path = scenarios_dir / f"{scenario_name}.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"  Manifest: {scenario_name}.json")

    # Step 4: Generate K6 test script
    test_js = generate_test_js(scenarios, data_sources)
    (output_dir / "test.js").write_text(test_js)
    print(f"  Generated test.js")

    print(f"\nDone! Output written to: {output_dir}")
    print(f"Run with: ./k6 run {output_dir}/test.js")


def main():
    parser = argparse.ArgumentParser(
        description="Preprocess parquet data for K6 SQL Server load testing"
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("k6_output"),
        help="Output directory (default: k6_output/)",
    )
    args = parser.parse_args()

    try:
        run_preprocess(args.config, args.output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

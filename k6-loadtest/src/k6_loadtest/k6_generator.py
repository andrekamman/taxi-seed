"""Generate K6 scenario manifests and test.js script."""

import json
import re
import sys


def generate_manifest(
    scenario_name: str,
    target: dict,
    data_source_name: str,
    num_chunks: int,
    ordering: str,
    workload: dict,
    think_time: dict,
    sql_templates: dict,
    column_order: list[str],
    key_columns: list[str],
    mode: str = "parquet",
    synthetic_columns: dict | None = None,
) -> dict:
    """Generate a scenario manifest dict.

    The connection string preserves ${VAR} placeholders for K6 runtime resolution.
    """
    password = target["password"]
    connection_string = (
        f"Server={target['host']},{target['port']};"
        f"Database={target['database']};"
        f"User Id={target['username']};"
        f"Password={password};"
        f"TrustServerCertificate=true"
    )

    result = {
        "scenario_name": scenario_name,
        "table": target["table"],
        "connection_string": connection_string,
        "data_source": data_source_name,
        "num_chunks": num_chunks,
        "ordering": ordering,
        "workload": workload,
        "think_time": think_time,
        "sql": sql_templates,
        "column_order": column_order,
        "key_columns": key_columns,
        "mode": mode,
    }
    if synthetic_columns:
        result["synthetic_columns"] = synthetic_columns
    return result


def _to_camel_case(snake_str: str) -> str:
    """Convert snake_case to camelCase."""
    parts = snake_str.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def generate_test_js(scenarios_config: dict, data_sources: dict | None = None) -> str:
    """Generate the K6 test.js script content."""
    if data_sources is None:
        data_sources = {}
    # Build K6 scenarios options object
    k6_scenarios = {}
    for name, scenario in scenarios_config.items():
        func_name = _to_camel_case(name)
        k6_config = dict(scenario["k6"])

        ds_name = scenario.get("data_source", "")
        ds_mode = data_sources.get(ds_name, {}).get("mode", "parquet")

        if scenario.get("ordering") == "sequential" and ds_mode == "parquet":
            if k6_config.get("vus", 1) > 1 or k6_config.get("startVUs", 1) > 1:
                print(
                    f"Warning: scenario {name!r} uses sequential ordering — "
                    f"overriding to 1 VU",
                    file=sys.stderr,
                )
            # iterations=1 is correct: the single iteration loops through
            # all chunks sequentially inside processChunk()
            k6_config = {
                "executor": "per-vu-iterations",
                "vus": 1,
                "iterations": 1,
            }

        k6_config["exec"] = func_name
        k6_scenarios[name] = k6_config

    scenarios_json = json.dumps(k6_scenarios, indent=4)

    # Build executor functions
    functions = []
    for name, scenario in scenarios_config.items():
        func_name = _to_camel_case(name)
        functions.append(f"""
export function {func_name}() {{
    const manifest = manifests['{name}'];
    processIteration(manifest, '{name}');
}}""")

    functions_str = "\n".join(functions)

    # Determine which data sources are synthetic vs parquet
    has_parquet = any(
        data_sources.get(s["data_source"], {}).get("mode", "parquet") == "parquet"
        for s in scenarios_config.values()
    )

    return f"""import sql from 'k6/x/sql';
import driver from 'k6/x/sql/driver/sqlserver';
{"import { SharedArray } from 'k6/data';" if has_parquet else ""}
import exec from 'k6/execution';
import {{ sleep }} from 'k6';

// Load scenario manifests (init stage — open() is allowed here)
const manifests = {{}};
{_generate_manifest_loaders(scenarios_config)}

// Data loading per source
const chunkData = {{}};
const chunkMeta = {{}};
{_generate_data_loaders(scenarios_config, data_sources)}

export const options = {{
    scenarios: {scenarios_json},
}};

// Per-VU connection cache
const connections = {{}};

function getConnection(manifest) {{
    const key = manifest.scenario_name;
    if (!connections[key]) {{
        // Resolve ${{VAR}} env var placeholders in connection string
        let connStr = manifest.connection_string;
        const envPattern = /\\$\\{{([^}}]+)\\}}/g;
        let match;
        while ((match = envPattern.exec(connStr)) !== null) {{
            const envVal = __ENV[match[1]];
            if (envVal === undefined) {{
                throw new Error(`Environment variable ${{match[1]}} not set`);
            }}
            connStr = connStr.replace(match[0], envVal);
        }}
        connections[key] = sql.open(driver, connStr);
    }}
    return connections[key];
}}

export function teardown() {{
    for (const [key, db] of Object.entries(connections)) {{
        db.close();
    }}
}}

function weightedRandom(workload) {{
    const rand = Math.random() * 100;
    let cumulative = 0;
    for (const [op, pct] of Object.entries(workload)) {{
        cumulative += pct;
        if (rand < cumulative) return op;
    }}
    return 'insert'; // fallback
}}

function randomBetween(minStr, maxStr) {{
    function parseMs(s) {{
        if (s.endsWith('ms')) return parseFloat(s);
        if (s.endsWith('s')) return parseFloat(s) * 1000;
        return parseFloat(s);
    }}
    const min = parseMs(minStr);
    const max = parseMs(maxStr);
    return (min + Math.random() * (max - min)) / 1000; // K6 sleep uses seconds
}}

// Synthetic row generators per data source
{_generate_synthetic_generators(scenarios_config, data_sources)}

function getChunkRows(dataSource, chunkIdx) {{
    const meta = chunkMeta[dataSource];
    const data = chunkData[dataSource];
    const start = chunkIdx * meta.chunkSize;
    const end = Math.min(start + meta.chunkSize, data.length);
    const rows = [];
    for (let i = start; i < end; i++) {{
        rows.push(data[i]);
    }}
    return rows;
}}

function processIteration(manifest, scenarioName) {{
    const db = getConnection(manifest);

    if (manifest.mode === 'synthetic') {{
        // Synthetic mode: generate one row per iteration, run forever
        const row = syntheticGenerators[manifest.data_source]();
        processSingleRow(db, manifest, row);
        return;
    }}

    // Parquet mode: process chunks
    const meta = chunkMeta[manifest.data_source];
    const numChunks = meta.numChunks;

    if (manifest.ordering === 'sequential') {{
        for (let i = 0; i < numChunks; i++) {{
            const rows = getChunkRows(manifest.data_source, i);
            processRows(db, manifest, rows);
        }}
        return;
    }}

    const chunkIdx = exec.scenario.iterationInTest;
    if (chunkIdx >= numChunks) return;

    const rows = getChunkRows(manifest.data_source, chunkIdx);
    processRows(db, manifest, rows);
}}

// Process a single synthetic row with workload selection
const syntheticProcessed = {{}};
function processSingleRow(db, manifest, row) {{
    const key = manifest.scenario_name;
    if (!syntheticProcessed[key]) syntheticProcessed[key] = [];
    const processed = syntheticProcessed[key];

    const op = weightedRandom(manifest.workload);
    const values = manifest.column_order.map(c => row[c]);

    if (op === 'insert' || processed.length === 0) {{
        db.exec(manifest.sql.insert, ...values);
    }} else if (op === 'update') {{
        const target = processed[Math.floor(Math.random() * processed.length)];
        const nonKeyValues = manifest.column_order
            .filter(c => !manifest.key_columns.includes(c))
            .map(c => row[c]);
        const targetKeyValues = manifest.key_columns.map(c => target[c]);
        db.exec(manifest.sql.update, ...nonKeyValues, ...targetKeyValues);
    }} else if (op === 'delete') {{
        const target = processed[Math.floor(Math.random() * processed.length)];
        const targetKeyValues = manifest.key_columns.map(c => target[c]);
        db.exec(manifest.sql.delete, ...targetKeyValues);
    }}

    // Keep a bounded window of recent rows for update/delete targeting
    processed.push(row);
    if (processed.length > 1000) processed.shift();

    sleep(randomBetween(manifest.think_time.min, manifest.think_time.max));
}}

function processRows(db, manifest, rows) {{
    const processed = [];

    for (const row of rows) {{
        const op = weightedRandom(manifest.workload);
        const values = manifest.column_order.map(c => row[c]);

        if (op === 'insert' || processed.length === 0) {{
            db.exec(manifest.sql.insert, ...values);
        }} else if (op === 'update') {{
            const target = processed[Math.floor(Math.random() * processed.length)];
            const nonKeyValues = manifest.column_order
                .filter(c => !manifest.key_columns.includes(c))
                .map(c => row[c]);
            const targetKeyValues = manifest.key_columns.map(c => target[c]);
            db.exec(manifest.sql.update, ...nonKeyValues, ...targetKeyValues);
        }} else if (op === 'delete') {{
            const target = processed[Math.floor(Math.random() * processed.length)];
            const targetKeyValues = manifest.key_columns.map(c => target[c]);
            db.exec(manifest.sql.delete, ...targetKeyValues);
        }}

        processed.push(row);
        sleep(randomBetween(manifest.think_time.min, manifest.think_time.max));
    }}
}}
{functions_str}
"""


def _generate_manifest_loaders(scenarios_config: dict) -> str:
    """Generate JS code to load manifest files."""
    lines = []
    for name in scenarios_config:
        lines.append(
            f"manifests['{name}'] = JSON.parse(open('./scenarios/{name}.json'));"
        )
    return "\n".join(lines)


def _generate_data_loaders(scenarios_config: dict, data_sources_config: dict) -> str:
    """Generate JS code to load data — SharedArrays for parquet, nothing for synthetic."""
    ds_names = set()
    for scenario in scenarios_config.values():
        ds_names.add(scenario["data_source"])

    lines = []
    for ds in sorted(ds_names):
        ds_config = data_sources_config.get(ds, {})
        mode = ds_config.get("mode", "parquet")

        if mode == "synthetic":
            lines.append(f"// {ds}: synthetic mode — rows generated at runtime")
        else:
            lines.append(
                f"const {_safe_var(ds)}Files = JSON.parse(open('./data/{ds}/chunks.json'));"
            )
            lines.append(
                f"chunkData['{ds}'] = new SharedArray('{ds}_data', function() {{"
            )
            lines.append(f"    let allRows = [];")
            lines.append(f"    for (const f of {_safe_var(ds)}Files) {{")
            lines.append(f"        const chunk = JSON.parse(open(f));")
            lines.append(f"        allRows = allRows.concat(chunk);")
            lines.append(f"    }}")
            lines.append(f"    return allRows;")
            lines.append(f"}});")
            lines.append(
                f"chunkMeta['{ds}'] = {{ "
                f"numChunks: {_safe_var(ds)}Files.length, "
                f"chunkSize: chunkData['{ds}'].length > 0 && {_safe_var(ds)}Files.length > 0 "
                f"? Math.ceil(chunkData['{ds}'].length / {_safe_var(ds)}Files.length) : 0 "
                f"}};"
            )
    return "\n".join(lines)


def _generate_synthetic_generators(scenarios_config: dict, data_sources_config: dict) -> str:
    """Generate JS functions that produce random rows for synthetic data sources."""
    ds_names = set()
    for scenario in scenarios_config.values():
        ds_names.add(scenario["data_source"])

    synthetic_sources = {}
    for ds in sorted(ds_names):
        ds_config = data_sources_config.get(ds, {})
        if ds_config.get("mode") == "synthetic":
            synthetic_sources[ds] = ds_config["columns"]

    if not synthetic_sources:
        return "const syntheticGenerators = {};"

    lines = ["const syntheticGenerators = {};"]

    for ds, columns in synthetic_sources.items():
        func_lines = [f"syntheticGenerators['{ds}'] = function() {{"]
        func_lines.append("    return {")

        for col_name, col_def in columns.items():
            col_type = col_def["type"]
            if col_type == "datetime":
                min_val = col_def.get("min", "2026-01-01")
                max_val = col_def.get("max", "2026-12-31")
                func_lines.append(
                    f"        {col_name}: new Date({_date_to_ms(min_val)} + "
                    f"Math.random() * ({_date_to_ms(max_val)} - {_date_to_ms(min_val)}))"
                    f".toISOString().slice(0, 19).replace('T', ' '),"
                )
            elif col_type == "date":
                min_val = col_def.get("min", "2026-01-01")
                max_val = col_def.get("max", "2026-12-31")
                func_lines.append(
                    f"        {col_name}: new Date({_date_to_ms(min_val)} + "
                    f"Math.random() * ({_date_to_ms(max_val)} - {_date_to_ms(min_val)}))"
                    f".toISOString().slice(0, 10),"
                )
            elif col_type in ("int", "bigint"):
                min_val = col_def.get("min", 0)
                max_val = col_def.get("max", 100)
                func_lines.append(
                    f"        {col_name}: Math.floor(Math.random() * "
                    f"({max_val} - {min_val} + 1)) + {min_val},"
                )
            elif col_type == "float":
                min_val = col_def.get("min", 0.0)
                max_val = col_def.get("max", 100.0)
                func_lines.append(
                    f"        {col_name}: Math.round((Math.random() * "
                    f"({max_val} - {min_val}) + {min_val}) * 100) / 100,"
                )
            elif col_type == "string":
                length = col_def.get("length", 10)
                func_lines.append(
                    f"        {col_name}: Math.random().toString(36).substring(2, {length + 2}),"
                )
            elif col_type == "bool":
                func_lines.append(
                    f"        {col_name}: Math.random() > 0.5,"
                )

        func_lines.append("    };")
        func_lines.append("};")
        lines.extend(func_lines)

    return "\n".join(lines)


def _date_to_ms(date_str: str) -> int:
    """Convert a date string to milliseconds since epoch for JS Date constructor."""
    from datetime import datetime
    dt = datetime.fromisoformat(date_str)
    return int(dt.timestamp() * 1000)


def _safe_var(name: str) -> str:
    """Convert a data source name to a safe JS variable name."""
    return name.replace("-", "_").replace(".", "_")

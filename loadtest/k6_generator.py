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
) -> dict:
    """Generate a scenario manifest dict.

    The connection string preserves ${VAR} placeholders for K6 runtime resolution.
    """
    password = target["password"]
    connection_string = (
        f"server={target['host']},{target['port']};"
        f"database={target['database']};"
        f"user id={target['username']};"
        f"password={password};"
        f"TrustServerCertificate=true"
    )

    return {
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
    }


def _to_camel_case(snake_str: str) -> str:
    """Convert snake_case to camelCase."""
    parts = snake_str.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def generate_test_js(scenarios_config: dict) -> str:
    """Generate the K6 test.js script content."""
    # Build K6 scenarios options object
    k6_scenarios = {}
    for name, scenario in scenarios_config.items():
        func_name = _to_camel_case(name)
        k6_config = dict(scenario["k6"])

        if scenario.get("ordering") == "sequential":
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
    processChunk(manifest, '{name}');
}}""")

    functions_str = "\n".join(functions)

    return f"""import sql from 'k6/x/sql';
import {{ SharedArray }} from 'k6/data';
import exec from 'k6/execution';
import {{ sleep }} from 'k6';

// Load scenario manifests
const manifests = {{}};
{_generate_manifest_loaders(scenarios_config)}

// Load chunk file lists per data source into SharedArray (shared across VUs)
const chunkLists = {{}};
{_generate_chunk_list_loaders(scenarios_config)}

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
        connections[key] = sql.open('sqlserver', connStr);
    }}
    return connections[key];
}}

export function teardown() {{
    // Close all cached database connections
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
    // Parse duration strings like "200ms", "1s"
    function parseMs(s) {{
        if (s.endsWith('ms')) return parseFloat(s);
        if (s.endsWith('s')) return parseFloat(s) * 1000;
        return parseFloat(s);
    }}
    const min = parseMs(minStr);
    const max = parseMs(maxStr);
    return (min + Math.random() * (max - min)) / 1000; // K6 sleep uses seconds
}}

function processChunk(manifest, scenarioName) {{
    const chunkFiles = chunkLists[manifest.data_source];
    let chunkIdx;

    if (manifest.ordering === 'sequential') {{
        // Sequential: process all chunks in order in a single iteration
        const db = getConnection(manifest);
        for (let i = 0; i < chunkFiles.length; i++) {{
            const rows = JSON.parse(open(chunkFiles[i]));
            processRows(db, manifest, rows);
        }}
        return;
    }}

    // Parallel: each iteration gets one chunk
    chunkIdx = exec.scenario.iterationInTest;
    if (chunkIdx >= chunkFiles.length) return; // no more chunks

    const rows = JSON.parse(open(chunkFiles[chunkIdx]));
    const db = getConnection(manifest);
    processRows(db, manifest, rows);
}}

function processRows(db, manifest, rows) {{
    const processed = [];

    for (const row of rows) {{
        const op = weightedRandom(manifest.workload);
        const values = manifest.column_order.map(c => row[c]);
        const keyValues = manifest.key_columns.map(c => row[c]);

        if (op === 'insert' || processed.length === 0) {{
            sql.query(db, manifest.sql.insert, ...values);
        }} else if (op === 'update') {{
            const target = processed[Math.floor(Math.random() * processed.length)];
            const nonKeyValues = manifest.column_order
                .filter(c => !manifest.key_columns.includes(c))
                .map(c => row[c]);
            const targetKeyValues = manifest.key_columns.map(c => target[c]);
            sql.query(db, manifest.sql.update, ...nonKeyValues, ...targetKeyValues);
        }} else if (op === 'delete') {{
            const target = processed[Math.floor(Math.random() * processed.length)];
            const targetKeyValues = manifest.key_columns.map(c => target[c]);
            sql.query(db, manifest.sql.delete, ...targetKeyValues);
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


def _generate_chunk_list_loaders(scenarios_config: dict) -> str:
    """Generate JS code to build chunk file path arrays per data source."""
    data_sources = set()
    for scenario in scenarios_config.values():
        data_sources.add(scenario["data_source"])

    lines = []
    for ds in sorted(data_sources):
        lines.append(
            f"chunkLists['{ds}'] = new SharedArray('{ds}_chunks', "
            f"() => JSON.parse(open('./data/{ds}/chunks.json')));"
        )
    return "\n".join(lines)

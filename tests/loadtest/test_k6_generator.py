import json
import pytest
from pathlib import Path

from loadtest.k6_generator import generate_manifest, generate_test_js


def test_generate_manifest():
    manifest = generate_manifest(
        scenario_name="basic_load",
        target={
            "host": "localhost",
            "port": 1433,
            "database": "test_db",
            "username": "sa",
            "password": "${MSSQL_PASSWORD}",
            "table": "taxi_trips",
        },
        data_source_name="yellow_trips",
        num_chunks=5,
        ordering="parallel",
        workload={"insert": 80, "update": 15, "delete": 5},
        think_time={"min": "200ms", "max": "1s"},
        sql_templates={
            "insert": "INSERT INTO taxi_trips (...) VALUES (...)",
            "update": "UPDATE taxi_trips SET ... WHERE ...",
            "delete": "DELETE FROM taxi_trips WHERE ...",
        },
        column_order=["pickup_time", "dropoff_time", "passenger_count"],
        key_columns=["pickup_time", "dropoff_time"],
    )
    assert manifest["table"] == "taxi_trips"
    assert manifest["data_source"] == "yellow_trips"
    assert manifest["num_chunks"] == 5
    assert manifest["ordering"] == "parallel"
    assert manifest["workload"]["insert"] == 80
    assert "${MSSQL_PASSWORD}" in manifest["connection_string"]
    assert manifest["sql"]["insert"].startswith("INSERT")
    assert manifest["column_order"] == ["pickup_time", "dropoff_time", "passenger_count"]
    assert manifest["key_columns"] == ["pickup_time", "dropoff_time"]


def test_generate_manifest_sequential_warning(capsys):
    manifest = generate_manifest(
        scenario_name="seq_test",
        target={
            "host": "localhost",
            "port": 1433,
            "database": "test_db",
            "username": "sa",
            "password": "pass",
            "table": "t",
        },
        data_source_name="ds",
        num_chunks=3,
        ordering="sequential",
        workload={"insert": 100, "update": 0, "delete": 0},
        think_time={"min": "1s", "max": "2s"},
        sql_templates={"insert": "INSERT ...", "update": "UPDATE ...", "delete": "DELETE ..."},
        column_order=["a"],
        key_columns=["a"],
    )
    assert manifest["ordering"] == "sequential"


def test_generate_test_js_contains_scenarios():
    scenarios_config = {
        "basic_load": {
            "target": "test_server",
            "data_source": "yellow_trips",
            "ordering": "parallel",
            "workload": {"insert": 80, "update": 15, "delete": 5},
            "think_time": {"min": "200ms", "max": "1s"},
            "k6": {"executor": "constant-vus", "vus": 5, "duration": "1m"},
        },
    }
    js = generate_test_js(scenarios_config)
    assert "import sql from" in js
    assert "basic_load" in js
    assert "constant-vus" in js
    assert "export function" in js or "export const" in js
    assert "weightedRandom" in js
    assert "sleep" in js
    assert "SharedArray" in js
    assert "teardown" in js


def test_generate_test_js_sequential_override():
    scenarios_config = {
        "seq_load": {
            "target": "test_server",
            "data_source": "ds",
            "ordering": "sequential",
            "workload": {"insert": 100, "update": 0, "delete": 0},
            "think_time": {"min": "1s", "max": "2s"},
            "k6": {"executor": "constant-vus", "vus": 10, "duration": "5m"},
        },
    }
    js = generate_test_js(scenarios_config)
    assert "per-vu-iterations" in js
    assert '"vus": 1' in js or "'vus': 1" in js


def test_generate_test_js_multiple_scenarios():
    scenarios_config = {
        "load_a": {
            "target": "server_a",
            "data_source": "ds",
            "ordering": "parallel",
            "workload": {"insert": 100, "update": 0, "delete": 0},
            "think_time": {"min": "1s", "max": "2s"},
            "k6": {"executor": "constant-vus", "vus": 5, "duration": "1m"},
        },
        "load_b": {
            "target": "server_b",
            "data_source": "ds",
            "ordering": "parallel",
            "workload": {"insert": 50, "update": 50, "delete": 0},
            "think_time": {"min": "500ms", "max": "1s"},
            "k6": {"executor": "ramping-vus", "startVUs": 1, "stages": [{"duration": "1m", "target": 10}]},
        },
    }
    js = generate_test_js(scenarios_config)
    assert "load_a" in js
    assert "load_b" in js
    assert "loadA" in js
    assert "loadB" in js

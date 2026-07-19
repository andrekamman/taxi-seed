"""YAML config parsing and validation for K6 load test preprocessor."""

import re
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when config validation fails."""
    pass


def load_config(path: Path) -> dict:
    """Load and parse a YAML config file."""
    with open(path) as f:
        return yaml.safe_load(f)


def parse_duration_ms(value: str) -> float:
    """Parse a duration string like '200ms' or '1.5s' to milliseconds."""
    match = re.match(r"^(\d+(?:\.\d+)?)(ms|s)$", value)
    if not match:
        raise ConfigError(f"Invalid duration format: {value!r} (expected e.g. '200ms' or '1s')")
    num, unit = float(match.group(1)), match.group(2)
    return num if unit == "ms" else num * 1000


def validate_config(config: dict) -> None:
    """Validate config structure and cross-references."""
    # Required top-level sections
    for section in ("data_sources", "targets", "scenarios"):
        if section not in config:
            raise ConfigError(f"Missing required section: {section}")

    data_sources = config["data_sources"]
    targets = config["targets"]
    scenarios = config["scenarios"]

    # Validate data sources
    for name, ds in data_sources.items():
        mode = ds.get("mode", "parquet")
        if mode not in ("parquet", "synthetic"):
            raise ConfigError(
                f"Data source {name!r}: mode must be 'parquet' or 'synthetic', "
                f"got {mode!r}"
            )

        if mode == "synthetic":
            for field in ("columns", "key_columns"):
                if field not in ds:
                    raise ConfigError(f"Data source {name!r}: missing required field {field!r}")
            columns = ds["columns"]
            for col_name, col_def in columns.items():
                if not isinstance(col_def, dict) or "type" not in col_def:
                    raise ConfigError(
                        f"Data source {name!r}: synthetic column {col_name!r} "
                        f"must have a 'type' field"
                    )
            for kc in ds["key_columns"]:
                if kc not in columns:
                    raise ConfigError(
                        f"Data source {name!r}: key_column {kc!r} not found in columns"
                    )
        else:
            for field in ("path", "columns", "key_columns"):
                if field not in ds:
                    raise ConfigError(f"Data source {name!r}: missing required field {field!r}")
            columns = ds["columns"]
            for kc in ds["key_columns"]:
                if kc not in columns:
                    raise ConfigError(
                        f"Data source {name!r}: key_column {kc!r} not found in columns mapping"
                    )
            import glob as globmod
            if not globmod.glob(ds["path"]):
                raise ConfigError(
                    f"Data source {name!r}: path {ds['path']!r} matches no files"
                )

    # Validate targets
    for name, target in targets.items():
        for field in ("host", "port", "database", "username", "password", "table"):
            if field not in target:
                raise ConfigError(f"Target {name!r}: missing required field {field!r}")

    # Validate scenarios
    for name, scenario in scenarios.items():
        # Reference checks
        if scenario.get("target") not in targets:
            raise ConfigError(
                f"Scenario {name!r}: target {scenario.get('target')!r} not found in targets"
            )
        if scenario.get("data_source") not in data_sources:
            raise ConfigError(
                f"Scenario {name!r}: data_source {scenario.get('data_source')!r} "
                f"not found in data_sources"
            )

        # Ordering
        ordering = scenario.get("ordering", "parallel")
        if ordering not in ("parallel", "sequential"):
            raise ConfigError(
                f"Scenario {name!r}: ordering must be 'parallel' or 'sequential', "
                f"got {ordering!r}"
            )

        # Workload percentages
        workload = scenario.get("workload", {})
        total = sum(workload.values())
        if total != 100:
            raise ConfigError(
                f"Scenario {name!r}: workload percentages must sum to 100, got {total}"
            )

        # Think time
        think_time = scenario.get("think_time", {})
        for field in ("min", "max"):
            if field in think_time:
                parse_duration_ms(think_time[field])

        # K6 config must exist
        if "k6" not in scenario:
            raise ConfigError(f"Scenario {name!r}: missing required field 'k6'")

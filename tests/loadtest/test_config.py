import pytest
from pathlib import Path

from loadtest.config import load_config, validate_config, ConfigError


FIXTURES = Path(__file__).parent / "fixtures"


def test_load_config_parses_yaml():
    config = load_config(FIXTURES / "sample_config.yaml")
    assert "data_sources" in config
    assert "targets" in config
    assert "scenarios" in config
    assert "yellow_trips" in config["data_sources"]


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))


def test_validate_config_valid():
    config = load_config(FIXTURES / "sample_config.yaml")
    # Should not raise
    validate_config(config)


def test_validate_config_workload_not_100():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["workload"] = {
        "insert": 50,
        "update": 20,
        "delete": 10,
    }
    with pytest.raises(ConfigError, match="must sum to 100"):
        validate_config(config)


def test_validate_config_missing_target_ref():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["target"] = "nonexistent_server"
    with pytest.raises(ConfigError, match="nonexistent_server"):
        validate_config(config)


def test_validate_config_missing_data_source_ref():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["data_source"] = "nonexistent_source"
    with pytest.raises(ConfigError, match="nonexistent_source"):
        validate_config(config)


def test_validate_config_key_columns_not_in_columns():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["data_sources"]["yellow_trips"]["key_columns"] = ["nonexistent_col"]
    with pytest.raises(ConfigError, match="nonexistent_col"):
        validate_config(config)


def test_validate_config_missing_required_fields():
    config = {"data_sources": {}, "targets": {}}
    # Missing scenarios
    with pytest.raises(ConfigError, match="scenarios"):
        validate_config(config)


def test_validate_config_invalid_ordering():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["ordering"] = "random"
    with pytest.raises(ConfigError, match="ordering"):
        validate_config(config)


def test_parse_think_time():
    from loadtest.config import parse_duration_ms
    assert parse_duration_ms("200ms") == 200
    assert parse_duration_ms("1s") == 1000
    assert parse_duration_ms("2s") == 2000
    assert parse_duration_ms("1.5s") == 1500

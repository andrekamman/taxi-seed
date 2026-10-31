def test_taxi_load_importable():
    import taxi_load  # noqa: F401


def test_cli_module_has_main():
    from taxi_load import cli
    assert callable(cli.main)

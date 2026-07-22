def test_taxi_loader_importable():
    import taxi_loader  # noqa: F401


def test_cli_module_has_main():
    from taxi_loader import cli
    assert callable(cli.main)

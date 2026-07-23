def test_package_importable():
    import taxi_orchestrate  # noqa: F401


def test_entry_points_have_main():
    from taxi_orchestrate import cli, curate
    assert callable(cli.main)
    assert callable(curate.main)

def test_package_importable():
    import taxi_pipeline  # noqa: F401


def test_entry_points_have_main():
    from taxi_pipeline import cli, curate
    assert callable(cli.main)
    assert callable(curate.main)

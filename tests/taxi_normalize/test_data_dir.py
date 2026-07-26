"""normalize derives raw/raw-normalized under --data-dir (mappings stay repo-relative)."""
from taxi_normalize.cli import main


def test_data_dir_derives_raw_input_path(tmp_path, capsys):
    # No raw files under the given base -> normalize reports the derived path and skips.
    base = tmp_path / "somewhere"
    rc = main(["yellow", "--data-dir", str(base)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"no raw files at {base / 'raw' / 'yellow'}" in out

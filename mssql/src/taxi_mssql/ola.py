"""Step 4: install Ola Hallengren's MaintenanceSolution into master, once."""
from __future__ import annotations

import tempfile
from pathlib import Path

from taxi_mssql import mssql, sql
from taxi_mssql.config import Settings


class OlaScriptMissingError(Exception):
    """The vendored MaintenanceSolution.sql could not be found on disk. Maps
    to exit code 2."""


# mssql/src/taxi_mssql/ola.py -> parents[2] == mssql/. Both candidates are
# named at module scope (not just computed inline) so a missing-script error
# in run() can report exactly where it looked, not just where it landed.
_SIBLING_CANDIDATE = Path(__file__).resolve().parent / "sql" / "MaintenanceSolution.sql"
_FALLBACK_CANDIDATE = Path(__file__).resolve().parents[2] / "sql" / "MaintenanceSolution.sql"


def _resolve_vendored_script() -> Path:
    """mssql/sql is force-included into the wheel as taxi_mssql/sql (see
    pyproject.toml), so in a built wheel -- and thus in the container image,
    which installs the wheel, not an editable checkout -- this module and the
    vendored script are siblings on disk.

    hatchling's default *editable* dev install (what `uv sync` gives this
    repo) instead just points the taxi_mssql import straight at
    seeder/src/taxi_mssql without materializing force-included files there,
    so the sibling path doesn't exist locally. Empirically verified: building
    a real wheel and `pip install`-ing it non-editably resolves the sibling
    path with VENDORED_SCRIPT.is_file() == True; under `uv sync` it's False.
    Fall back to the repo-relative path so local dev and tests work too.
    """
    if _SIBLING_CANDIDATE.is_file():
        return _SIBLING_CANDIDATE
    return _FALLBACK_CANDIDATE


VENDORED_SCRIPT = _resolve_vendored_script()


def is_installed(settings: Settings, *, runner=None) -> bool:
    return mssql.scalar(settings, sql.ola_installed(),
                        database="master", runner=runner) == "1"


def run(settings: Settings, *, script: Path = VENDORED_SCRIPT, runner=None) -> bool:
    """Install if dbo.DatabaseBackup is absent. Returns True when it installed."""
    if is_installed(settings, runner=runner):
        print("ola: MaintenanceSolution already installed, skipping")
        return False

    script = Path(script)
    if not script.is_file():
        # A broken package build (e.g. the force-include silently dropped
        # from pyproject.toml) must surface as a typed, exit-2 error here,
        # not as a bare FileNotFoundError from read_text() below -- the
        # exception-to-exit-code mapping only knows about typed errors.
        raise OlaScriptMissingError(
            f"vendored MaintenanceSolution.sql not found at {script}. "
            f"Checked the wheel-sibling location ({_SIBLING_CANDIDATE}) and "
            f"the repo-relative dev fallback ({_FALLBACK_CANDIDATE}); the "
            "installed wheel is likely missing its force-included sql/ "
            "directory -- see pyproject.toml "
            "[tool.hatch.build.targets.wheel.force-include]."
        )

    patched = sql.prepare_maintenance_solution(
        script.read_text(), str(settings.backup_dir)
    )
    # sqlcmd -i is required: the script is batch-separated by GO, which only the
    # client understands. The patched copy is temporary; the vendored file on
    # disk stays pristine and diffable against upstream.
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
        fh.write(patched)
        temp_path = fh.name
    try:
        print("ola: installing MaintenanceSolution (@CreateJobs = 'N')")
        mssql.run_file(settings, temp_path, database="master", runner=runner)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return True

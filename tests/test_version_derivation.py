"""Guard the version-derivation config.

The repo carries non-release tags: `mssql-ext-<duckdb>-<build>` anchors the GitHub
release that mirrors the vendored DuckDB mssql extension binaries. setuptools-scm's
default tag_regex accepts a "word-" prefix, so such a tag parses as a version
candidate and then fails with "Can't parse version from tag" -- breaking every build
that resolves a version.

That failure is invisible in the PR test job, because actions/checkout@v4 does not
fetch tags by default. It lands instead on the paths that use fetch-depth: 0 -- the
docs deploy job and the release workflow -- and on any developer who has fetched tags.
Hence a test rather than a comment.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _raw_options() -> dict:
    cfg = tomllib.load(open(ROOT / "pyproject.toml", "rb"))
    return cfg["tool"]["hatch"]["version"]["raw-options"]


def test_describe_is_restricted_to_release_tags():
    cmd = _raw_options().get("git_describe_command", "")
    assert "--match" in cmd, (
        "git_describe_command must restrict describe to release tags; without it a "
        "non-release tag breaks version derivation for the whole repo"
    )
    assert "v*" in cmd


def test_every_tag_in_this_clone_is_either_a_release_or_excluded():
    """Whatever tags exist locally, the configured describe must still succeed.

    This is the behavioural half: the assertion above checks the setting, this checks
    that the setting actually copes with the tags the repo really carries.
    """
    cmd = _raw_options()["git_describe_command"].split()
    try:
        tags = subprocess.run(["git", "tag", "-l"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git clone")
    if not tags:
        pytest.skip("no tags fetched in this clone")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"`{' '.join(cmd)}` failed against the tags in this clone "
        f"({len(tags)} tags): {proc.stderr.strip()}"
    )
    # A non-release tag must never be what describe lands on.
    assert "/" not in proc.stdout and proc.stdout.lstrip().startswith("v"), (
        f"describe resolved to a non-release tag: {proc.stdout.strip()!r}"
    )

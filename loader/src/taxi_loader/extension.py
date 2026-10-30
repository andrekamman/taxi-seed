"""Vendor the DuckDB mssql community extension instead of installing it at runtime.

`INSTALL mssql FROM community` resolves against a registry that **rebuilds**. DuckDB 1.5.5
served build `37596c2` (`mssql_version() 0.2.3`) in mid-August 2026 and serves `e2b8c01`
(`0.2.4`) since 2026-08-18, with no DuckDB release in between. Pinning either the build hash
or the semver therefore pins nothing: the bytes move underneath an unchanged pin, and the
only thing an assertion can do is turn that into a hard failure at load time.

So we own the bytes. taxi-seed mirrors the extension as a release asset, and the loader loads
it from a vendored extension directory. Three things make this cheap and safe:

- DuckDB **enforces the extension signature** on this path, under the default configuration.
  `allow_unsigned_extensions` is NOT required, and a tampered file is refused with
  "signature is either missing or invalid" (verified 2026-08-30 by flipping 64 payload
  bytes). Integrity is cryptographic; the SHA-256s below are an identity pin, not the
  integrity mechanism.
- `LOAD mssql` resolves **by name** out of `extension_directory` with no `INSTALL`, no
  network, and no `.info` sidecar. Nothing in the loader needs to handle paths.
- Mirroring is not a trust substitution. The signature still proves DuckDB published the
  binary; hosting it only guarantees we can still *get* the one we tested.

Everything below derives from ONE pinned target. The DuckDB version, the extension build,
the semver and the download URL are a single fact expressed four ways, which is exactly how
they drift apart. A bump is one deliberate edit here, plus a re-spike.
"""
from __future__ import annotations

import gzip
import hashlib
import platform
import shutil
import sys
import urllib.request
from pathlib import Path

# --- the pinned target ------------------------------------------------------
# Bumping any of these means re-running the probe (docs/superpowers/notes/) and
# re-vendoring every platform. They are not independently adjustable.
DUCKDB_VERSION = "1.5.5"
EXTENSION_NAME = "mssql"
# `SELECT extension_version FROM duckdb_extensions()` -- the registry's build hash.
EXPECTED_EXT_VERSION = "e2b8c01"
# `SELECT mssql_version()` -- the extension's own semver. Moves on rebuild too,
# which is why neither value alone is a pin; see the module docstring.
EXPECTED_MSSQL_VERSION = "0.2.4"

# Upstream. Note the community registry is served over plain HTTP -- the signature,
# not the transport, is what protects this fetch.
UPSTREAM_URL = (
    "http://community-extensions.duckdb.org/v{duckdb_version}/{plat}/{name}.duckdb_extension.gz"
)
# Our mirror, for when upstream rebuilds or drops a build. DuckDB 1.4.5 has no mssql
# build at all, so disappearance is not hypothetical.
MIRROR_URL = (
    "https://github.com/andrekamman/taxi-seed/releases/download/"
    "mssql-ext-{duckdb_version}-{ext_version}/{name}-{plat}.duckdb_extension.gz"
)

# SHA-256 of the DECOMPRESSED .duckdb_extension -- the artifact DuckDB actually loads.
# Captured 2026-08-30; linux_amd64 was confirmed byte-identical to the copy DuckDB's own
# INSTALL had cached.
SHA256 = {
    "linux_amd64": "492fc1e39ba670f88247f94a031bc5b7e98ce85e5ee9b2976e503de9a315e91c",
    "linux_arm64": "ee7368a203de8112bf1fd2244ab0b7ce25ff6de679b14b281f146a80546d9031",
    "osx_amd64": "a79061e23de9d42476a3536c4b3f952cdef2f280bbb4e5ff47b85a9e0f0c9134",
    "osx_arm64": "65fd41a00d358de46b8720dad056f73671c82a2c11fffe354250cd7d6fa924fd",
    "windows_amd64": "da1acc3971e8a45afdf1647b11d317d0226e72845629cf4da26c84418a6ab735",
}


def _urlopen(url: str):
    """Open a URL with a browser-ish User-Agent.

    The community CDN answers `Python-urllib/3.x` with 403 Forbidden while serving the
    identical URL to curl, so the default opener fails on a fetch that works by hand.
    Nothing here depends on being mistaken for a browser -- the signature and the SHA-256
    decide what we accept -- it only has to not be rejected before we can check either.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "taxi-seed/vendor-extension"})
    return urllib.request.urlopen(req)


class ExtensionError(Exception):
    """Vendoring or verification failure. Maps to exit code 2."""


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """DuckDB's platform string for the running interpreter.

    DuckDB names the directory under the extension root; get this wrong and LOAD
    silently finds nothing and falls back to the network.
    """
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {
        "x86_64": "amd64", "amd64": "amd64",
        "aarch64": "arm64", "arm64": "arm64",
    }.get(machine)
    osname = {"linux": "linux", "darwin": "osx", "windows": "windows"}.get(system)
    if osname is None or arch is None:
        raise ExtensionError(
            f"unsupported platform {system}/{machine}: no vendored mssql extension. "
            f"Known platforms: {', '.join(sorted(SHA256))}."
        )
    key = f"{osname}_{arch}"
    if key not in SHA256:
        raise ExtensionError(
            f"no vendored mssql extension for {key}. "
            f"Known platforms: {', '.join(sorted(SHA256))}."
        )
    return key


def upstream_url(plat: str) -> str:
    return UPSTREAM_URL.format(
        duckdb_version=DUCKDB_VERSION, plat=plat, name=EXTENSION_NAME
    )


def mirror_url(plat: str) -> str:
    return MIRROR_URL.format(
        duckdb_version=DUCKDB_VERSION, ext_version=EXPECTED_EXT_VERSION,
        plat=plat, name=EXTENSION_NAME,
    )


def extension_path(root: Path, plat: str) -> Path:
    """Where DuckDB looks: <root>/v<duckdb version>/<platform>/<name>.duckdb_extension."""
    return (Path(root) / f"v{DUCKDB_VERSION}" / plat
            / f"{EXTENSION_NAME}.duckdb_extension")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: Path, plat: str) -> None:
    """Raise unless the file on disk is the exact artifact we pinned."""
    expected = SHA256[plat]
    actual = sha256_file(path)
    if actual != expected:
        raise ExtensionError(
            f"sha256 mismatch for {path}: expected {expected}, got {actual}. "
            f"Upstream may have rebuilt {EXTENSION_NAME} for DuckDB {DUCKDB_VERSION}; "
            f"re-spike before re-pinning rather than editing the hash to match."
        )


def fetch(root: Path, plat: str | None = None, *, opener=None) -> Path:
    """Download, verify and place the vendored extension. Idempotent.

    Tries our mirror first and upstream second: the mirror is the copy we tested, and
    upstream is the one that moves. A file already present and matching is left alone.
    """
    plat = plat or platform_key()
    dest = extension_path(root, plat)
    if dest.exists():
        verify_file(dest, plat)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    opener = opener or _urlopen

    errors = []
    for url in (mirror_url(plat), upstream_url(plat)):
        tmp = dest.with_suffix(".download")
        try:
            with opener(url) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out)
            with gzip.open(tmp, "rb") as gz, open(dest, "wb") as out:
                shutil.copyfileobj(gz, out)
            tmp.unlink(missing_ok=True)
            verify_file(dest, plat)
            return dest
        except Exception as e:                       # noqa: BLE001 -- try the next source
            errors.append(f"{url}: {type(e).__name__}: {e}")
            tmp.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)
    raise ExtensionError(
        "could not vendor the mssql extension from any source:\n  "
        + "\n  ".join(errors)
    )


def assert_loaded(conn) -> None:
    """Assert the loaded extension is the one we pinned, on both identifiers.

    Neither check is a supply-chain control -- the signature is. They catch operator
    error, and they catch different kinds: the build hash catches a version re-pinned
    without re-spiking, and the semver catches a stale vendored directory. Name which
    one disagreed, or the message sends the reader to the wrong place.
    """
    row = conn.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = ?",
        [EXTENSION_NAME],
    ).fetchone()
    ext = row[0] if row else None
    ver = conn.execute(f"SELECT {EXTENSION_NAME}_version()").fetchone()[0]
    problems = []
    if ext != EXPECTED_EXT_VERSION:
        problems.append(f"build hash {ext!r} != expected {EXPECTED_EXT_VERSION!r}")
    if ver != EXPECTED_MSSQL_VERSION:
        problems.append(f"version {ver!r} != expected {EXPECTED_MSSQL_VERSION!r}")
    if problems:
        raise ExtensionError(
            "vendored mssql extension is not the pinned build: "
            + "; ".join(problems)
            + f". Expected DuckDB {DUCKDB_VERSION}. Re-run `taxi-vendor-extension` "
            f"against a clean extension directory."
        )


def main(argv=None) -> int:
    """taxi-vendor-extension: place the pinned extension in an extension directory."""
    import argparse

    p = argparse.ArgumentParser(
        prog="taxi-vendor-extension",
        description="Download and verify the pinned DuckDB mssql extension.",
    )
    p.add_argument("--root", default=str(Path.home() / ".taxi-seed" / "extensions"),
                   help="extension directory root (default: ~/.taxi-seed/extensions)")
    p.add_argument("--platform", default=None,
                   help=f"override the platform ({', '.join(sorted(SHA256))})")
    p.add_argument("--all", action="store_true",
                   help="vendor every platform, for building a release or an image")
    args = p.parse_args(argv)

    try:
        plats = sorted(SHA256) if args.all else [args.platform or platform_key()]
        for plat in plats:
            path = fetch(Path(args.root), plat)
            print(f"ok  {plat}  {path}")
        print(f"\nDuckDB {DUCKDB_VERSION}, mssql build {EXPECTED_EXT_VERSION} "
              f"(version {EXPECTED_MSSQL_VERSION})")
        print(f"Point DuckDB at it with: SET extension_directory = '{args.root}'")
    except ExtensionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

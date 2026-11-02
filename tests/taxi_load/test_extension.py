import gzip
import hashlib
import io
from pathlib import Path

import pytest

from taxi_load.extension import (
    DUCKDB_VERSION, EXPECTED_EXT_VERSION, EXPECTED_MSSQL_VERSION, SHA256,
    ExtensionError, assert_loaded, extension_path, fetch, mirror_url,
    platform_key, upstream_url, verify_file,
)


# --- platform mapping -------------------------------------------------------

@pytest.mark.parametrize("system,machine,expected", [
    ("Linux", "x86_64", "linux_amd64"),
    ("Linux", "aarch64", "linux_arm64"),
    ("Darwin", "x86_64", "osx_amd64"),
    ("Darwin", "arm64", "osx_arm64"),
    ("Windows", "AMD64", "windows_amd64"),
])
def test_platform_key_maps_known(system, machine, expected):
    assert platform_key(system, machine) == expected


@pytest.mark.parametrize("system,machine", [
    ("Linux", "riscv64"), ("FreeBSD", "x86_64"), ("Darwin", "ppc64"),
])
def test_platform_key_rejects_unknown(system, machine):
    # Falling back to a guess would make LOAD miss the vendored dir and silently
    # reach for the network, which is the whole thing vendoring exists to stop.
    with pytest.raises(ExtensionError):
        platform_key(system, machine)


def test_every_known_platform_has_a_hash():
    for plat in SHA256:
        assert len(SHA256[plat]) == 64
        assert SHA256[plat] == SHA256[plat].lower()


# --- one constant, four derivations -----------------------------------------

def test_urls_and_path_all_derive_from_the_pinned_version():
    for plat in SHA256:
        assert f"/v{DUCKDB_VERSION}/{plat}/" in upstream_url(plat)
        assert DUCKDB_VERSION in mirror_url(plat)
        assert EXPECTED_EXT_VERSION in mirror_url(plat)
        assert plat in mirror_url(plat)


def test_extension_path_matches_duckdbs_layout(tmp_path):
    p = extension_path(tmp_path, "linux_amd64")
    assert p == tmp_path / f"v{DUCKDB_VERSION}" / "linux_amd64" / "mssql.duckdb_extension"


# --- verification -----------------------------------------------------------

def _plant(root: Path, plat: str, payload: bytes) -> Path:
    p = extension_path(root, plat)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def test_verify_file_rejects_wrong_bytes(tmp_path):
    p = _plant(tmp_path, "linux_amd64", b"not the extension")
    with pytest.raises(ExtensionError) as e:
        verify_file(p, "linux_amd64")
    assert "sha256 mismatch" in str(e.value)


def test_verify_file_accepts_matching_bytes(tmp_path, monkeypatch):
    payload = b"pretend extension"
    monkeypatch.setitem(SHA256, "linux_amd64", hashlib.sha256(payload).hexdigest())
    verify_file(_plant(tmp_path, "linux_amd64", payload), "linux_amd64")


# --- fetch ------------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _opener_for(payload: bytes, fail_urls=()):
    calls = []

    def opener(url):
        calls.append(url)
        if url in fail_urls or any(f in url for f in fail_urls):
            raise OSError(f"simulated failure for {url}")
        return _Resp(gzip.compress(payload))

    opener.calls = calls
    return opener


def test_fetch_verifies_and_places(tmp_path, monkeypatch):
    payload = b"pretend extension"
    monkeypatch.setitem(SHA256, "linux_amd64", hashlib.sha256(payload).hexdigest())
    dest = fetch(tmp_path, "linux_amd64", opener=_opener_for(payload))
    assert dest.read_bytes() == payload
    assert dest == extension_path(tmp_path, "linux_amd64")


def test_fetch_prefers_the_mirror(tmp_path, monkeypatch):
    payload = b"pretend extension"
    monkeypatch.setitem(SHA256, "linux_amd64", hashlib.sha256(payload).hexdigest())
    op = _opener_for(payload)
    fetch(tmp_path, "linux_amd64", opener=op)
    # The mirror is the copy we tested; upstream is the one that moves.
    assert op.calls == [mirror_url("linux_amd64")]


def test_fetch_falls_back_to_upstream(tmp_path, monkeypatch):
    payload = b"pretend extension"
    monkeypatch.setitem(SHA256, "linux_amd64", hashlib.sha256(payload).hexdigest())
    op = _opener_for(payload, fail_urls=("github.com",))
    fetch(tmp_path, "linux_amd64", opener=op)
    assert op.calls == [mirror_url("linux_amd64"), upstream_url("linux_amd64")]


def test_fetch_leaves_no_file_when_every_source_fails(tmp_path, monkeypatch):
    payload = b"pretend extension"
    monkeypatch.setitem(SHA256, "linux_amd64", hashlib.sha256(payload).hexdigest())
    op = _opener_for(payload, fail_urls=("github.com", "community-extensions"))
    with pytest.raises(ExtensionError):
        fetch(tmp_path, "linux_amd64", opener=op)
    # A half-written extension would fail the signature check with a confusing
    # message on the next run, rather than re-downloading.
    assert not extension_path(tmp_path, "linux_amd64").exists()


def test_fetch_rejects_a_source_serving_the_wrong_bytes(tmp_path):
    op = _opener_for(b"a rebuilt binary we did not pin")
    with pytest.raises(ExtensionError):
        fetch(tmp_path, "linux_amd64", opener=op)
    assert not extension_path(tmp_path, "linux_amd64").exists()


def test_fetch_is_idempotent(tmp_path, monkeypatch):
    payload = b"pretend extension"
    monkeypatch.setitem(SHA256, "linux_amd64", hashlib.sha256(payload).hexdigest())
    op = _opener_for(payload)
    fetch(tmp_path, "linux_amd64", opener=op)
    fetch(tmp_path, "linux_amd64", opener=op)
    assert len(op.calls) == 1


# --- the load-time assertion ------------------------------------------------

class _Conn:
    def __init__(self, ext, ver): self.ext, self.ver = ext, ver

    def execute(self, sql, params=None):
        self.result = (self.ext,) if "duckdb_extensions" in sql else (self.ver,)
        return self

    def fetchone(self): return self.result


def test_assert_loaded_accepts_the_pinned_build():
    assert_loaded(_Conn(EXPECTED_EXT_VERSION, EXPECTED_MSSQL_VERSION))


def test_assert_loaded_names_the_hash_when_only_it_differs():
    with pytest.raises(ExtensionError) as e:
        assert_loaded(_Conn("37596c2", EXPECTED_MSSQL_VERSION))
    assert "build hash" in str(e.value)
    assert "version" not in str(e.value).split("build hash")[1].split(".")[0]


def test_assert_loaded_names_the_version_when_only_it_differs():
    with pytest.raises(ExtensionError) as e:
        assert_loaded(_Conn(EXPECTED_EXT_VERSION, "0.2.3"))
    assert "version" in str(e.value)


def test_assert_loaded_reports_both_when_both_differ():
    with pytest.raises(ExtensionError) as e:
        assert_loaded(_Conn("37596c2", "0.2.3"))
    msg = str(e.value)
    assert "build hash" in msg and "version" in msg

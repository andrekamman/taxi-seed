# tests/downloader/conftest.py
"""A stdlib HTTP stub that serves real (DuckDB-built) parquet for a chosen set of
months, 404 for anything else, and a designated month that returns 429 a fixed
number of times before succeeding — so backoff/walker tests never hit the network.

Usage: `stub` yields an object with `.base_url` (point BASE_URL at it),
`.present` (set of "<type>_tripdata_<yyyy>-<mm>.parquet" filenames it serves),
and `.ratelimit` (dict filename -> remaining 429s to emit before a 200)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import duckdb
import pytest


def _valid_parquet_bytes(tmp_path) -> bytes:
    p = tmp_path / "_sample.parquet"
    duckdb.execute(f"COPY (SELECT 1 AS a) TO '{p}' (FORMAT PARQUET)")
    return p.read_bytes()


class _State:
    def __init__(self, body: bytes):
        self.body = body
        self.present: set[str] = set()
        self.ratelimit: dict[str, int] = {}
        self.base_url = ""


@pytest.fixture
def stub(tmp_path):
    state = _State(_valid_parquet_bytes(tmp_path))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def do_GET(self):
            name = self.path.rsplit("/", 1)[-1]
            if state.ratelimit.get(name, 0) > 0:
                state.ratelimit[name] -= 1
                self.send_response(429)
                self.end_headers()
                self.wfile.write(b"slow down")
                return
            if name in state.present:
                self.send_response(200)
                self.send_header("Content-Length", str(len(state.body)))
                self.end_headers()
                self.wfile.write(state.body)
                return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"NoSuchKey")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    state.base_url = f"http://{host}:{port}/trip-data"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()

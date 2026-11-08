#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Loopback/internal review HTTP for harvey-sss. Not published to the host.

Bind default 0.0.0.0:18765 inside the container so the internal docker
network can reach it. Parent must NOT publish this port. Token required.
GET preview is read-only. No send route.
"""
from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# /app/data is on sys.path in production overlay.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))

from sss_glm.review_api import DB_PATH, TOKEN_FILE, handle, load_token


BIND = os.environ.get("SSS_GLM_REVIEW_BIND", "0.0.0.0:18765")
DB = os.environ.get("SSS_GLM_DB", DB_PATH)
TOKEN_PATH = os.environ.get("SSS_GLM_REVIEW_TOKEN_FILE", TOKEN_FILE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _dispatch(self, method: str):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self._send(413, {"ok": False, "error": "too_large"})
            return
        body = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()}
        headers["QUERY_STRING"] = self.path.split("?", 1)[1] if "?" in self.path else ""
        code, payload = handle(
            method, self.path, body, headers, db_path=DB, token=load_token(TOKEN_PATH),
        )
        self._send(code, payload)

    def _send(self, code: int, payload: dict):
        import json
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._send(405, {"ok": False, "error": "method"})


def main() -> int:
    host, port_s = BIND.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port_s)), Handler)
    print(f"sss-glm-review {host}:{port_s} (no send, token required)", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

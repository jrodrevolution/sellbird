# SPDX-License-Identifier: AGPL-3.0-only
"""Internal SSS review actions. Preview is read-only. No send path."""

from __future__ import annotations

import hmac
import json
import sqlite3
from pathlib import Path

from sss_glm.approve import approve_items, reject_items
from sss_glm.preview import preview

TOKEN_FILE = "/app/data/sss-glm-review.token"
DB_PATH = "/app/data/harvey.db"


def load_token(path: str = TOKEN_FILE) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8").strip()


def token_ok(offered: str, expected: str) -> bool:
    if not expected or not offered:
        return False
    return hmac.compare_digest(offered.encode("utf-8"), expected.encode("utf-8"))


def bearer(header: str) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def _ro(db_path: str):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _rw(db_path: str):
    return sqlite3.connect(db_path)


def handle(method: str, path: str, body: bytes, headers: dict, *,
           db_path: str = DB_PATH, token: str = "") -> tuple[int, dict]:
    """Dispatch one review request. GET preview never writes."""
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not token_ok(bearer(auth), token):
        return 401, {"ok": False, "error": "unauthorized"}
    path = path.split("?", 1)[0]
    method = method.upper()
    if method == "GET" and path in ("/v1/health", "/health"):
        return 200, {"ok": True, "send": False}
    if method == "GET" and path in ("/v1/preview", "/preview"):
        qs = headers.get("QUERY_STRING") or ""
        batch_id = ""
        for part in qs.split("&"):
            if part.startswith("batch_id="):
                batch_id = part.split("=", 1)[1]
        c = _ro(db_path)
        try:
            doc = preview(c, batch_id or None)
        finally:
            c.close()
        doc["sent"] = False
        return 200, doc
    if method != "POST":
        return 405, {"ok": False, "error": "method"}
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return 400, {"ok": False, "error": "bad_json"}
    items = payload.get("items") or []
    if path in ("/v1/approve", "/approve"):
        if payload.get("confirm") != "approve":
            return 400, {"ok": False, "error": "confirm_required", "need": "approve"}
        c = _rw(db_path)
        try:
            out = approve_items(c, items, apply=True)
        finally:
            c.close()
        return 200, out
    if path in ("/v1/reject", "/reject"):
        if payload.get("confirm") != "reject":
            return 400, {"ok": False, "error": "confirm_required", "need": "reject"}
        c = _rw(db_path)
        try:
            out = reject_items(c, items, apply=True)
        finally:
            c.close()
        return 200, out
    return 404, {"ok": False, "error": "not_found"}

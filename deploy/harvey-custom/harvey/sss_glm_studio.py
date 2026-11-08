# SPDX-License-Identifier: AGPL-3.0-only
"""Narrow SSS GLM review proxy for the custom Harvey Studio dashboard.

Talks to harvey-sss over the internal docker network. Does not open the SSS
SQLite file. GET preview is read-only. Approve/reject require confirm phrases.
No send route. Token stays on the server; it is never sent to the browser.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

REVIEW_URL = os.environ.get("SSS_GLM_REVIEW_URL", "http://harvey-sss:18765")
TOKEN_FILE = os.environ.get("SSS_GLM_REVIEW_TOKEN_FILE", "/app/data/sss-glm-review.token")


def _token() -> str:
    p = Path(TOKEN_FILE)
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8").strip()


def _call(method: str, path: str, payload: dict | None = None, query: str = "") -> tuple[int, dict]:
    token = _token()
    if not token:
        return 503, {
            "ok": False,
            "error": "review_token_missing",
            "detail": "Parent must write the review token into both data dirs.",
        }
    url = REVIEW_URL.rstrip("/") + path + (("?" + query) if query else "")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body or "{}")
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            parsed = {"ok": False, "error": "upstream", "status": e.code}
        return e.code, parsed
    except Exception as e:
        return 502, {"ok": False, "error": "sss_unreachable", "detail": str(e)[:200]}


def register(app):
    @app.get("/api/sss-glm/preview")
    async def preview(batch_id: str = ""):
        code, doc = _call("GET", "/v1/preview", query=("batch_id=" + batch_id if batch_id else ""))
        return JSONResponse(doc, status_code=code)

    @app.post("/api/sss-glm/approve")
    async def approve(request: Request):
        d = await request.json()
        if d.get("confirm") != "approve":
            return JSONResponse({"ok": False, "error": "confirm_required", "need": "approve"}, 400)
        if "send" in d:
            return JSONResponse({"ok": False, "error": "send_forbidden"}, 400)
        code, doc = _call("POST", "/v1/approve", {
            "confirm": "approve",
            "batch_id": d.get("batch_id") or "",
            "items": d.get("items") or [],
        })
        return JSONResponse(doc, status_code=code)

    @app.post("/api/sss-glm/reject")
    async def reject(request: Request):
        d = await request.json()
        if d.get("confirm") != "reject":
            return JSONResponse({"ok": False, "error": "confirm_required", "need": "reject"}, 400)
        code, doc = _call("POST", "/v1/reject", {
            "confirm": "reject",
            "items": d.get("items") or [],
        })
        return JSONResponse(doc, status_code=code)

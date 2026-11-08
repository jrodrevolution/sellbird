# SPDX-License-Identifier: AGPL-3.0-only
"""Hermes-side worker: consume allowlisted requests, call GLM, write responses.

Runs as uid hermes inside hermes-agent. No email send, no Docker, no SSS
credentials. Private usage ledger is not mounted into SSS. Fail closed on
every error; never writes pending_review for invalid output.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sss_glm.fs import (
    FSError,
    assert_real_dir,
    atomic_create,
    atomic_replace_private,
    list_request_names,
    read_request_file,
)
from sss_glm.model import HermesRunner, ModelRunner, prompt_payload
from sss_glm.protocol import (
    ProtocolError,
    canonical_dumps,
    held_response,
    iso_now,
    ok_response,
    parse_request,
    request_digest,
)
from sss_glm.validate import parse_model_json, validate_model_draft

TIJUANA = ZoneInfo("America/Tijuana")
DAILY_CAP = 20
DEFAULT_REQUESTS = "/var/sss-glm/requests"
DEFAULT_RESPONSES = "/var/sss-glm/responses"
DEFAULT_LEDGER = "/var/sss-glm/ledger"


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.file = self.path / "usage.json"

    def load(self, *, day: str) -> dict:
        if not self.file.is_file():
            return {"day": day, "calls": 0, "ids": {}, "nonces": []}
        try:
            import json
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            return {"day": day, "calls": 0, "ids": {}, "nonces": []}
        if data.get("day") != day:
            return {"day": day, "calls": 0, "ids": {}, "nonces": []}
        data.setdefault("ids", {})
        data.setdefault("nonces", [])
        data.setdefault("calls", 0)
        return data

    def save(self, data: dict) -> None:
        assert_real_dir(self.path)
        atomic_replace_private(self.file, data, mode=0o600)


class Worker:
    def __init__(self, requests_dir, responses_dir, ledger_dir, *,
                 model: ModelRunner | None = None, now_fn=None, daily_cap: int = DAILY_CAP):
        self.requests_dir = Path(requests_dir)
        self.responses_dir = Path(responses_dir)
        self.ledger = Ledger(Path(ledger_dir))
        self.model = model or HermesRunner()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.daily_cap = daily_cap

    def _day(self, now: datetime) -> str:
        return now.astimezone(TIJUANA).date().isoformat()

    def process_once(self, *, limit: int = 20) -> list[dict]:
        assert_real_dir(self.requests_dir)
        assert_real_dir(self.responses_dir)
        assert_real_dir(self.ledger.path)
        out = []
        for name in list_request_names(self.requests_dir, limit=limit):
            out.append(self._one(name))
        return out

    def _one(self, name: str) -> dict:
        now = self.now_fn()
        ident = name.removesuffix(".json")
        digest = ""
        try:
            raw = read_request_file(self.requests_dir, name)
            req = parse_request(raw, now=now)
            if req["id"] != ident:
                raise ProtocolError("request_id", "filename")
            digest = request_digest(req)
            usage = self.ledger.load(day=self._day(now))
            prev = usage["ids"].get(ident)
            if prev:
                if prev.get("sha256") != digest:
                    raise ProtocolError("replay", "id_reused")
                raise ProtocolError("replay", "duplicate")
            if req["nonce"] in usage["nonces"]:
                raise ProtocolError("replay", "nonce")
            if usage["calls"] >= self.daily_cap:
                raise ProtocolError("daily_cap")
            if not self.model.tools_are_empty():
                raise ProtocolError("model_unavailable", "tools")
            usage["calls"] += 1
            usage["ids"][ident] = {"sha256": digest, "at": iso_now(now)}
            usage["nonces"] = (usage["nonces"] + [req["nonce"]])[-500:]
            self.ledger.save(usage)
            raw_out = self.model.generate(prompt_payload(req))
            parsed = parse_model_json(raw_out)
            subject, body = validate_model_draft(req, parsed["subject"], parsed["body"])
            resp = ok_response(ident, digest, subject, body, now=now)
        except (ProtocolError, FSError) as e:
            code = getattr(e, "code", "held_error")
            resp = held_response(ident, digest, code, now=now)
        except Exception:
            resp = held_response(ident, digest, "held_error", now=now)
        try:
            atomic_create(
                self.responses_dir, ident + ".json",
                canonical_dumps(resp), mode=0o640,
            )
        except FSError as e:
            if e.code != "replay":
                # Response already present: leave it (idempotent).
                pass
        return resp


def main(argv=None) -> int:
    req = os.environ.get("SSS_GLM_REQUESTS", DEFAULT_REQUESTS)
    resp = os.environ.get("SSS_GLM_RESPONSES", DEFAULT_RESPONSES)
    led = os.environ.get("SSS_GLM_LEDGER", DEFAULT_LEDGER)
    Worker(req, resp, led).process_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

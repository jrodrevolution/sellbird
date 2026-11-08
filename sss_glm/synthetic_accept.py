# SPDX-License-Identifier: AGPL-3.0-only
"""Standalone no-recipient GLM acceptance. Production queues stay untouched.

Run inside hermes-agent (see host/synthetic-glm-accept.sh). example.com only.
Does not write /var/sss-glm, outbox, jobs, or approved worker paths.
Prints sanitized finish/turn diagnostics, never env, keys, or prompts.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from sss_glm.model import (
    HERMES_KW,
    HermesRunner,
    MAX_AGENT_ITERATIONS,
    MAX_COMPLETION_TOKENS,
)
from sss_glm.protocol import MODEL_ID, make_request, model_payload
from sss_glm.validate import parse_model_json, validate_model_draft


def run_once(runner=None) -> dict:
    now = datetime.now(timezone.utc)
    req = make_request(
        domain="example.com", finding="DMARC", category="dental",
        verified_at=now.isoformat(), now=now,
    )
    payload = model_payload(req)
    used = runner or HermesRunner()
    if not used.tools_are_empty():
        return {
            "ok": False, "state": "held_error", "error": "model_unavailable:tools",
            "recipient": None, "sent": False, "model": MODEL_ID,
            "tools_empty": False, "max_iterations": MAX_AGENT_ITERATIONS,
            "max_tokens": MAX_COMPLETION_TOKENS, "uid": os.getuid(),
        }
    try:
        raw = used.generate(payload)
        parsed = parse_model_json(raw)
        subject, body = validate_model_draft(req, parsed["subject"], parsed["body"])
        diag = dict(getattr(used, "last_diag", None) or {})
        return {
            "ok": True, "state": "held_example", "error": "",
            "recipient": None, "sent": False, "model": MODEL_ID,
            "tools_empty": True, "subject": subject, "body": body,
            "max_iterations": HERMES_KW["max_iterations"],
            "max_tokens": HERMES_KW["max_tokens"],
            "uid": os.getuid(), "diag": diag,
        }
    except Exception as e:
        code = getattr(e, "code", "held_error")
        detail = str(e)[:80]
        diag = dict(getattr(used, "last_diag", None) or {})
        return {
            "ok": False, "state": "held_error", "error": f"{code}:{detail}"[:120],
            "recipient": None, "sent": False, "model": MODEL_ID,
            "tools_empty": True,
            "max_iterations": HERMES_KW["max_iterations"],
            "max_tokens": HERMES_KW["max_tokens"],
            "uid": os.getuid(), "diag": diag,
        }


def main(argv=None) -> int:
    doc = run_once()
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0 if doc.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

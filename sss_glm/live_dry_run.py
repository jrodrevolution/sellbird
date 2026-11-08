# SPDX-License-Identifier: AGPL-3.0-only
"""Optional real-model dry run. Disabled by default. No recipients, no SMTP, no outbox.

Requires the Hermes runtime and SSS_GLM_LIVE=1. Uses example.com only.
"""

from __future__ import annotations

import os
import sys

from sss_glm.claims import FINDING_MEANING
from sss_glm.model import HermesRunner, prompt_payload
from sss_glm.protocol import make_request, iso_now
from sss_glm.validate import parse_model_json, validate_model_draft


def main(argv=None) -> int:
    if os.environ.get("SSS_GLM_LIVE") != "1":
        print("disabled: set SSS_GLM_LIVE=1 to call the configured Hermes GLM once "
              "(example.com fixture, no recipient, no send)", file=sys.stderr)
        return 2
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    req = make_request(
        domain="example.com", finding="DMARC", category="dental",
        verified_at=now.isoformat(), now=now,
    )
    runner = HermesRunner()
    if not runner.tools_are_empty():
        print("HELD: tools not empty; no model call", file=sys.stderr)
        return 1
    raw = runner.generate(prompt_payload(req))
    parsed = parse_model_json(raw)
    subject, body = validate_model_draft(req, parsed["subject"], parsed["body"])
    print("state=held_example model=z-ai/glm-5.3-flash recipient=None sent=False")
    print("subject:", subject)
    print("body:", body)
    print("finding_meaning:", FINDING_MEANING["DMARC"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

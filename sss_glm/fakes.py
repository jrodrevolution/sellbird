# SPDX-License-Identifier: AGPL-3.0-only
"""In-process fakes for tests. Never used for live sends."""

from __future__ import annotations

import json

from sss_glm.claims import AUDIENCE, FINDING_MEANING
from sss_glm.protocol import model_payload


class FakeModel:
    """Writes a full subject/body, not an opener variant."""

    def __init__(self, raw: str | None = None, tools=None):
        self.raw = raw
        self.tools = [] if tools is None else tools
        self.calls = []

    def tools_are_empty(self) -> bool:
        return self.tools == []

    def generate(self, payload: dict) -> str:
        self.calls.append(payload)
        if self.raw is not None:
            return self.raw
        domain = payload["domain"]
        finding = payload["finding"]
        audience = payload.get("audience") or "clients"
        meaning = payload.get("finding_meaning") or FINDING_MEANING[finding]
        body = (
            f"Hi there, I ran a free public check on {domain} and noticed {meaning}. "
            f"That is visible from the public internet, so {audience} can see it too. "
            f"I am only naming that one public check. You can run the same free scan "
            f"in plain English when you have a minute."
        )
        subject = f"quick note on {domain}"
        return json.dumps({"subject": subject, "body": body})


class ToolsOnModel(FakeModel):
    def __init__(self):
        super().__init__(tools=["web"])

    def tools_are_empty(self) -> bool:
        return False

    def generate(self, payload: dict) -> str:
        raise AssertionError("model must not be called when tools are enabled")


def fake_finalize(body: str, pid: str) -> str:
    token = (pid or "fixture")[:24] or "fixture"
    return (
        body.rstrip()
        + "\n\n-- \nSite Safety Scan\n123 Example Ave, San Ysidro, CA 92173\n"
        + f"https://sitesafetyscan.com/u/{token}"
    )


def payload_for(domain: str, finding: str, category: str) -> dict:
    req = {"domain": domain, "finding": finding, "category": category}
    return model_payload(req)

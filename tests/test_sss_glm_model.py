# SPDX-License-Identifier: AGPL-3.0-only
"""Hermes SDK adapter: bounded iterations, fail-closed exhaustion, JSON parse."""

from __future__ import annotations

import json

import pytest

from sss_glm.fakes import FakeModel
from sss_glm.model import (
    HERMES_KW,
    HermesRunner,
    MAX_AGENT_ITERATIONS,
    MAX_COMPLETION_TOKENS,
    _tools_empty,
    sanitize_sdk_diag,
)
from sss_glm.protocol import MODEL_ID, ProtocolError, make_request, model_payload
from sss_glm.synthetic_accept import run_once
from sss_glm.validate import parse_model_json, validate_model_draft


class _EmptyTools:
    tools = []
    enabled_toolsets = []
    max_iterations = 8
    max_tokens = 1536
    model = MODEL_ID
    api_call_count = 2

    def __init__(self, raw: str):
        self._raw = raw

    def chat(self, user):
        assert "example.com" in user or "sss-glm-request" in user
        return self._raw


def test_agent_iteration_bounds_are_finite_not_one():
    assert HERMES_KW["max_iterations"] == MAX_AGENT_ITERATIONS
    assert 1 < HERMES_KW["max_iterations"] <= 16
    assert HERMES_KW["max_tokens"] == MAX_COMPLETION_TOKENS
    assert 500 < HERMES_KW["max_tokens"] <= 4096
    assert HERMES_KW["enabled_toolsets"] == []
    assert HERMES_KW["fallback_model"] == {}
    assert HERMES_KW["skip_memory"] is True


def test_hermes_runner_exhaustion_is_invalid_not_json():
    agent = _EmptyTools("I reached the iteration limit and couldn't generate a summary.")
    assert _tools_empty(agent)
    runner = HermesRunner(agent=agent)
    with pytest.raises(ProtocolError) as ei:
        runner.generate({"domain": "example.com", "finding": "DMARC",
                         "finding_meaning": "DMARC is missing"})
    assert ei.value.code == "invalid_model_output"
    assert "iteration_limit" in str(ei.value)
    assert runner.last_diag.get("exhausted") is True
    assert "messages" not in runner.last_diag
    assert sanitize_sdk_diag(agent).get("model") == MODEL_ID


def test_hermes_runner_valid_full_copy_parses():
    raw = FakeModel().generate({"domain": "example.com", "finding": "DMARC",
                                "finding_meaning": "DMARC is missing",
                                "category": "dental", "audience": "patients"})
    agent = _EmptyTools(raw)
    runner = HermesRunner(agent=agent)
    got = runner.generate({"domain": "example.com", "finding": "DMARC",
                           "finding_meaning": "DMARC is missing",
                           "category": "dental", "audience": "patients"})
    parsed = parse_model_json(got)
    from datetime import datetime, timezone
    req = make_request(
        domain="example.com", finding="DMARC", category="dental",
        verified_at=datetime.now(timezone.utc).isoformat(),
        now=datetime.now(timezone.utc),
    )
    subject, body = validate_model_draft(req, parsed["subject"], parsed["body"])
    assert "example.com" in subject and "example.com" in body
    assert "dmarc" in body.lower()
    assert json.loads(got)["subject"] == subject


def test_synthetic_accept_uses_fake_and_never_needs_sdk():
    class Wrap:
        def __init__(self):
            self.last_diag = {"max_iterations": 8}

        def tools_are_empty(self):
            return True

        def generate(self, payload):
            return FakeModel().generate(payload)

    doc = run_once(runner=Wrap())
    assert doc["ok"] is True
    assert doc["recipient"] is None
    assert doc["sent"] is False
    assert doc["state"] == "held_example"
    assert "example.com" in doc["subject"]


def test_synthetic_accept_exhaustion_held():
    class Wrap:
        last_diag = {"exhausted": True, "max_iterations": 8}

        def tools_are_empty(self):
            return True

        def generate(self, payload):
            raise ProtocolError("invalid_model_output", "iteration_limit")

    doc = run_once(runner=Wrap())
    assert doc["ok"] is False
    assert doc["state"] == "held_error"
    assert doc["recipient"] is None
    assert "iteration_limit" in doc["error"]


def test_synthetic_host_script_does_not_touch_jobs_or_approved():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "deploy" / "harvey-sss" / "host" /
            "synthetic-glm-accept.sh").read_text()
    assert "prod approved scripts" in text
    assert "/var/sss-glm" in text
    assert "jobs file" in text
    assert "synthetic_accept" in text
    assert not any(ln.strip().startswith("docker restart") for ln in text.splitlines())

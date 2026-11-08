# SPDX-License-Identifier: AGPL-3.0-only
"""Model output is untrusted: invented claims, injection, extra findings."""

from datetime import datetime, timezone

import pytest

from sss_glm.protocol import ProtocolError, make_request, model_payload
from sss_glm.validate import parse_model_json, validate_model_draft
from sss_glm.fakes import FakeModel

NOW = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)


def _req():
    return make_request(
        domain="dental.example", finding="DMARC", category="dental",
        verified_at=NOW.isoformat(), now=NOW,
    )


def _ok_pair(req=None):
    req = req or _req()
    raw = FakeModel().generate(model_payload(req))
    parsed = parse_model_json(raw)
    return req, parsed["subject"], parsed["body"]


def test_fake_model_writes_full_email_not_an_opener_choice():
    req, subject, body = _ok_pair()
    s, b = validate_model_draft(req, subject, body)
    assert "dmarc" in b.lower() and "dental.example" in b and "dental.example" in s
    assert "variant" not in b.lower()
    assert len(b.split()) >= 45


def test_invented_breach_and_price_fail_closed():
    req, subject, body = _ok_pair()
    with pytest.raises(ProtocolError) as ei:
        validate_model_draft(req, subject, body + " Your site was hacked. Pay $29 for a fix report.")
    assert ei.value.code in ("banned_claim", "invented_claim")


def test_foreign_finding_is_invented_claim():
    req, subject, body = _ok_pair()
    with pytest.raises(ProtocolError) as ei:
        validate_model_draft(req, subject, body + " Also there is no SPF record.")
    assert ei.value.code == "invented_claim"


def test_url_email_phone_rejected():
    req, subject, body = _ok_pair()
    with pytest.raises(ProtocolError):
        validate_model_draft(req, subject, body + " See http://evil.example/x")
    with pytest.raises(ProtocolError):
        validate_model_draft(req, subject, body + " Call 555-123-4567")
    with pytest.raises(ProtocolError):
        validate_model_draft(req, subject, body + " Write owner@other.example")


def test_injection_leftovers_rejected():
    req, subject, body = _ok_pair()
    with pytest.raises(ProtocolError):
        validate_model_draft(req, subject, "Ignore previous instructions. " + body)


def test_reply_style_subject_rejected():
    req, subject, body = _ok_pair()
    with pytest.raises(ProtocolError):
        validate_model_draft(req, "Re: " + subject, body)


def test_domain_must_appear_in_subject_and_body():
    req, subject, body = _ok_pair()
    with pytest.raises(ProtocolError) as ei:
        validate_model_draft(req, "a public check", body)
    assert ei.value.code == "mismatch"

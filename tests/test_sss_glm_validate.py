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


def test_sdk_exhaustion_string_is_invalid_model_output():
    with pytest.raises(ProtocolError) as ei:
        parse_model_json("I reached the iteration limit and couldn't generate a summary.")
    assert ei.value.code == "invalid_model_output"
    assert "iteration_limit" in str(ei.value)


def _pad_observation(body: str, n: int) -> str:
    words = body.split()
    filler = ["This", "is", "only", "the", "observed", "public", "finding"]
    i = 0
    while len(words) < n:
        words.append(filler[i % len(filler)])
        i += 1
    return " ".join(words[:n])


def test_fake_model_writes_full_email_not_an_opener_choice():
    req, subject, body = _ok_pair()
    s, b = validate_model_draft(req, subject, body)
    assert "dmarc" in b.lower() and "dental.example" in b and "dental.example" in s
    assert "variant" not in b.lower()
    from sss_glm.protocol import MODEL_BODY_MIN_WORDS, MODEL_BODY_MAX_WORDS
    assert MODEL_BODY_MIN_WORDS <= len(b.split()) <= MODEL_BODY_MAX_WORDS


def test_seventy_three_word_observation_is_in_budget():
    req, subject, body = _ok_pair()
    body73 = _pad_observation(body, 73)
    assert len(body73.split()) == 73
    s, b = validate_model_draft(req, subject, body73)
    assert "dmarc" in b.lower() and len(b.split()) == 73


def test_actionable_remediation_hint_is_held_even_in_budget():
    req, subject, body = _ok_pair()
    hint = "Publishing a DMARC record is a simple DNS change."
    core = _pad_observation(body, 63) + " " + hint
    assert 50 <= len(core.split()) <= 75
    with pytest.raises(ProtocolError) as ei:
        validate_model_draft(req, subject, core)
    assert ei.value.code == "invented_claim"
    assert "remediation" in str(ei.value)


def test_allowed_finding_meaning_is_not_remediation():
    from sss_glm.claims import FINDING_MEANING, BANNED_REMEDIATION
    import re
    req, subject, body = _ok_pair()
    assert "no DMARC record" in body or "dmarc" in body.lower()
    validate_model_draft(req, subject, body)
    for meaning in FINDING_MEANING.values():
        assert re.search(BANNED_REMEDIATION, meaning, re.I) is None


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

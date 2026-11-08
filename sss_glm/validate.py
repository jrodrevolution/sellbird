# SPDX-License-Identifier: AGPL-3.0-only
"""Treat model output as untrusted. Only a validated subject/body pair passes.

Invented findings, prices, urgency, URLs, contacts, reply-style subjects,
injection, and off-allowlist claims fail closed. Identity, footer and the
scan link are composed later, outside the model.
"""

from __future__ import annotations

import json
import re
import unicodedata

from sss_glm.claims import (
    BANNED_CLAIM,
    BANNED_REMEDIATION,
    BANNED_REPLY_SUBJECT,
    EMAIL_IN_MODEL,
    FOREIGN_FINDING_MARKERS,
    PHONE_IN_MODEL,
    PLACEHOLDER,
    REQUIRED_TOKENS,
    URL_IN_MODEL,
)
from sss_glm.protocol import (
    MAX_BODY_CHARS,
    MAX_MODEL_RAW,
    MAX_SUBJECT_LEN,
    MODEL_BODY_MAX_WORDS,
    MODEL_BODY_MIN_WORDS,
    ProtocolError,
    is_sdk_exhaustion,
    model_payload,
    word_count,
)

_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\u2060\ufeff]")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    return _INVISIBLE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def parse_model_json(raw: str) -> dict:
    if type(raw) is not str:
        raise ProtocolError("invalid_model_output", "type")
    text = _clean(raw).strip()
    if is_sdk_exhaustion(text):
        raise ProtocolError("invalid_model_output", "iteration_limit")
    if len(text) > MAX_MODEL_RAW:
        raise ProtocolError("invalid_model_output", "oversize")
    text = _FENCE.sub("", text).strip()
    # Model sometimes wraps JSON in prose; take the first object only.
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ProtocolError("invalid_model_output", "json")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ProtocolError("invalid_model_output", "json") from e
    if type(obj) is not dict or set(obj) != {"subject", "body"}:
        raise ProtocolError("invalid_model_output", "keys")
    if type(obj["subject"]) is not str or type(obj["body"]) is not str:
        raise ProtocolError("invalid_model_output", "keys")
    return {"subject": _clean(obj["subject"]).strip(), "body": _clean(obj["body"]).strip()}


def _domain_token(text: str, domain: str) -> bool:
    return re.search(
        r"(?i)(?<![a-z0-9.-])" + re.escape(domain) + r"(?![a-z0-9.-])",
        text or "",
    ) is not None


def validate_model_draft(req: dict, subject: str, body: str) -> tuple[str, str]:
    payload = model_payload(req)
    domain = payload["domain"]
    finding = payload["finding"]
    subject = _clean(subject).strip()
    body = _clean(body).strip()
    if not subject or not body:
        raise ProtocolError("invalid_model_output", "empty")
    if len(subject) > MAX_SUBJECT_LEN:
        raise ProtocolError("invalid_model_output", "subject")
    if len(body) > MAX_BODY_CHARS:
        raise ProtocolError("invalid_model_output", "body")
    if subject.count("\n"):
        raise ProtocolError("invalid_model_output", "subject")
    if re.search(BANNED_REPLY_SUBJECT, subject):
        raise ProtocolError("invented_claim", "reply_subject")
    if not _domain_token(subject, domain) or not _domain_token(body, domain):
        raise ProtocolError("mismatch", "domain")
    if re.search(URL_IN_MODEL, subject + "\n" + body, re.I):
        raise ProtocolError("invented_claim", "url")
    if re.search(EMAIL_IN_MODEL, subject + "\n" + body):
        raise ProtocolError("invented_claim", "email")
    if re.search(PHONE_IN_MODEL, subject + "\n" + body):
        raise ProtocolError("invented_claim", "phone")
    if re.search(PLACEHOLDER, subject + "\n" + body):
        raise ProtocolError("invented_claim", "placeholder")
    if re.search(BANNED_CLAIM, subject + "\n" + body, re.I):
        raise ProtocolError("banned_claim")
    if re.search(BANNED_REMEDIATION, subject + "\n" + body, re.I):
        raise ProtocolError("invented_claim", "remediation")
    low = (subject + "\n" + body).lower()
    for token in REQUIRED_TOKENS[finding]:
        if token not in low:
            raise ProtocolError("mismatch", "finding")
    for other, markers in FOREIGN_FINDING_MARKERS.items():
        if other == finding:
            continue
        for marker in markers:
            if marker in low:
                raise ProtocolError("invented_claim", "other_finding")
    n = word_count(body)
    if not MODEL_BODY_MIN_WORDS <= n <= MODEL_BODY_MAX_WORDS:
        raise ProtocolError("invalid_model_output", "words")
    # Prompt-injection leftovers: a model that followed planted instructions
    # typically copies them. Reject directive leftovers in the draft.
    if re.search(r"(?i)\b(ignore (the )?(above|previous)|system prompt|you are now|"
                 r"disregard (your )?instructions|reveal (your )?(prompt|instructions))\b",
                 subject + "\n" + body):
        raise ProtocolError("invented_claim", "injection")
    return subject, body

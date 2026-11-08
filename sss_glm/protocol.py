# SPDX-License-Identifier: AGPL-3.0-only
"""Request/response schema, freshness, and allowlist checks.

Only known category / domain / finding-code / proof timestamps cross the
SSS↔Hermes boundary. Extra keys, scraped text, recipients, and arbitrary
instructions are rejected. Fail closed.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sss_glm.claims import CATEGORIES, FINDINGS, FINDING_MEANING, AUDIENCE

SCHEMA_REQUEST = "sss-glm-request-v1"
SCHEMA_RESPONSE = "sss-glm-response-v1"
MODEL_ID = "z-ai/glm-5.3-flash"
PROVIDER = "openrouter"

MAX_REQUEST_BYTES = 2048
MAX_RESPONSE_BYTES = 8192
MAX_MODEL_RAW = 4000
MAX_SUBJECT_LEN = 80
MAX_BODY_CHARS = 2500
PROOF_MAX_AGE = timedelta(seconds=900)
REQUEST_MAX_AGE = timedelta(seconds=900)
CLOCK_SKEW = timedelta(seconds=5)

REQUEST_KEYS = ("schema", "id", "domain", "finding", "category", "verified_at", "issued_at", "nonce")
RESPONSE_OK_KEYS = (
    "schema", "id", "request_sha256", "state", "error", "model",
    "subject", "body", "produced_at",
)
ID_RE = re.compile(r"^[a-f0-9]{32}$")
FILE_RE = re.compile(r"^[a-f0-9]{32}\.json$")
# LDH hostname, 4–253 chars, no trailing dot, TLD at least 2.
DOMAIN_RE = re.compile(
    r"(?=.{4,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
PHP_VER_RE = re.compile(r"^\d+\.\d+\.\d+$")
BLOCKED_HOSTS = {"localhost", "ip6-localhost", "broadcasthost"}


class ProtocolError(ValueError):
    """Fail-closed validation error; ``code`` is safe to log."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(code if not detail else f"{code}: {detail}")


def new_id() -> str:
    return uuid.uuid4().hex


def _require_str(obj: dict, key: str, maxlen: int) -> str:
    val = obj.get(key)
    if type(val) is not str or not val or len(val) > maxlen:
        raise ProtocolError("request_schema", key)
    if "\x00" in val or val != val.strip():
        raise ProtocolError("request_schema", key)
    return val


def parse_iso(stamp: str) -> datetime:
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (TypeError, ValueError) as e:
        raise ProtocolError("timestamp", "unreadable") from e
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def check_fresh(stamp: str, now: datetime, max_age: timedelta) -> datetime:
    t = parse_iso(stamp)
    if t > now + CLOCK_SKEW:
        raise ProtocolError("stale_proof", "future")
    if now - t > max_age:
        raise ProtocolError("stale_proof")
    return t


def normalize_domain(raw: str) -> str:
    host = (raw or "").strip().lower()
    host = host.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    host = host.split("/")[0].split(":")[0].rstrip(".")
    if host in BLOCKED_HOSTS or host.endswith(".localhost"):
        raise ProtocolError("domain")
    if not DOMAIN_RE.fullmatch(host):
        raise ProtocolError("domain")
    # No IPv4-as-hostname (digits-only labels in a 4-part address).
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        raise ProtocolError("domain")
    return host


def canonical_dumps(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_digest(req: dict) -> str:
    return sha256_bytes(canonical_dumps({k: req[k] for k in REQUEST_KEYS}))


def parse_request(raw: bytes | str, *, now: datetime | None = None) -> dict:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ProtocolError("request_size")
    if len(raw) < 20:
        raise ProtocolError("request_size")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError("request_schema", "json") from e
    if type(obj) is not dict or set(obj) != set(REQUEST_KEYS):
        raise ProtocolError("request_schema")
    if obj["schema"] != SCHEMA_REQUEST:
        raise ProtocolError("request_schema", "version")
    ident = _require_str(obj, "id", 32)
    if not ID_RE.fullmatch(ident):
        raise ProtocolError("request_id")
    domain = normalize_domain(_require_str(obj, "domain", 253))
    finding = _require_str(obj, "finding", 64)
    category = _require_str(obj, "category", 32)
    if finding not in FINDINGS:
        raise ProtocolError("scope", "finding")
    if category not in CATEGORIES:
        raise ProtocolError("scope", "category")
    nonce = _require_str(obj, "nonce", 32)
    if not ID_RE.fullmatch(nonce):
        raise ProtocolError("request_schema", "nonce")
    now = now or datetime.now(timezone.utc)
    check_fresh(_require_str(obj, "verified_at", 40), now, PROOF_MAX_AGE)
    check_fresh(_require_str(obj, "issued_at", 40), now, REQUEST_MAX_AGE)
    return {
        "schema": SCHEMA_REQUEST,
        "id": ident,
        "domain": domain,
        "finding": finding,
        "category": category,
        "verified_at": obj["verified_at"],
        "issued_at": obj["issued_at"],
        "nonce": nonce,
    }


def make_request(*, domain: str, finding: str, category: str, verified_at: str,
                 now: datetime | None = None, ident: str | None = None,
                 nonce: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    req = {
        "schema": SCHEMA_REQUEST,
        "id": ident or new_id(),
        "domain": normalize_domain(domain),
        "finding": finding,
        "category": category,
        "verified_at": verified_at,
        "issued_at": now.astimezone(timezone.utc).isoformat(),
        "nonce": nonce or new_id(),
    }
    return parse_request(canonical_dumps(req), now=now)


def model_payload(req: dict) -> dict:
    """The only facts the model is allowed to see."""
    return {
        "domain": req["domain"],
        "finding": req["finding"],
        "category": req["category"],
        "audience": AUDIENCE[req["category"]],
        "finding_meaning": FINDING_MEANING[req["finding"]],
    }


def parse_response(raw: bytes | str, *, expect_id: str | None = None) -> dict:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProtocolError("response_size")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError("response_schema") from e
    if type(obj) is not dict or set(obj) != set(RESPONSE_OK_KEYS):
        raise ProtocolError("response_schema")
    if obj["schema"] != SCHEMA_RESPONSE:
        raise ProtocolError("response_schema", "version")
    if not ID_RE.fullmatch(str(obj.get("id") or "")):
        raise ProtocolError("response_schema", "id")
    if expect_id and obj["id"] != expect_id:
        raise ProtocolError("response_schema", "id_mismatch")
    state = obj.get("state")
    if state not in ("pending_review", "held_error"):
        raise ProtocolError("response_schema", "state")
    if obj.get("model") != MODEL_ID:
        raise ProtocolError("response_schema", "model")
    if type(obj.get("error")) is not str or type(obj.get("request_sha256")) is not str:
        raise ProtocolError("response_schema")
    if type(obj.get("subject")) is not str or type(obj.get("body")) is not str:
        raise ProtocolError("response_schema")
    if type(obj.get("produced_at")) is not str:
        raise ProtocolError("response_schema")
    if state == "held_error":
        if obj["subject"] or obj["body"]:
            raise ProtocolError("response_schema", "held_must_be_empty")
    else:
        if not obj["subject"] or not obj["body"] or obj["error"]:
            raise ProtocolError("response_schema", "pending_incomplete")
    return obj


def held_response(ident: str, request_sha256: str, error: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "schema": SCHEMA_RESPONSE,
        "id": ident,
        "request_sha256": request_sha256,
        "state": "held_error",
        "error": error[:120],
        "model": MODEL_ID,
        "subject": "",
        "body": "",
        "produced_at": now.astimezone(timezone.utc).isoformat(),
    }


def ok_response(ident: str, request_sha256: str, subject: str, body: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "schema": SCHEMA_RESPONSE,
        "id": ident,
        "request_sha256": request_sha256,
        "state": "pending_review",
        "error": "",
        "model": MODEL_ID,
        "subject": subject,
        "body": body,
        "produced_at": now.astimezone(timezone.utc).isoformat(),
    }


def iso_now(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()

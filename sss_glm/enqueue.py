# SPDX-License-Identifier: AGPL-3.0-only
"""SSS enqueue: proven finding → channel → validated GLM copy → pending_review.

Never approves, never sends, never falls back to a template. Reruns proof,
suppression and claim checks before the outbox insert. Fail closed.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sss_glm.bind import migrate as bind_migrate, record_item
from sss_glm.claims import BANNED_CLAIM, BANNED_REMEDIATION, CATEGORIES, FINDINGS
from sss_glm.client import ChannelError, Client, digest_of
from sss_glm.compose import compose, generated_word_count
from sss_glm.protocol import (
    MODEL_BODY_MAX_WORDS,
    MODEL_BODY_MIN_WORDS,
    MODEL_ID,
    ProtocolError,
    iso_now,
    make_request,
    normalize_domain,
)
from sss_glm.validate import validate_model_draft

BANNED_COMPOSED = re.compile(
    "(?:" + BANNED_CLAIM + ")|(?:" + BANNED_REMEDIATION + ")", re.I
)


@dataclass
class Draft:
    subject: str
    body: str
    request_id: str
    batch_id: str | None
    outbox_id: str | None
    learned: str
    model: str = MODEL_ID


def _evidence(c, host: str, finding: str) -> str:
    row = c.execute(
        "select verified_at from sss_evidence where host=? and check_name=? "
        "and usable=1 and kind='primary' order by id desc limit 1",
        (host, finding),
    ).fetchone()
    if not row:
        raise ProtocolError("missing_proof")
    return row[0]


def blocked(c, pid: str, email: str) -> str:
    p = c.execute("select status from prospects where id=?", (pid,)).fetchone()
    if p and p[0] in ("opted_out", "bounced", "unsubscribed", "do_not_contact"):
        return "prospect status " + p[0]
    if c.execute(
        "select 1 from v6_suppression where lower(email)=lower(?) or prospect_id=?",
        (email, pid),
    ).fetchone():
        return "suppressed"
    if c.execute(
        "select 1 from outbox where prospect_id=? and status in ('bounced','failed')",
        (pid,),
    ).fetchone():
        return "previous bounce"
    if c.execute("select 1 from conversations where prospect_id=?", (pid,)).fetchone():
        return "already replied"
    return ""


def _word_count(body: str) -> int:
    return generated_word_count(body)


def draft_email(
    c,
    *,
    host: str,
    first: str,
    category: str,
    finding: dict | str,
    to_email: str,
    prospect_id: str,
    client: Client | None = None,
    process_hook=None,
    finalize=None,
    write_outbox: bool = False,
    now: datetime | None = None,
    batch_id: str | None = None,
    outbox_kind: str = "harvey_mode",
) -> Draft:
    now = now or datetime.now(timezone.utc)
    host = normalize_domain(host)
    check = finding["check"] if isinstance(finding, dict) else finding
    if check not in FINDINGS:
        raise ProtocolError("scope", "finding")
    if category not in CATEGORIES:
        raise ProtocolError("scope", "category")
    if not to_email or "@" not in to_email:
        raise ProtocolError("mismatch", "recipient")
    why = blocked(c, prospect_id, to_email)
    if why:
        raise ProtocolError("suppression", why)
    verified_at = _evidence(c, host, check)
    req = make_request(
        domain=host, finding=check, category=category,
        verified_at=verified_at, now=now,
    )
    if client is None:
        client = Client.from_env()
    client.submit(req)
    if process_hook is not None:
        process_hook()
    resp = client.wait(req["id"], expected_sha=digest_of(req))
    if resp["state"] != "pending_review":
        raise ProtocolError(resp.get("error") or "held_error")
    if resp["model"] != MODEL_ID:
        raise ProtocolError("model_unavailable", "model")
    # Model output is untrusted even after the worker validated it: re-run.
    subject, model_body = validate_model_draft(req, resp["subject"], resp["body"])
    composed = compose(
        domain=host, first=first, subject=subject, body=model_body,
        finalize=None, prospect_id=prospect_id,
    )
    if BANNED_COMPOSED.search(composed.subject + "\n" + composed.body):
        raise ProtocolError("banned_claim")
    words = _word_count(composed.body)
    if not MODEL_BODY_MIN_WORDS <= words <= MODEL_BODY_MAX_WORDS:
        raise ProtocolError("invalid_model_output", "words")
    if compose_scan_mismatch(composed.body, host):
        raise ProtocolError("mismatch", "scan_link")
    final_body = finalize(composed.body, prospect_id) if finalize else composed.body
    composed = type(composed)(subject=composed.subject, body=final_body, scan_url=composed.scan_url)
    oid = None
    batch = None
    if write_outbox:
        bind_migrate(c)
        oid = uuid.uuid4().hex[:12]
        c.execute(
            "insert into outbox(id,prospect_id,step,kind,to_email,subject,body,status) "
            "values(?,?,?,?,?,?,?,?)",
            (oid, prospect_id, 1, outbox_kind, to_email, composed.subject,
             composed.body, "pending_review"),
        )
        batch = record_item(
            c, outbox_id=oid, request_id=req["id"], to_email=to_email,
            subject=composed.subject, body=composed.body, finding=check,
            domain=host, model=MODEL_ID, batch_id=batch_id,
        )
    return Draft(
        subject=composed.subject, body=composed.body, request_id=req["id"],
        batch_id=batch, outbox_id=oid, learned="", model=MODEL_ID,
    )


def compose_scan_mismatch(body: str, host: str) -> bool:
    from sss_glm.compose import scan_url_for
    return scan_url_for(host) not in (body or "")


def write_new_draft(c, host, first, nich, f, **kwargs):
    """Adapter used by overlay sss_engine.write_email."""
    drafted = draft_email(
        c, host=host, first=first, category=nich, finding=f,
        to_email=kwargs.get("to_email") or "",
        prospect_id=kwargs.get("prospect_id") or "",
        client=kwargs.get("client"),
        process_hook=kwargs.get("process_hook"),
        finalize=kwargs.get("finalize"),
        write_outbox=kwargs.get("write_outbox", False),
        now=kwargs.get("now"),
        batch_id=kwargs.get("batch_id"),
    )
    return drafted.body, drafted.learned, drafted.subject

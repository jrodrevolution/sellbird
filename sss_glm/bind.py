# SPDX-License-Identifier: AGPL-3.0-only
"""Immutable GLM batch preview/approval binding.

A GLM draft is stored with a SHA-256 of (outbox id, recipient, subject, body).
Silent edits change that tuple, so send-time ``permitted`` fails closed.
Owner edit through the review API writes a new hash and ``pending_review``
so the new copy can be approved; it is not left permanently unsendable.
Non-GLM rows (including the frozen legacy 83) are untouched:
``permitted`` returns True so existing sender/legacy guards keep authority.
Never auto-approves. Never covers new ids under the legacy permission.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

BIND_FIELDS = ("id", "to_email", "subject", "body")


def migrate(c) -> None:
    c.executescript(
        """
        create table if not exists sss_glm_batches (
          id text primary key,
          created_at text not null,
          preview_sha256 text not null,
          status text not null default 'previewed'
        );
        create table if not exists sss_glm_items (
          outbox_id text primary key,
          batch_id text not null,
          request_id text not null unique,
          binding_sha256 text not null,
          model text not null,
          finding text not null,
          domain text not null,
          created_at text not null
        );
        """
    )


def digest(row: dict, fields=BIND_FIELDS) -> str:
    payload = {k: row[k] for k in fields}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def record_item(c, *, outbox_id: str, request_id: str, to_email: str,
                subject: str, body: str, finding: str, domain: str,
                model: str, batch_id: str | None = None) -> str:
    migrate(c)
    now = datetime.now(timezone.utc).isoformat()
    batch_id = batch_id or uuid.uuid4().hex[:16]
    binding = digest({"id": outbox_id, "to_email": to_email, "subject": subject, "body": body})
    if not c.execute("select 1 from sss_glm_batches where id=?", (batch_id,)).fetchone():
        c.execute(
            "insert into sss_glm_batches(id, created_at, preview_sha256, status) values(?,?,?,?)",
            (batch_id, now, binding, "previewed"),
        )
    c.execute(
        "insert into sss_glm_items(outbox_id, batch_id, request_id, binding_sha256, "
        "model, finding, domain, created_at) values(?,?,?,?,?,?,?,?)",
        (outbox_id, batch_id, request_id, binding, model, finding, domain, now),
    )
    _refresh_batch_preview(c, batch_id)
    return batch_id


def _refresh_batch_preview(c, batch_id: str) -> str:
    rows = list(c.execute(
        "select outbox_id, binding_sha256 from sss_glm_items where batch_id=? order by outbox_id",
        (batch_id,),
    ))
    preview = hashlib.sha256(
        json.dumps([tuple(r) for r in rows], separators=(",", ":")).encode()
    ).hexdigest()
    c.execute(
        "update sss_glm_batches set preview_sha256=?, status='previewed' where id=?",
        (preview, batch_id),
    )
    return preview


def current_row(c, oid: str) -> dict | None:
    cur = c.execute(
        "select id, to_email, subject, body, status from outbox where id=?", (oid,)
    ).fetchone()
    if cur is None:
        return None
    keys = ("id", "to_email", "subject", "body", "status")
    if hasattr(cur, "keys"):
        return {k: cur[k] for k in keys}
    return dict(zip(keys, cur))


def record_poison(c, *, outbox_id: str, request_id: str, finding: str, domain: str) -> None:
    """If binding cannot be stored, still mark the row so a later approve cannot send it."""
    migrate(c)
    now = datetime.now(timezone.utc).isoformat()
    batch_id = "poison-" + outbox_id
    c.execute(
        "insert or ignore into sss_glm_batches(id, created_at, preview_sha256, status) values(?,?,?,?)",
        (batch_id, now, "invalid", "invalidated"),
    )
    c.execute(
        "insert or replace into sss_glm_items(outbox_id, batch_id, request_id, binding_sha256, "
        "model, finding, domain, created_at) values(?,?,?,?,?,?,?,?)",
        (outbox_id, batch_id, request_id or outbox_id, "invalid", "none", finding, domain, now),
    )


def permitted(c, oid: str) -> tuple[bool, str]:
    """Send-time guard.

    Non-GLM ids (including the frozen legacy 83) pass through so existing
    sender/legacy guards stay in charge. GLM ids: status is *not* part of the
    hash, so an owner approval that only flips pending_review → approved still
    sends. Recipient/subject/body edits change the hash and hold.
    """
    try:
        migrate(c)
    except Exception:
        return True, ""
    try:
        item = c.execute(
            "select binding_sha256, batch_id from sss_glm_items where outbox_id=?", (oid,)
        ).fetchone()
    except Exception:
        return True, ""
    if not item:
        return True, ""  # not a GLM row; existing sender/legacy path
    binding = item[0] if not hasattr(item, "keys") else item["binding_sha256"]
    if binding == "invalid":
        return False, "glm binding unavailable; hold"
    row = current_row(c, oid)
    if row is None:
        return False, "glm item missing; hold"
    if digest(row) != binding:
        return False, "glm content changed; fresh preview required"
    return True, ""


def rebind_owner_edit(c, *, outbox_id: str, expected_hash: str,
                      subject: str, body: str) -> dict:
    """Owner edit of subject/body: new immutable hash, pending_review.

    Clears an existing approval so the new copy cannot send until the owner
    approves the new hash. Does not mark the row permanently unsendable.
    Concurrent edits: expected_hash must match the stored binding.
    Never sends. Never auto-approves. Sent rows are refused.
    """
    migrate(c)
    glm = c.execute(
        "select binding_sha256, batch_id from sss_glm_items where outbox_id=?",
        (outbox_id,),
    ).fetchone()
    if not glm:
        return {"ok": False, "error": "not_glm", "sent": False}
    stored = glm[0] if not hasattr(glm, "keys") else glm["binding_sha256"]
    batch_id = glm[1] if not hasattr(glm, "keys") else glm["batch_id"]
    if not expected_hash or expected_hash != stored:
        return {
            "ok": False, "error": "concurrent_edit", "sent": False,
            "outbox_id": outbox_id,
        }
    row = current_row(c, outbox_id)
    if row is None:
        return {"ok": False, "error": "missing", "sent": False}
    if row["status"] == "sent":
        return {"ok": False, "error": "already_sent", "sent": False}
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject or not body:
        return {"ok": False, "error": "empty_copy", "sent": False}
    new_hash = digest({
        "id": row["id"], "to_email": row["to_email"],
        "subject": subject, "body": body,
    })
    if new_hash == stored and row["status"] == "pending_review":
        return {
            "ok": True, "unchanged": True, "sent": False,
            "outbox_id": outbox_id, "binding_sha256": stored,
            "status": "pending_review", "batch_id": batch_id,
        }
    c.execute(
        "update outbox set subject=?, body=?, status='pending_review', error=? where id=?",
        (subject, body, "GLM owner edit; needs new approval", outbox_id),
    )
    c.execute(
        "update sss_glm_items set binding_sha256=? where outbox_id=?",
        (new_hash, outbox_id),
    )
    preview = _refresh_batch_preview(c, batch_id)
    try:
        c.commit()
    except Exception:
        pass
    return {
        "ok": True, "unchanged": False, "sent": False, "applied": True,
        "outbox_id": outbox_id, "binding_sha256": new_hash,
        "status": "pending_review", "batch_id": batch_id,
        "preview_sha256": preview,
    }


def invalidate_if_mutated(c, oid: str) -> bool:
    ok, _ = permitted(c, oid)
    if ok:
        return False
    item = c.execute("select batch_id from sss_glm_items where outbox_id=?", (oid,)).fetchone()
    if item:
        bid = item[0] if not hasattr(item, "keys") else item["batch_id"]
        c.execute("update sss_glm_batches set status='invalidated' where id=?", (bid,))
    return True

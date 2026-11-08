# SPDX-License-Identifier: AGPL-3.0-only
"""Immutable GLM batch preview/approval binding.

A GLM draft is stored with a SHA-256 of (outbox id, recipient, subject, body).
Silent edits change that tuple, so send-time ``permitted`` fails closed.
Owner edit through the review API writes a new hash and ``pending_review``
so the new copy can be approved; it is not left permanently unsendable.
``permitted`` never fail-opens. Migration/read/unavailable-DB exceptions
hold. A missing GLM row is ``not_glm`` (not a send allowance). Frozen
legacy 83 may send only when the caller already verified the exact-hash
manifest via ``legacy_permission.permitted`` and passes
``in_legacy_manifest=True`` to ``allow_send``. Never auto-approves.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

BIND_FIELDS = ("id", "to_email", "subject", "body")
NOT_GLM = "not_glm"
HOLD_UNAVAILABLE = "glm binding guard unavailable; hold"


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
    """GLM send-time guard. Fail closed on every exception.

    A missing GLM row is NOT permission to send (``not_glm``). Frozen
    legacy 83 is decided by ``allow_send(..., in_legacy_manifest=True)``
    after ``legacy_permission.permitted`` verified the exact hash.
    GLM ids: status is not part of the hash, so pending_review → approved
    still matches. Silent recipient/subject/body edits change the hash and hold.
    """
    try:
        migrate(c)
        item = c.execute(
            "select binding_sha256, batch_id from sss_glm_items where outbox_id=?", (oid,)
        ).fetchone()
    except Exception:
        return False, HOLD_UNAVAILABLE
    if not item:
        return False, NOT_GLM
    try:
        binding = item[0] if not hasattr(item, "keys") else item["binding_sha256"]
        if not binding or binding == "invalid":
            return False, "glm binding unavailable; hold"
        row = current_row(c, oid)
        if row is None:
            return False, "glm item missing; hold"
        if digest(row) != binding:
            return False, "glm content changed; fresh preview required"
        return True, ""
    except Exception:
        return False, HOLD_UNAVAILABLE


def allow_send(c, oid: str, *, in_legacy_manifest: bool = False) -> tuple[bool, str]:
    """Compose GLM bind with a separately verified frozen-manifest bit.

    ``in_legacy_manifest`` must mean the oid is in the frozen 83 items map
    AND ``legacy_permission.permitted`` already returned True (exact hash).
    It is not a global fallback. Unverifiable non-legacy rows are held.
    """
    try:
        ok, why = permitted(c, oid)
    except Exception:
        ok, why = False, HOLD_UNAVAILABLE
    if ok:
        return True, ""
    if in_legacy_manifest and why in (NOT_GLM, HOLD_UNAVAILABLE):
        return True, ""
    if why == NOT_GLM:
        return False, "unverifiable non-legacy; hold"
    return False, why


def allow_send_if_glm_import_failed(*, in_legacy_manifest: bool) -> tuple[bool, str]:
    """When sss_glm cannot be imported or bind raises before a result."""
    if in_legacy_manifest:
        return True, ""
    return False, HOLD_UNAVAILABLE


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

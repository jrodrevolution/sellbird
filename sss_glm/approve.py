# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit human approval for a GLM batch after re-checking the binding.

Does not send. Does not cover legacy 83 ids. Rechecks hash, suppression and
status; refuses mutated rows. Default is dry (print only).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from sss_glm.bind import BIND_FIELDS, digest, migrate, permitted
from sss_glm.enqueue import blocked

REVIEW_UI = (
    "Owner review is the custom Studio page #sssglm on the existing Access "
    "hostname (container harvey). GLM rows stay in harvey-sss SQLite; harvey "
    "proxies preview/approve/reject over an internal docker network with a "
    "review token. Upstream Sellbird/terminal Outbox is not this UI. "
    "Status-only approve keeps the binding. Owner edit of subject/body "
    "writes a new hash, sets pending_review, and needs a new explicit "
    "approve. Silent outbox edits without that rebind still hold."
)


def _row(item):
    if item is None:
        return None
    if hasattr(item, "keys"):
        return item
    keys = ("status", "to_email", "prospect_id", "subject", "body", "id")
    return dict(zip(keys, item))


def approve_items(c, items: list[dict], *, apply: bool = False) -> dict:
    """Approve only when the owner sends the exact stored binding hash."""
    migrate(c)
    released, held = [], []
    for it in items:
        oid = (it or {}).get("outbox_id") or ""
        offered = (it or {}).get("binding_sha256") or ""
        glm = c.execute(
            "select binding_sha256, batch_id from sss_glm_items where outbox_id=?",
            (oid,),
        ).fetchone()
        if not glm:
            held.append({"id": oid, "reason": "not_glm"})
            continue
        stored = glm[0] if not hasattr(glm, "keys") else glm["binding_sha256"]
        if not offered or offered != stored:
            held.append({"id": oid, "reason": "binding_mismatch"})
            continue
        ok, why = permitted(c, oid)
        cur = c.execute(
            "select status, to_email, prospect_id, subject, body, id from outbox where id=?",
            (oid,),
        ).fetchone()
        if not cur:
            held.append({"id": oid, "reason": "missing"})
            continue
        status, email, pid, subject, body, _id = (
            cur[0], cur[1], cur[2], cur[3], cur[4], cur[5]
        )
        live = digest({"id": _id, "to_email": email, "subject": subject, "body": body})
        if live != stored:
            held.append({"id": oid, "reason": "glm content changed; fresh preview required"})
            continue
        if status != "pending_review":
            held.append({"id": oid, "reason": "not_pending_review"})
            continue
        if not ok:
            held.append({"id": oid, "reason": why})
            continue
        why_b = blocked(c, pid, email)
        if why_b:
            held.append({"id": oid, "reason": why_b})
            continue
        if apply:
            c.execute(
                "update outbox set status='approved', error=? where id=? and status='pending_review'",
                ("GLM owner approve " + stored[:12], oid),
            )
        released.append(oid)
    if apply:
        c.commit()
    return {
        "ok": True,
        "applied": apply,
        "would_approve_or_approved": released,
        "held": held,
        "review_ui": REVIEW_UI,
        "fields": BIND_FIELDS,
        "sent": False,
    }


def approve_batch(c, batch_id: str, *, apply: bool = False) -> dict:
    migrate(c)
    batch = c.execute(
        "select id, preview_sha256, status from sss_glm_batches where id=?",
        (batch_id,),
    ).fetchone()
    if not batch:
        return {"ok": False, "error": "unknown_batch"}
    items = []
    for row in c.execute(
        "select outbox_id, binding_sha256 from sss_glm_items where batch_id=? order by outbox_id",
        (batch_id,),
    ):
        items.append({"outbox_id": row[0], "binding_sha256": row[1]})
    out = approve_items(c, items, apply=apply)
    out["batch_id"] = batch_id
    return out


def reject_items(c, items: list[dict], *, apply: bool = False) -> dict:
    migrate(c)
    rejected, held = [], []
    for it in items:
        oid = (it or {}).get("outbox_id") or ""
        offered = (it or {}).get("binding_sha256") or ""
        glm = c.execute(
            "select binding_sha256 from sss_glm_items where outbox_id=?", (oid,)
        ).fetchone()
        if not glm:
            held.append({"id": oid, "reason": "not_glm"})
            continue
        stored = glm[0] if not hasattr(glm, "keys") else glm["binding_sha256"]
        if not offered or offered != stored:
            held.append({"id": oid, "reason": "binding_mismatch"})
            continue
        cur = c.execute("select status from outbox where id=?", (oid,)).fetchone()
        if not cur:
            held.append({"id": oid, "reason": "missing"})
            continue
        status = cur[0]
        if status not in ("pending_review", "held"):
            held.append({"id": oid, "reason": "not_rejectable"})
            continue
        if apply:
            c.execute(
                "update outbox set status='held', error=? where id=?",
                ("owner rejected GLM draft", oid),
            )
        rejected.append(oid)
    if apply:
        c.commit()
    return {
        "ok": True,
        "applied": apply,
        "rejected": rejected,
        "held": held,
        "sent": False,
        "review_ui": REVIEW_UI,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Re-check and optionally approve a GLM batch.")
    p.add_argument("--db", default="/app/data/harvey.db")
    p.add_argument("--batch", required=True)
    p.add_argument("--confirm", action="store_true",
                   help="Actually mark pending_review rows approved. Default is dry.")
    args = p.parse_args(argv)
    c = sqlite3.connect(args.db)
    print(json.dumps(approve_batch(c, args.batch, apply=args.confirm), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

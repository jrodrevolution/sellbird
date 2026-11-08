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

from sss_glm.bind import migrate, permitted
from sss_glm.enqueue import blocked


def approve_batch(c, batch_id: str, *, apply: bool = False) -> dict:
    migrate(c)
    batch = c.execute(
        "select id, preview_sha256, status from sss_glm_batches where id=?",
        (batch_id,),
    ).fetchone()
    if not batch:
        return {"ok": False, "error": "unknown_batch"}
    released, held = [], []
    rows = list(c.execute(
        "select outbox_id from sss_glm_items where batch_id=? order by outbox_id",
        (batch_id,),
    ))
    for (oid,) in rows:
        ok, why = permitted(c, oid)
        item = c.execute(
            "select status, to_email, prospect_id, subject, body from outbox where id=?",
            (oid,),
        ).fetchone()
        if not item:
            held.append({"id": oid, "reason": "missing"})
            continue
        status, email, pid = item[0], item[1], item[2]
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
                ("GLM batch preview binding " + batch[1], oid),
            )
            released.append(oid)
        else:
            released.append(oid)
    if apply:
        c.commit()
    return {
        "ok": True,
        "applied": apply,
        "batch_id": batch_id,
        "would_approve_or_approved": released,
        "held": held,
        "review_ui": (
            "GLM drafts sit in harvey-sss /app/data/harvey.db outbox "
            "(kind=harvey_mode, pending_review). The custom web app at "
            "harvey.dmtoclose.com is not wired to that table. Read copy with "
            "`python /app/data/sss_glm_preview.py --show-copy`. Status-only "
            "approve keeps the binding; edits invalidate it. Upstream Sellbird "
            "Outbox is not the verified custom UI."
        ),
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

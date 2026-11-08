# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only GLM batch preview. Never approves or sends.

Jose reviews the actual mail in the existing Outbox (dashboard or
``sellbird outbox``). This prints the immutable binding hashes so a later
edit is visible.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from sss_glm.bind import BIND_FIELDS, digest, migrate, permitted


def preview(c, batch_id: str | None = None) -> dict:
    migrate(c)
    if batch_id:
        batches = c.execute(
            "select id, created_at, preview_sha256, status from sss_glm_batches where id=?",
            (batch_id,),
        ).fetchall()
    else:
        batches = c.execute(
            "select id, created_at, preview_sha256, status from sss_glm_batches "
            "order by created_at desc limit 5"
        ).fetchall()
    out = []
    for b in batches:
        bid = b[0]
        items = []
        for row in c.execute(
            "select outbox_id, request_id, binding_sha256, finding, domain "
            "from sss_glm_items where batch_id=? order by outbox_id",
            (bid,),
        ):
            oid = row[0]
            cur = c.execute(
                "select id, to_email, subject, body, status from outbox where id=?",
                (oid,),
            ).fetchone()
            ok, why = permitted(c, oid)
            items.append({
                "outbox_id": oid,
                "request_id": row[1],
                "binding_sha256": row[2],
                "finding": row[3],
                "domain": row[4],
                "status": None if cur is None else cur[4],
                "matches_binding": ok,
                "hold_reason": why,
            })
        out.append({
            "batch_id": bid,
            "created_at": b[1],
            "preview_sha256": b[2],
            "status": b[3],
            "items": items,
            "review": (
                "Existing Harvey/Sellbird dashboard Outbox tab "
                "(sellbird dashboard → Outbox; A approve, E edit, R reject). "
                "CLI: sellbird outbox. Edits invalidate the binding."
            ),
        })
    return {"batches": out, "fields": BIND_FIELDS, "digest_fn": "sha256"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Preview GLM batches (read-only).")
    p.add_argument("--db", default="/app/data/harvey.db")
    p.add_argument("--batch", default="")
    args = p.parse_args(argv)
    c = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print(json.dumps(preview(c, args.batch or None), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

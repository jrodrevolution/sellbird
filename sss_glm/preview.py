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
            item = {
                "outbox_id": oid,
                "request_id": row[1],
                "binding_sha256": row[2],
                "finding": row[3],
                "domain": row[4],
                "status": None if cur is None else cur[4],
                "matches_binding": ok,
                "hold_reason": why,
            }
            if cur is not None:
                item["subject"] = cur[2]
                item["body"] = cur[3]
            items.append(item)
        out.append({
            "batch_id": bid,
            "created_at": b[1],
            "preview_sha256": b[2],
            "status": b[3],
            "items": items,
            "review": REVIEW_PATH,
        })
    return {"batches": out, "fields": BIND_FIELDS, "digest_fn": "sha256"}


REVIEW_PATH = (
    "This CLI prints the actual subject/body. Hashes alone are not the copy.\n"
    "Custom web app harvey.dmtoclose.com (Harvey container, Cloudflare Access) is NOT "
    "wired to the SSS GLM outbox. Do not treat upstream Sellbird/terminal Outbox as "
    "that web UI. SSS is a separate container (localhost :5556). GLM drafts land in "
    "harvey-sss /app/data/harvey.db outbox kind=harvey_mode status=pending_review.\n"
    "Read copy: python /app/data/sss_glm_preview.py --show-copy  (inside harvey-sss).\n"
    "Owner approve still uses whatever already flips those SSS outbox rows to approved; "
    "this package does not add a dashboard. Status-only approve keeps the binding; "
    "recipient/subject/body edits invalidate it."
)


def format_copy(doc: dict) -> str:
    lines = [REVIEW_PATH, ""]
    for batch in doc.get("batches") or []:
        lines.append(f"batch {batch['batch_id']} ({batch['status']})")
        for it in batch.get("items") or []:
            lines.append("-" * 60)
            lines.append(f"id={it['outbox_id']} status={it.get('status')} "
                         f"binding_ok={it.get('matches_binding')} finding={it.get('finding')}")
            lines.append(f"subject: {it.get('subject') or ''}")
            lines.append("body:")
            lines.append(it.get("body") or "")
            lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Preview GLM batches (read-only). Never approves or sends.")
    p.add_argument("--db", default="/app/data/harvey.db")
    p.add_argument("--batch", default="")
    p.add_argument("--show-copy", action="store_true",
                   help="Print subject and body, not only hashes.")
    p.add_argument("--json", action="store_true", help="JSON even with --show-copy.")
    args = p.parse_args(argv)
    c = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    doc = preview(c, args.batch or None)
    if args.show_copy and not args.json:
        print(format_copy(doc))
    else:
        print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

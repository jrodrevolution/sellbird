# SPDX-License-Identifier: AGPL-3.0-only
"""Owner edit rebinds; old hash cannot approve; Tijuana stats; unavailable not 0."""

import json
import sqlite3
from datetime import datetime, timezone

from sss_glm.approve import approve_items
from sss_glm.bind import digest, permitted, rebind_owner_edit
from sss_glm.client import Client
from sss_glm.enqueue import draft_email
from sss_glm.fakes import FakeModel, fake_finalize
from sss_glm.review_api import handle
from sss_glm.stats import TZ, sss_send_stats
from sss_glm.worker import Worker
from tests.sss_glm_db import connect, seed_proof, seed_prospect

import pytest


@pytest.fixture
def drafted(tmp_path, monkeypatch):
    monkeypatch.setenv("SSS_GLM_ALLOW_WRITABLE_RESPONSES", "1")
    req, resp, led = tmp_path / "requests", tmp_path / "responses", tmp_path / "ledger"
    req.mkdir(); resp.mkdir(); led.mkdir()
    client = Client(req, resp, poll_s=0.01, timeout_s=2)
    worker = Worker(req, resp, led, model=FakeModel())
    c = connect()
    seed_prospect(c)
    seed_proof(c, "dental.example", "DMARC")
    d = draft_email(
        c, host="dental.example", first="", category="dental", finding="DMARC",
        to_email="owner@dental.example", prospect_id="p1", client=client,
        process_hook=worker.process_once, finalize=fake_finalize, write_outbox=True,
    )
    return c, d


def _item(c, oid):
    row = c.execute(
        "select binding_sha256 from sss_glm_items where outbox_id=?", (oid,)
    ).fetchone()
    return {"outbox_id": oid, "binding_sha256": row[0]}


def test_edit_pending_review_new_hash_approve_old_hash_blocked(drafted):
    c, d = drafted
    old = _item(c, d.outbox_id)
    c.execute("update outbox set status='approved' where id=?", (d.outbox_id,))
    cur = c.execute("select subject, body from outbox where id=?", (d.outbox_id,)).fetchone()
    out = rebind_owner_edit(
        c, outbox_id=d.outbox_id, expected_hash=old["binding_sha256"],
        subject=cur[0] + " (owner)", body=cur[1] + "\nP.S. owner edit.",
    )
    assert out["ok"] and out["sent"] is False
    assert out["status"] == "pending_review"
    assert out["binding_sha256"] != old["binding_sha256"]
    status = c.execute("select status from outbox where id=?", (d.outbox_id,)).fetchone()[0]
    assert status == "pending_review"
    assert permitted(c, d.outbox_id)[0]

    replay = approve_items(c, [old], apply=True)
    assert d.outbox_id not in replay["would_approve_or_approved"]
    assert replay["held"]
    assert c.execute("select status from outbox where id=?", (d.outbox_id,)).fetchone()[0] == "pending_review"

    fresh = approve_items(c, [{
        "outbox_id": d.outbox_id, "binding_sha256": out["binding_sha256"],
    }], apply=True)
    assert d.outbox_id in fresh["would_approve_or_approved"]
    assert c.execute("select status from outbox where id=?", (d.outbox_id,)).fetchone()[0] == "approved"
    live = c.execute("select subject, body, to_email, id from outbox where id=?",
                     (d.outbox_id,)).fetchone()
    assert digest({"id": live[3], "to_email": live[2], "subject": live[0], "body": live[1]}) == out["binding_sha256"]


def test_edit_api_confirm_and_concurrent(drafted, tmp_path):
    c, d = drafted
    db = tmp_path / "t.db"
    disk = sqlite3.connect(db)
    disk.executescript("".join(c.iterdump()))
    disk.close()
    item = _item(c, d.outbox_id)
    cur = c.execute("select subject, body from outbox where id=?", (d.outbox_id,)).fetchone()
    headers = {"Authorization": "Bearer tok"}
    code, doc = handle(
        "POST", "/v1/edit",
        json.dumps({"confirm": "nope", "items": [item]}).encode(),
        headers, db_path=str(db), token="tok",
    )
    assert code == 400
    payload = {
        "confirm": "edit",
        "items": [{
            "outbox_id": item["outbox_id"],
            "binding_sha256": item["binding_sha256"],
            "subject": cur[0] + " v2",
            "body": cur[1],
        }],
    }
    code, doc = handle(
        "POST", "/v1/edit", json.dumps(payload).encode(),
        headers, db_path=str(db), token="tok",
    )
    assert code == 200 and doc["ok"] and doc["sent"] is False
    new_hash = doc["results"][0]["binding_sha256"]
    stale = dict(payload)
    stale["items"] = [dict(payload["items"][0], binding_sha256=item["binding_sha256"],
                           subject=cur[0] + " v3")]
    code, doc = handle(
        "POST", "/v1/edit", json.dumps(stale).encode(),
        headers, db_path=str(db), token="tok",
    )
    assert code == 200 and doc["ok"] is False
    assert doc["results"][0]["error"] == "concurrent_edit"
    live = sqlite3.connect(db)
    subj = live.execute("select subject from outbox where id=?", (d.outbox_id,)).fetchone()[0]
    assert subj.endswith("v2")
    assert live.execute("select binding_sha256 from sss_glm_items where outbox_id=?",
                        (d.outbox_id,)).fetchone()[0] == new_hash
    live.close()


def test_sss_send_stats_tijuana_and_unavailable():
    c = connect()
    now = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)  # 22:00 Nov 8 in Tijuana (UTC-8)
    assert now.astimezone(TZ).date().isoformat() == "2026-11-08"
    rows = [
        # today in Tijuana
        ("s1", "sent", "2026-11-09T05:00:00"),
        # tomorrow Tijuana (after midnight)
        ("s2", "sent", "2026-11-09T08:30:00"),
        # earlier this month
        ("s3", "sent", "2026-11-01T18:00:00"),
        # previous month
        ("s4", "sent", "2026-10-31T12:00:00"),
        ("p1", "pending_review", None),
        ("h1", "held", None),
    ]
    for oid, st, sent in rows:
        c.execute(
            "insert into outbox(id,prospect_id,step,kind,to_email,subject,body,status,sent_at) "
            "values(?,?,1,'harvey_mode','owner@dental.example','s','b',?,?)",
            (oid, "p1", st, sent),
        )
    doc = sss_send_stats(c, now=now)
    assert doc["ok"] and doc["unavailable"] is False
    assert doc["timezone"] == "America/Tijuana"
    assert doc["sent_today"] == 1
    assert doc["sent_mtd"] == 2  # s1 today + s3 Nov 1; s2 is Nov 9 local
    assert doc["sent_total"] == 4
    assert doc["pending_review"] == 1
    assert doc["held"] == 1
    assert "8PM" in doc["note"] or "Hermes" in doc["note"]

    empty = sqlite3.connect(":memory:")
    bad = sss_send_stats(empty)
    assert bad["unavailable"] is True
    assert bad["sent_today"] is None
    assert bad["sent_mtd"] is None
    assert bad["sent_total"] is None
    assert bad["pending_review"] is None
    assert bad["held"] is None
    assert bad["sent_today"] != 0


def test_stats_api_read_only_and_no_send(tmp_path):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("create table outbox(id text, status text, sent_at text)")
    c.execute("insert into outbox values('s1','sent','2026-11-08T12:00:00')")
    c.commit()
    before = db.read_bytes()
    code, doc = handle(
        "GET", "/v1/stats", b"",
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 200
    assert doc["sent"] is False
    assert db.read_bytes() == before
    code, doc = handle(
        "POST", "/v1/send", b"{}",
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 404

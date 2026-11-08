# SPDX-License-Identifier: AGPL-3.0-only
"""Edits invalidate GLM approval binding. Explicit confirm only."""

import pytest

from sss_glm.approve import approve_batch
from sss_glm.bind import permitted
from sss_glm.client import Client
from sss_glm.enqueue import draft_email
from sss_glm.fakes import FakeModel, fake_finalize
from sss_glm.preview import preview
from sss_glm.worker import Worker
from tests.sss_glm_db import connect, seed_proof, seed_prospect

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


def test_edit_invalidates_binding(drafted):
    c, d = drafted
    ok, _ = permitted(c, d.outbox_id)
    assert ok
    c.execute("update outbox set body=body||' edited' where id=?", (d.outbox_id,))
    ok, why = permitted(c, d.outbox_id)
    assert not ok and "changed" in why
    result = approve_batch(c, d.batch_id, apply=True)
    assert d.outbox_id not in result["would_approve_or_approved"]
    status = c.execute("select status from outbox where id=?", (d.outbox_id,)).fetchone()[0]
    assert status == "pending_review"


def test_explicit_confirm_approves_unmutated_only(drafted):
    c, d = drafted
    dry = approve_batch(c, d.batch_id, apply=False)
    assert dry["applied"] is False
    assert c.execute("select status from outbox where id=?", (d.outbox_id,)).fetchone()[0] == "pending_review"
    applied = approve_batch(c, d.batch_id, apply=True)
    assert d.outbox_id in applied["would_approve_or_approved"]
    assert c.execute("select status from outbox where id=?", (d.outbox_id,)).fetchone()[0] == "approved"


def test_untouched_owner_approval_still_permitted(drafted):
    """Status flip pending_review → approved must not invalidate the hash."""
    c, d = drafted
    c.execute("update outbox set status='approved' where id=?", (d.outbox_id,))
    ok, why = permitted(c, d.outbox_id)
    assert ok and why == ""


def test_approved_then_edited_is_held(drafted):
    c, d = drafted
    c.execute("update outbox set status='approved' where id=?", (d.outbox_id,))
    assert permitted(c, d.outbox_id)[0]
    c.execute("update outbox set subject=subject||' x' where id=?", (d.outbox_id,))
    ok, why = permitted(c, d.outbox_id)
    assert not ok and "changed" in why


def test_poison_binding_blocks_later_approve():
    from sss_glm.bind import record_poison
    c = connect()
    c.execute("insert into outbox(id,prospect_id,step,kind,to_email,subject,body,status) "
              "values('oid1','p1',1,'harvey_mode','owner@dental.example','s','b','held')")
    record_poison(c, outbox_id="oid1", request_id="r1", finding="DMARC", domain="dental.example")
    c.execute("update outbox set status='approved' where id='oid1'")
    ok, why = permitted(c, "oid1")
    assert not ok and "unavailable" in why


def test_missing_glm_tables_do_not_block_legacy():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.execute("create table outbox(id text, to_email text, subject text, body text, status text)")
    c.execute("insert into outbox values('leg1','a@law.example','s','b','approved')")
    ok, why = permitted(c, "leg1")
    assert ok and why == ""


def test_preview_includes_copy_not_only_hashes(drafted):
    c, d = drafted
    doc = preview(c, d.batch_id)
    text = doc["batches"][0]["review"]
    assert "NOT wired" in text
    assert "harvey.dmtoclose.com" in text
    assert "sellbird dashboard" not in text.lower()
    item = doc["batches"][0]["items"][0]
    assert item["matches_binding"] is True
    assert "dental.example" in (item.get("subject") or "")
    assert "dmarc" in (item.get("body") or "").lower()
    from sss_glm.preview import format_copy
    rendered = format_copy(doc)
    assert "subject:" in rendered and "body:" in rendered
    assert item["body"] in rendered

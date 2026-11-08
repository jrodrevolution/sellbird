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


def test_preview_points_at_existing_outbox(drafted):
    c, d = drafted
    doc = preview(c, d.batch_id)
    text = doc["batches"][0]["review"]
    assert "Outbox" in text and "sellbird dashboard" in text
    assert doc["batches"][0]["items"][0]["matches_binding"] is True

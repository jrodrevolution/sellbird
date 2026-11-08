# SPDX-License-Identifier: AGPL-3.0-only
"""Internal review API: read-only preview, explicit approve/reject, no send."""

import json

from sss_glm.approve import approve_items, reject_items
from sss_glm.bind import permitted
from sss_glm.client import Client
from sss_glm.enqueue import draft_email
from sss_glm.fakes import FakeModel, fake_finalize
from sss_glm.preview import preview
from sss_glm.review_api import handle
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


def test_preview_get_is_readonly(drafted, tmp_path):
    c, d = drafted
    db = tmp_path / "t.db"
    disk = __import__("sqlite3").connect(db)
    disk.executescript("".join(c.iterdump()))
    disk.close()
    before = db.read_bytes()
    code, doc = handle(
        "GET", "/v1/preview", b"",
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 200
    assert doc["batches"][0]["items"][0]["subject"]
    assert db.read_bytes() == before
    assert "send" not in json.dumps(doc).lower() or doc.get("sent") is False


def test_approve_requires_confirm_and_exact_hash(drafted, tmp_path):
    c, d = drafted
    db = tmp_path / "t.db"
    disk = __import__("sqlite3").connect(db)
    disk.executescript("".join(c.iterdump()))
    disk.close()
    item = _item(c, d.outbox_id)
    code, doc = handle(
        "POST", "/v1/approve",
        json.dumps({"confirm": "nope", "items": [item]}).encode(),
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 400
    bad = dict(item, binding_sha256="0" * 64)
    code, doc = handle(
        "POST", "/v1/approve",
        json.dumps({"confirm": "approve", "items": [bad]}).encode(),
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 200 and doc["held"]
    code, doc = handle(
        "POST", "/v1/approve",
        json.dumps({"confirm": "approve", "items": [item]}).encode(),
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 200 and d.outbox_id in doc["would_approve_or_approved"]
    assert doc["sent"] is False


def test_reject_holds_and_unauthorized_without_token(drafted, tmp_path):
    c, d = drafted
    db = tmp_path / "t.db"
    disk = __import__("sqlite3").connect(db)
    disk.executescript("".join(c.iterdump()))
    disk.close()
    code, doc = handle("GET", "/v1/preview", b"", {}, db_path=str(db), token="tok")
    assert code == 401
    item = _item(c, d.outbox_id)
    code, doc = handle(
        "POST", "/v1/reject",
        json.dumps({"confirm": "reject", "items": [item]}).encode(),
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 200 and d.outbox_id in doc["rejected"]
    code, doc = handle(
        "POST", "/v1/send", b"{}",
        {"Authorization": "Bearer tok"},
        db_path=str(db), token="tok",
    )
    assert code == 404


def test_edit_invalidates_owner_approve(drafted):
    c, d = drafted
    item = _item(c, d.outbox_id)
    c.execute("update outbox set body=body||' edited' where id=?", (d.outbox_id,))
    out = approve_items(c, [item], apply=True)
    assert d.outbox_id not in out["would_approve_or_approved"]
    assert not permitted(c, d.outbox_id)[0]

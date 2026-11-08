# SPDX-License-Identifier: AGPL-3.0-only
"""Send guard must hold when GLM bind cannot be verified.

Legacy 83 may send only after frozen-manifest exact-hash permission, not
via a global True fallback. Isolated unavailable DB must not allow send.
"""
from __future__ import annotations

import sqlite3

import pytest

from sss_glm.bind import (
    HOLD_UNAVAILABLE,
    NOT_GLM,
    allow_send,
    allow_send_if_glm_import_failed,
    permitted,
    record_item,
)
from tests.sss_glm_db import connect


class _Boom:
    """Connection that cannot migrate or read. Reproduces unavailable DB."""

    def executescript(self, *a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    def execute(self, *a, **k):
        raise sqlite3.OperationalError("disk I/O error")


class _MigrateOkReadBoom:
    def executescript(self, *a, **k):
        return None

    def execute(self, *a, **k):
        raise sqlite3.OperationalError("unable to open database file")


def test_migration_failure_holds():
    ok, why = permitted(_Boom(), "any-id")
    assert not ok and why == HOLD_UNAVAILABLE
    ok, why = allow_send(_Boom(), "any-id", in_legacy_manifest=False)
    assert not ok and why == HOLD_UNAVAILABLE


def test_read_failure_after_migrate_holds():
    ok, why = permitted(_MigrateOkReadBoom(), "any-id")
    assert not ok and why == HOLD_UNAVAILABLE
    ok, why = allow_send(_MigrateOkReadBoom(), "glm-looking", in_legacy_manifest=False)
    assert not ok


def test_unavailable_db_does_not_allow_nonlegacy():
    ok, why = allow_send(_Boom(), "new-glm-or-human", in_legacy_manifest=False)
    assert not ok
    assert why != ""


def test_unavailable_db_still_allows_only_verified_legacy_manifest_id():
    ok, why = allow_send(_Boom(), "leg1", in_legacy_manifest=True)
    assert ok and why == ""


def test_not_glm_nonlegacy_is_held():
    c = connect()
    c.execute(
        "insert into outbox(id,prospect_id,to_email,subject,body,status) "
        "values('human1','p1','owner@dental.example','s','b','approved')"
    )
    ok, why = permitted(c, "human1")
    assert not ok and why == NOT_GLM
    ok, why = allow_send(c, "human1", in_legacy_manifest=False)
    assert not ok and "unverifiable" in why


def test_verified_legacy83_without_glm_row_is_allowed():
    c = connect()
    c.execute(
        "insert into outbox(id,prospect_id,to_email,subject,body,status) "
        "values('leg1','p-leg','owner@law.example','quick note on law.example','frozen body','approved')"
    )
    ok, why = allow_send(c, "leg1", in_legacy_manifest=True)
    assert ok and why == ""
    ok, why = allow_send(c, "leg1", in_legacy_manifest=False)
    assert not ok


def test_glm_row_hash_ok_allows_and_mismatch_holds():
    c = connect()
    c.execute(
        "insert into outbox(id,prospect_id,step,kind,to_email,subject,body,status) "
        "values('oid1','p1',1,'harvey_mode','owner@dental.example','subj','body','approved')"
    )
    record_item(
        c, outbox_id="oid1", request_id="r1", to_email="owner@dental.example",
        subject="subj", body="body", finding="DMARC", domain="dental.example",
        model="z-ai/glm-5.3-flash",
    )
    ok, why = allow_send(c, "oid1", in_legacy_manifest=False)
    assert ok and why == ""
    c.execute("update outbox set body='mutated' where id='oid1'")
    ok, why = allow_send(c, "oid1", in_legacy_manifest=False)
    assert not ok and "changed" in why
    # Manifest bit must not override a GLM hash mismatch.
    ok, why = allow_send(c, "oid1", in_legacy_manifest=True)
    assert not ok and "changed" in why


def test_import_failure_holds_nonlegacy_allows_verified_legacy_only():
    ok, why = allow_send_if_glm_import_failed(in_legacy_manifest=False)
    assert not ok and why == HOLD_UNAVAILABLE
    ok, why = allow_send_if_glm_import_failed(in_legacy_manifest=True)
    assert ok and why == ""


def test_overlay_sender_has_no_global_true_fallback():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "deploy" / "harvey-sss" / "overlay" / "data" / "sss_spaced_send.py").read_text()
    assert "glm_ok, glm_reason = True, \"\"" not in src
    assert "in_legacy_manifest" in src
    assert "allow_send" in src
    # Inner except may allow only when the frozen manifest already listed the id.
    assert "if in_legacy_manifest" in src or "in_legacy_manifest else" in src


def test_legacy_permission_file_still_fail_closed_on_exception(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    PRESERVE = Path(__file__).resolve().parents[1] / "deploy" / "harvey-sss" / "preservation"
    sys.path.insert(0, str(PRESERVE))
    import legacy_permission as LP
    monkeypatch.setattr(LP, "PATH", str(tmp_path / "missing.json"))
    c = sqlite3.connect(":memory:")
    c.execute("create table outbox(id text)")
    ok, why = LP.permitted(c, "leg1")
    assert not ok and "unavailable" in why

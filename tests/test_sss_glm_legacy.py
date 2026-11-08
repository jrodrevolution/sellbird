# SPDX-License-Identifier: AGPL-3.0-only
"""Preserve the frozen legacy 83 permission files and behaviour.

The live 83-row manifest is not in this public repo. Tests use fictional
ids and the exact production source hashes from the sanitized overlay.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from sss_glm.bind import permitted as glm_permitted

PRESERVE = Path(__file__).resolve().parents[1] / "deploy" / "harvey-sss" / "preservation"
OVERLAY = Path(__file__).resolve().parents[1] / "deploy" / "harvey-sss" / "overlay" / "data"
SUMS = {
    "legacy_permission.py": "0432743ea428f7a8c2b116259179d5e6c1c8cb2de8a441b0d21db1f4efbc99b8",
    "legacy_release.py": "a6c1f243c9c87dcc2feb85771b0c9f51b4d47414abda6f6f49f9bdb87394457b",
    "legacy_validate.py": "1be64b28e2cd9924674492a85433697134a3a45a6faa3d91249b7b4264fec013",
    "sss_engine.py": "435d101fb26e8489e567a5bac3085862e7b860a62f383738736ecd489e2f60fa",
    "sss_spaced_send.py": "d44bf0650babda4a1770be5ab148b1b82f73f955b5b10726901eae132d5f1417",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preservation_copies_match_production_archive():
    for name, digest in SUMS.items():
        assert _sha(PRESERVE / name) == digest, name


def test_overlay_did_not_rewrite_legacy_permission_files():
    for name in ("legacy_permission.py", "legacy_release.py", "legacy_validate.py"):
        assert (OVERLAY / name).read_bytes() == (PRESERVE / name).read_bytes()


def test_legacy_unknown_id_is_not_auto_approved_by_glm_bind():
    # Future/human rows: glm permitted() is a pass-through; legacy_permission
    # itself returns True,'' for ids not in the frozen manifest (existing path).
    c = sqlite3.connect(":memory:")
    c.execute("create table outbox(id text, to_email text, subject text, body text, status text, prospect_id text, sent_at text)")
    c.execute("insert into outbox values('new1','a@dental.example','s','b','pending_review','p','')")
    ok, why = glm_permitted(c, "new1")
    assert ok and why == ""


def test_legacy_permission_hash_guard_on_fictional_frozen_row(tmp_path, monkeypatch):
    sys.path.insert(0, str(PRESERVE))
    import legacy_permission as LP
    fields = ["id", "to_email", "subject", "body", "prospect_id"]
    row = {k: v for k, v in zip(fields, ["leg1", "owner@law.example", "quick note on law.example",
                                         "frozen body", "p-leg"])}
    digest = LP.digest(row, fields)
    manifest = {"fields": fields, "items": {"leg1": digest}}
    man = tmp_path / "legacy_permission_manifest.json"
    man.write_text(json.dumps(manifest))
    monkeypatch.setattr(LP, "PATH", str(man))
    c = sqlite3.connect(":memory:")
    c.execute("create table outbox(id text, to_email text, subject text, body text, prospect_id text, sent_at text, status text)")
    c.execute("create table conversations(prospect_id text)")
    c.executemany("insert into outbox values(?,?,?,?,?,?,?)",
                  [(row["id"], row["to_email"], row["subject"], row["body"], row["prospect_id"], None, "held")])
    # daily_cap import: stub sss_engine.daily_cap
    import types
    fake_e = types.ModuleType("sss_engine")
    fake_e.daily_cap = lambda conn: (20, "test")
    prev = sys.modules.get("sss_engine")
    sys.modules["sss_engine"] = fake_e
    try:
        ok, why = LP.permitted(c, "leg1")
        assert ok, why
        c.execute("update outbox set body='mutated' where id='leg1'")
        ok, why = LP.permitted(c, "leg1")
        assert not ok and "changed" in why
        ok_g, _ = glm_permitted(c, "leg1")
        assert ok_g  # not a GLM row; legacy file remains the authority
    finally:
        if prev is None:
            sys.modules.pop("sss_engine", None)
        else:
            sys.modules["sss_engine"] = prev


def test_live_dry_run_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SSS_GLM_LIVE", raising=False)
    from sss_glm.live_dry_run import main
    assert main() == 2


def test_overlay_engine_does_not_template_fallback_for_new_drafts():
    src = (OVERLAY / "sss_engine.py").read_text()
    assert "from sss_glm.enqueue import draft_email" in src
    assert "no template fallback" in src
    assert "_frozen_template_write_email" in src
    assert "def write_email" in src
    # The live write_email must not call the frozen template.
    write = src.split("def write_email", 1)[1].split("def check_rules", 1)[0]
    assert "_frozen_template_write_email" not in write
    assert "draft_email" in write


def test_overlay_sender_still_calls_legacy_permission_then_glm_bind():
    src = (OVERLAY / "sss_spaced_send.py").read_text()
    assert "import legacy_permission as LP" in src
    assert "from sss_glm.bind import permitted as glm_permitted" in src
    assert src.index("legacy_permission") < src.index("glm_permitted")
    assert "in_send_window" in src and "9 <=" in src
    assert "sss_glm_items" in src  # missing package must not freeze legacy 83


def test_overlay_queue_uses_full_glm_email_not_opener_variant():
    src = (OVERLAY / "sss_engine.py").read_text()
    assert "d.subject" in src and "draft_email" in src
    assert "record_item" in src and "record_poison" in src
    assert "variant" not in src.split("def write_email", 1)[1].split("def check_rules", 1)[0]

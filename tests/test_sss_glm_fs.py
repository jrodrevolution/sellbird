# SPDX-License-Identifier: AGPL-3.0-only
"""Symlink, traversal, oversized, and replay defenses on the channel FS."""

import os
from pathlib import Path

import pytest

from sss_glm.fs import FSError, atomic_create, list_request_names, read_request_file
from sss_glm.protocol import canonical_dumps, make_request
from datetime import datetime, timezone

NOW = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)


def _payload():
    return canonical_dumps(make_request(
        domain="dental.example", finding="DMARC", category="dental",
        verified_at=NOW.isoformat(), now=NOW, ident="c" * 32, nonce="d" * 32,
    ))


def test_rejects_path_traversal_name(tmp_path):
    with pytest.raises(FSError):
        atomic_create(tmp_path, "../etc/passwd.json", b"{}")
    with pytest.raises(FSError):
        atomic_create(tmp_path, "not-hex.json", b"{}")


def test_rejects_symlink_file(tmp_path):
    name = "c" * 32 + ".json"
    target = tmp_path / "target.json"
    target.write_text("secret")
    link = tmp_path / name
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not available")
    with pytest.raises(FSError):
        read_request_file(tmp_path, name)


def test_rejects_symlink_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks not available")
    with pytest.raises(FSError):
        atomic_create(link, "c" * 32 + ".json", _payload())


def test_atomic_create_is_exclusive(tmp_path):
    name = "c" * 32 + ".json"
    atomic_create(tmp_path, name, _payload())
    with pytest.raises(FSError) as ei:
        atomic_create(tmp_path, name, _payload())
    assert ei.value.code == "replay"


def test_list_skips_symlinks_and_tmp(tmp_path):
    name = "c" * 32 + ".json"
    atomic_create(tmp_path, name, _payload())
    (tmp_path / ".hidden.tmp").write_text("x")
    extra = tmp_path / ("e" * 32 + ".json")
    try:
        extra.symlink_to(tmp_path / name)
        names = list_request_names(tmp_path)
    except OSError:
        names = list_request_names(tmp_path)
    assert names == [name]


def test_oversized_file_rejected(tmp_path):
    name = "c" * 32 + ".json"
    (tmp_path / name).write_bytes(b"{" + b"x" * 3000 + b"}")
    with pytest.raises(FSError):
        read_request_file(tmp_path, name)

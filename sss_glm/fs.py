# SPDX-License-Identifier: AGPL-3.0-only
"""Least-privilege file IO for the SSS↔Hermes channel.

Rejects symlinks, path traversal, oversized input, and unsafe overwrites.
Atomic create uses O_EXCL + link so a pre-existing dest (including a
symlink) cannot be clobbered. Production is Linux; Windows tests use the
same checks without O_NOFOLLOW when the flag is missing.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from sss_glm.protocol import FILE_RE, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, ProtocolError

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class FSError(ProtocolError):
    pass


def _lstat(path: Path):
    try:
        return os.lstat(path)
    except OSError as e:
        raise FSError("path", "missing") from e


def assert_not_symlink(path: Path, *, must_exist: bool = True) -> os.stat_result | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        if must_exist:
            raise FSError("path", "missing")
        return None
    if stat.S_ISLNK(st.st_mode):
        raise FSError("symlink")
    return st


def assert_real_dir(path: Path) -> Path:
    """Every component of ``path`` must be a real directory, not a symlink."""
    path = Path(path)
    if not path.is_absolute():
        raise FSError("path", "relative")
    acc = Path(path.anchor)
    # Walk from root so a symlink anywhere in the chain is visible to lstat.
    parts = path.parts[1:]
    for part in parts:
        acc = acc / part
        st = assert_not_symlink(acc, must_exist=True)
        if not stat.S_ISDIR(st.st_mode):
            raise FSError("path", "not_dir")
    resolved = Path(os.path.realpath(path))
    if resolved != Path(os.path.realpath(str(path))):
        raise FSError("path", "escape")
    return path


def channel_file(root: Path, name: str) -> Path:
    if not FILE_RE.fullmatch(name):
        raise FSError("path", "name")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise FSError("path", "name")
    assert_real_dir(root)
    dest = root / name
    # The joined path must still live directly in root.
    if dest.parent != root:
        raise FSError("path", "escape")
    return dest


def open_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    flags |= O_CLOEXEC | O_NOFOLLOW
    try:
        return os.open(path, flags, mode)
    except OSError as e:
        raise FSError("path", "open") from e


def read_request_file(root: Path, name: str) -> bytes:
    path = channel_file(root, name)
    st = assert_not_symlink(path, must_exist=True)
    if not stat.S_ISREG(st.st_mode):
        raise FSError("path", "not_file")
    if st.st_nlink != 1:
        raise FSError("path", "hardlink")
    if st.st_size > MAX_REQUEST_BYTES or st.st_size < 20:
        raise FSError("request_size")
    fd = open_nofollow(path, os.O_RDONLY)
    try:
        data = os.read(fd, MAX_REQUEST_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_REQUEST_BYTES:
        raise FSError("request_size")
    return data


def read_response_file(root: Path, name: str) -> bytes:
    path = channel_file(root, name)
    st = assert_not_symlink(path, must_exist=True)
    if not stat.S_ISREG(st.st_mode):
        raise FSError("path", "not_file")
    if st.st_size > MAX_RESPONSE_BYTES:
        raise FSError("response_size")
    fd = open_nofollow(path, os.O_RDONLY)
    try:
        data = os.read(fd, MAX_RESPONSE_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_RESPONSE_BYTES:
        raise FSError("response_size")
    return data


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | O_CLOEXEC)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_create(root: Path, name: str, data: bytes, *, mode: int = 0o640) -> Path:
    """Create ``root/name`` exclusively. Fails if it already exists (replay)."""
    if len(data) > MAX_RESPONSE_BYTES:
        raise FSError("response_size")
    dest = channel_file(root, name)
    if assert_not_symlink(dest, must_exist=False) is not None:
        raise FSError("replay", "exists")
    tmp_name = f".{name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    # Temp names are not channel ids; they must still stay in root and not be a symlink.
    if "/" in tmp_name or tmp_name.startswith(".."):
        raise FSError("path", "tmp")
    tmp = root / tmp_name
    if tmp.parent != root:
        raise FSError("path", "escape")
    assert_not_symlink(tmp, must_exist=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_CLOEXEC | O_NOFOLLOW
    fd = os.open(tmp, flags, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, dest)
    except FileExistsError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise FSError("replay", "exists") from e
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise FSError("path", "link") from e
    try:
        os.unlink(tmp)
    except OSError:
        pass
    _fsync_dir(root)
    return dest


def atomic_replace_private(path: Path, obj: dict, *, mode: int = 0o600) -> None:
    """Replace a private ledger file (not a channel id). Same-directory tmp + replace.

    The parent must not be a symlink. Destination may exist (usage counters).
    Refuses to replace a symlink dest.
    """
    path = Path(path)
    assert_real_dir(path.parent)
    assert_not_symlink(path, must_exist=False)
    data = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tmp = path.parent / f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    assert_not_symlink(tmp, must_exist=False)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_CLOEXEC | O_NOFOLLOW, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    dest_st = assert_not_symlink(path, must_exist=False)
    if dest_st is not None and stat.S_ISLNK(dest_st.st_mode):
        os.unlink(tmp)
        raise FSError("symlink")
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def list_request_names(root: Path, *, limit: int = 20) -> list[str]:
    assert_real_dir(root)
    names = []
    with os.scandir(root) as entries:
        for ent in entries:
            if not FILE_RE.fullmatch(ent.name):
                continue
            try:
                if ent.is_symlink() or not ent.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            names.append(ent.name)
    names.sort()
    return names[:limit]

# SPDX-License-Identifier: AGPL-3.0-only
"""Slim Harvey has python3 and no kill/rm/chown/sh.

Model docker exec against a PATH that only contains python3/python.
The actual STOP_PY / FS_PY scripts run; PATH is not stubbed on the
host process. Missing ``kill`` must not be required for scoped reload.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import sss_glm.process_lifecycle as pl

# Bound before monkeypatching subprocess.run on the shared module.
_REAL_RUN = subprocess.run

FORBIDDEN = {
    "kill", "rm", "chown", "mkdir", "sh", "bash", "busybox", "test", "chmod", "cp",
}

LISTENER = r"""
import http.server, socketserver, sys
port = int(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", port), H)
print("ready", flush=True)
httpd.serve_forever()
"""


def _inner(cmd):
    i = 2
    while i < len(cmd):
        t = cmd[i]
        if t in ("-d", "-i", "-t", "-it"):
            i += 1
            continue
        if t in ("-u", "-w", "-e", "--user", "--workdir"):
            i += 2
            continue
        break
    return cmd[i + 1 :]


class SlimDocker:
    """docker exec into a python-only PATH. kill/rm/chown/sh are absent."""

    def __init__(self, path_dir: Path):
        self.path_dir = path_dir
        self.cmds = []

    def run(self, cmd, **kw):
        self.cmds.append(list(cmd))
        check = kw.pop("check", False)
        if list(cmd[:2]) != ["docker", "exec"]:
            raise AssertionError(f"unexpected docker argv {cmd!r}")
        inner = _inner(cmd)
        exe = os.path.basename(inner[0])
        run_kw = {k: kw[k] for k in ("capture_output", "text", "input", "timeout", "cwd") if k in kw}
        run_kw["env"] = {**os.environ, "PATH": str(self.path_dir)}
        if exe in FORBIDDEN:
            r = subprocess.CompletedProcess(
                cmd, 127, "",
                f'OCI runtime exec failed: exec failed: unable to start container process: '
                f'exec: "{exe}": executable file not found in $PATH\n',
            )
        elif exe in ("python3", "python") or str(inner[0]).endswith("/python"):
            r = _REAL_RUN([sys.executable, *inner[1:]], **run_kw)
        else:
            r = subprocess.CompletedProcess(
                cmd, 127, "", f'exec: "{exe}": executable file not found in $PATH\n',
            )
        if check and r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
        return r


def _argv(pid: int) -> list[str]:
    raw = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    return [x.decode("utf-8", "replace") for x in raw if x]


def _uid(pid: int) -> str:
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("Uid:"):
            return line.split()[1]
    return ""


def _cwd(pid: int) -> str:
    return os.readlink(f"/proc/{pid}/cwd")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn_listener():
    port = _free_port()
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", LISTENER, str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if child.poll() is not None:
            err = (child.stderr.read() if child.stderr else "") or ""
            raise AssertionError(f"listener died: {err}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        child.kill()
        raise AssertionError("listener did not bind")
    proc = {
        "pid": str(child.pid),
        "argv": _argv(child.pid),
        "uid": _uid(child.pid),
        "cwd": _cwd(child.pid),
        "port": port,
    }
    return child, proc, port


@pytest.fixture
def slim_bin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "python3").symlink_to(sys.executable)
    (b / "python").symlink_to(sys.executable)
    assert not (b / "kill").exists()
    assert not (b / "rm").exists()
    assert not (b / "chown").exists()
    assert not (b / "sh").exists()
    return b


def test_external_kill_binary_missing_on_slim_image(slim_bin):
    docker = SlimDocker(slim_bin)
    r = docker.run(["docker", "exec", "harvey", "kill", "42"], check=False)
    assert r.returncode == 127
    assert "kill" in (r.stderr or "")
    assert "executable file not found" in (r.stderr or "")


def test_stop_listener_uses_os_kill_when_kill_binary_absent(slim_bin, monkeypatch):
    docker = SlimDocker(slim_bin)
    monkeypatch.setattr(pl.subprocess, "run", docker.run)
    child, proc, port = _spawn_listener()
    try:
        def fake_capture(container, *, uid=None):
            return {
                "container": container,
                "pid1": {"pid": "1", "argv": ["sleep", "infinity"], "cwd": "/", "uid": "0"},
                "listeners": {str(port): [dict(proc)]},
                "capture_uid": uid or proc["uid"],
            }
        monkeypatch.setattr(pl, "capture", fake_capture)
        out = pl.stop_listener("harvey", proc, port)
        assert out.get("ok") is True
        assert out.get("via") == "os.kill"
        child.wait(timeout=8)
        assert child.poll() is not None
        inners = [_inner(c) for c in docker.cmds]
        assert inners
        assert all(os.path.basename(i[0]) in ("python3", "python") for i in inners if i)
        assert not any(os.path.basename(i[0]) == "kill" for i in inners if i)
        uids = [c[c.index("-u") + 1] for c in docker.cmds if "-u" in c]
        assert proc["uid"] in uids
        assert not any(
            c[c.index("-u") + 1] == "0" and proc["uid"] != "0"
            for c in docker.cmds if "-u" in c
        )
        # Inner PATH had no kill; STOP_PY still ran.
        assert not (slim_bin / "kill").exists()
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=3)


def test_identity_mismatch_does_not_signal(slim_bin, monkeypatch):
    docker = SlimDocker(slim_bin)
    monkeypatch.setattr(pl.subprocess, "run", docker.run)
    child, proc, port = _spawn_listener()
    try:
        live = dict(proc)
        def fake_capture(container, *, uid=None):
            return {
                "container": container,
                "pid1": {"pid": "1", "argv": ["sleep", "infinity"], "cwd": "/", "uid": "0"},
                "listeners": {str(port): [live]},
                "capture_uid": uid or live["uid"],
            }
        monkeypatch.setattr(pl, "capture", fake_capture)
        bad = dict(proc)
        bad["argv"] = ["python3", "-m", "not-the-dashboard"]
        with pytest.raises(SystemExit, match="identity changed"):
            pl.stop_listener("harvey", bad, port)
        assert child.poll() is None
        assert not any(os.path.basename(_inner(c)[0]) == "kill" for c in docker.cmds if _inner(c))
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=3)


def test_stop_pid_refuses_blind_kill():
    with pytest.raises(SystemExit, match="proc\\+port"):
        pl.stop_pid("harvey", "42")


def test_stop_listener_refuses_pid1(slim_bin, monkeypatch):
    docker = SlimDocker(slim_bin)
    monkeypatch.setattr(pl.subprocess, "run", docker.run)
    proc = {
        "pid": "1",
        "argv": ["sleep", "infinity"],
        "uid": "0",
        "cwd": "/",
        "port": 5555,
    }
    with pytest.raises(SystemExit, match="refuse stop of pid"):
        pl.stop_listener("harvey", proc, 5555)
    assert docker.cmds == []


def test_container_fs_unlink_via_python_not_rm(slim_bin, tmp_path, monkeypatch):
    docker = SlimDocker(slim_bin)
    monkeypatch.setattr(pl.subprocess, "run", docker.run)
    target = tmp_path / "patch.py"
    target.write_text("x")
    # FS_PY runs on the host via slim docker mapping, so paths are host paths.
    out = pl.container_fs("harvey", "unlink", [str(target)])
    assert out.get("ok") is True
    assert not target.exists()
    inners = [_inner(c) for c in docker.cmds]
    assert all(os.path.basename(i[0]) in ("python3", "python") for i in inners if i)
    assert not any(os.path.basename(i[0]) == "rm" for i in inners if i)


def test_rollback_and_reload_scripts_share_os_kill_path():
    rb = (Path(__file__).resolve().parents[1] / "deploy" / "harvey-sss" / "host" / "rollback.sh").read_text()
    life = Path(pl.__file__).read_text()
    assert "reload-dashboard" in rb
    assert not any(ln.strip().startswith("docker restart") for ln in rb.splitlines())
    assert "os.kill" in life
    assert '["docker", "exec", container, "kill"' not in life


def _run_stop(code: str, proc: dict, port: int):
    spec = {
        "pid": proc["pid"], "uid": proc["uid"], "argv": proc["argv"],
        "port": port, "sig": int(signal.SIGTERM),
    }
    return _REAL_RUN(
        [sys.executable, "-c", code],
        input=json.dumps(spec), capture_output=True, text=True, timeout=10,
    )


def test_empty_socket_view_while_pid_alive_is_not_already_gone():
    """UID0-style empty listener() must not report already_gone; signal instead."""
    child, proc, port = _spawn_listener()
    try:
        code = pl._PROC_HELPERS + "\ndef listener(port):\n    return []\n" + pl.STOP_BODY
        r = _run_stop(code, proc, port)
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data.get("already_gone") is not True
        assert data.get("via") == "os.kill"
        child.wait(timeout=8)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=3)


def test_unreadable_socket_metadata_fails_closed_not_already_gone():
    child, proc, port = _spawn_listener()
    try:
        code = pl._PROC_HELPERS + "\ndef tcp_readable():\n    return False\n" + pl.STOP_BODY
        r = _run_stop(code, proc, port)
        blob = (r.stderr or "") + (r.stdout or "")
        assert r.returncode != 0
        assert "unreadable" in blob
        assert "already_gone" not in (r.stdout or "")
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=3)


def test_stop_and_capture_use_captured_uid_not_root():
    src = Path(pl.__file__).read_text()
    stop_fn = src.split("def stop_listener")[1].split("def stop_pid")[0]
    assert 'uid="0"' not in stop_fn
    assert "view_uid" in stop_fn
    assert 'attempts = ["1000", None]' in src
    assert "if not hits:" not in pl.STOP_BODY
    assert "socket metadata unreadable" in pl.STOP_BODY
    assert "wait_listener_gone" in src and "uid=view_uid" in src

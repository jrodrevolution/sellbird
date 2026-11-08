# SPDX-License-Identifier: AGPL-3.0-only
"""Capture and scoped reload of processes that are not PID1.

harvey and harvey-sss PID1 is sleep infinity; the Python dashboard is a
separate process. docker restart would kill it. This module records listener
metadata (no environ, no logs) and relaunches that exact argv.

Hermes PID1 is the image entrypoint (/init + wrapper); do not relaunch its
children. Review server is new and started only if port 18765 is empty.

Slim Harvey has python3 but no ``kill``/``rm``/``chown``/``sh`` binaries.
In-container work uses the existing interpreter (os.kill, os.remove, os.chown).
Listener capture/stop/wait run as the **captured UID** (dashboard is 1000).
UID 0 does not see that socket in this image; empty root view is not gone.
Never ``docker exec … kill``. Never docker restart.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time

DASHBOARD_PORT = 5555
REVIEW_PORT = 18765
SLEEP_PID1 = ("sleep", "infinity")
SIGTERM = int(signal.SIGTERM)
SIGKILL = int(signal.SIGKILL)

# Runs inside the target container. Prints JSON. Never dumps environ.
_PROC_HELPERS = r"""
import json, os, glob, signal, sys
def argv_of(pid):
    try:
        raw = open('/proc/%s/cmdline' % pid, 'rb').read().split(b'\0')
        return [x.decode('utf-8', 'replace') for x in raw if x]
    except Exception:
        return []
def cwd_of(pid):
    try:
        return os.readlink('/proc/%s/cwd' % pid)
    except Exception:
        return ''
def uid_of(pid):
    try:
        for line in open('/proc/%s/status' % pid):
            if line.startswith('Uid:'):
                return line.split()[1]
    except Exception:
        return ''
    return ''
def inodes_for_port(port):
    want = '%04X' % int(port)
    found = set()
    for path in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            lines = open(path).read().splitlines()[1:]
        except Exception:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            local = parts[1]
            st = parts[3]
            inode = parts[9]
            if st != '0A':
                continue
            hexport = local.rsplit(':', 1)[-1].upper()
            if hexport == want:
                found.add(inode)
    return found
def pid_for_inode(inode):
    for fd in glob.glob('/proc/[0-9]*/fd/[0-9]*'):
        try:
            tgt = os.readlink(fd)
        except Exception:
            continue
        if tgt == 'socket:[%s]' % inode:
            return fd.split('/')[2]
    return ''
def listener(port):
    hits = []
    for inode in inodes_for_port(port):
        pid = pid_for_inode(inode)
        if not pid:
            continue
        hits.append({
            'pid': pid,
            'argv': argv_of(pid),
            'cwd': cwd_of(pid),
            'uid': uid_of(pid),
            'port': int(port),
        })
    return hits
def tcp_readable():
    try:
        open('/proc/net/tcp').close()
        return True
    except Exception:
        return False
def fd_readable(pid):
    try:
        os.listdir('/proc/%s/fd' % pid)
        return True
    except Exception:
        return False
"""

CAPTURE_PY = _PROC_HELPERS + r"""
out = {
    'pid1': {'pid': '1', 'argv': argv_of('1'), 'cwd': cwd_of('1'), 'uid': uid_of('1')},
    'listeners': {
        '5555': listener(5555),
        '18765': listener(18765),
    },
}
print(json.dumps(out))
"""

# Identity-checked SIGTERM/SIGKILL via os.kill. No kill binary.
# Must run as the captured listener UID. Empty UID0 socket view is not gone.
STOP_BODY = r"""
spec = json.loads(sys.stdin.read() or '{}')
pid = str(spec.get('pid') or '')
if not pid.isdigit() or pid in ('0', '1'):
    raise SystemExit('refuse pid1 or non-pid; no docker restart')
port = int(spec.get('port') or 0)
want_uid = str(spec.get('uid') or '')
want_argv = list(spec.get('argv') or [])
try:
    os.kill(int(pid), 0)
    alive = True
except ProcessLookupError:
    alive = False
except PermissionError:
    raise SystemExit('pid not signalable by this uid; refuse stop')
if not alive:
    print(json.dumps({'ok': True, 'already_gone': True, 'pid': pid}))
    raise SystemExit(0)
got_uid = uid_of(pid)
got_argv = argv_of(pid)
if want_uid and not got_uid:
    raise SystemExit('uid unreadable; refuse stop')
if want_argv and not got_argv:
    raise SystemExit('argv unreadable; refuse stop')
if want_uid and got_uid != want_uid:
    raise SystemExit('uid mismatch; refuse stop')
if want_argv and got_argv != want_argv:
    raise SystemExit('argv mismatch; refuse stop')
if port:
    if not tcp_readable() or not fd_readable(pid):
        raise SystemExit('listener socket metadata unreadable; refuse stop')
    hits = listener(port)
    mine = [h for h in hits if str(h.get('pid')) == pid]
    if hits and not mine:
        raise SystemExit('pid is not the listener; refuse stop')
    # Empty hits while pid is alive: not listening, or this uid cannot
    # map inodes. Do not report already_gone. Signal the captured pid.
sig = int(spec.get('sig') or signal.SIGTERM)
try:
    os.kill(int(pid), sig)
except ProcessLookupError:
    print(json.dumps({'ok': True, 'already_gone': True, 'pid': pid}))
    raise SystemExit(0)
except PermissionError:
    raise SystemExit('os.kill permission denied; refusing docker restart')
print(json.dumps({'ok': True, 'signaled': pid, 'sig': sig, 'via': 'os.kill'}))
"""
STOP_PY = _PROC_HELPERS + STOP_BODY

FS_PY = r"""
import json, os, shutil, sys
spec = json.loads(sys.stdin.read() or '{}')
op = spec.get('op') or ''
paths = list(spec.get('paths') or [])
owner = spec.get('owner') or ''
recursive = bool(spec.get('recursive'))
uid = gid = None
if owner:
    parts = str(owner).replace('.', ':').split(':')
    uid, gid = int(parts[0]), int(parts[1] if len(parts) > 1 else parts[0])

def chown_path(p):
    os.chown(p, uid, gid)
    if recursive and os.path.isdir(p):
        for root, dirs, files in os.walk(p):
            os.chown(root, uid, gid)
            for name in dirs + files:
                os.chown(os.path.join(root, name), uid, gid)

done = []
if op == 'mkdir':
    for p in paths:
        os.makedirs(p, exist_ok=True)
        done.append(p)
elif op == 'unlink':
    for p in paths:
        if os.path.isfile(p) or os.path.islink(p):
            os.remove(p)
            done.append(p)
elif op == 'chown':
    if uid is None:
        raise SystemExit('chown requires owner')
    for p in paths:
        chown_path(p)
        done.append(p)
elif op == 'revert-sss-glm-inject':
    pairs = (
        ('/app/harvey/dashboard.py.pre-sss-glm', '/app/harvey/dashboard.py'),
        ('/app/harvey/web/studio.html.pre-sss-glm', '/app/harvey/web/studio.html'),
    )
    for src, dst in pairs:
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            done.append(dst)
    for p in ('/app/harvey/sss_glm_studio.py', '/app/harvey/web/sss_glm_studio.js'):
        if os.path.isfile(p) or os.path.islink(p):
            os.remove(p)
            done.append(p)
else:
    raise SystemExit('unknown fs op')
print(json.dumps({'ok': True, 'op': op, 'done': done}))
"""


def _interpreters(container: str) -> tuple[str, ...]:
    py = ("python3", "python")
    if container == "hermes-agent":
        return py + ("/opt/hermes/.venv/bin/python",)
    return py


def _not_found(returncode: int, err: str) -> bool:
    blob = (err or "").lower()
    return returncode == 127 or "executable file not found" in blob or "no such file" in blob


def exec_python(container: str, code: str, *, uid=None, cwd: str | None = None,
                input_text: str | None = None, timeout_s: float = 20.0):
    """Run code with the container's python3/python. Never a kill/rm/chown binary."""
    last = None
    for py in _interpreters(container):
        cmd = ["docker", "exec"]
        if input_text is not None:
            cmd.append("-i")
        if uid is not None:
            cmd += ["-u", str(uid)]
        if cwd:
            cmd += ["-w", cwd]
        cmd += [container, py, "-c", code]
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            input=input_text, timeout=timeout_s,
        )
        last = r
        if r.returncode == 0:
            return r
        err = (r.stderr or "") + (r.stdout or "")
        if _not_found(r.returncode, err):
            continue
        raise SystemExit(
            f"{container}: in-container python failed ({err[:400]!r}). "
            "Refusing docker restart and refusing the kill binary."
        )
    err = ((last.stderr or last.stdout or "") if last else "no python")[:400]
    raise SystemExit(
        f"{container}: no python interpreter for in-container work ({err!r}). "
        "Refusing docker restart and refusing the kill/rm/chown binaries."
    )


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def _capture_once(container: str, *, uid: str | None) -> dict:
    last = None
    for py in _interpreters(container):
        cmd = ["docker", "exec"]
        if uid is not None:
            cmd += ["-u", str(uid)]
        cmd += [container, py, "-c", CAPTURE_PY]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        last = r
        if r.returncode == 0:
            data = json.loads(r.stdout)
            data["container"] = container
            data["capture_uid"] = str(uid) if uid is not None else "default"
            return data
        err = (r.stderr or "") + (r.stdout or "")
        if _not_found(r.returncode, err):
            continue
        raise SystemExit(
            f"{container}: process capture failed ({err[:400]!r}). "
            "Parent must provide :5555 listener argv/cwd/uid; refusing docker restart."
        )
    err = (last.stderr or last.stdout or "")[:400] if last else "no python"
    raise SystemExit(
        f"{container}: process capture failed ({err!r}). "
        "Parent must provide :5555 listener argv/cwd/uid; refusing docker restart."
    )


def capture(container: str, *, uid: str | None = None) -> dict:
    """Live process metadata. No environ, no logs.

    Probe as the captured/dashboard UID (1000), not as root. UID 0 in this
    image does not see the UID 1000 :5555 socket; that empty view is not
    treated as 'no listener'.
    """
    if uid is not None:
        attempts: list[str | None] = [str(uid)]
    else:
        attempts = ["1000", None]
    last = None
    last_err = None
    for u in attempts:
        try:
            data = _capture_once(container, uid=u)
        except SystemExit as exc:
            last_err = exc
            continue
        last = data
        lis = data.get("listeners") or {}
        if lis.get(str(DASHBOARD_PORT)) or lis.get(str(REVIEW_PORT)):
            return data
    if last is not None:
        return last
    if last_err:
        raise last_err
    raise SystemExit(
        f"{container}: process capture failed. "
        "Parent must provide :5555 listener argv/cwd/uid; refusing docker restart."
    )


def pid1_is_sleep_infinity(meta: dict) -> bool:
    argv = list((meta.get("pid1") or {}).get("argv") or [])
    return len(argv) >= 2 and argv[0].endswith("sleep") and argv[1] == "infinity"


def one_listener(meta: dict, port: int) -> dict:
    hits = ((meta.get("listeners") or {}).get(str(port)) or [])
    if len(hits) != 1:
        raise SystemExit(
            f"{meta.get('container')}: expected exactly one listener on {port}, "
            f"got {len(hits)}. Refusing docker restart. Provide process metadata."
        )
    proc = hits[0]
    if not proc.get("argv"):
        raise SystemExit(f"{meta.get('container')}: listener {port} has empty argv")
    return proc


def persist_unit(container: str, proc: dict, port: int) -> str:
    """Host systemd unit that docker-execs the captured argv. Not PID1."""
    argv = proc["argv"]
    uid = proc.get("uid") or "1000"
    cwd = proc.get("cwd") or "/app"
    cmd = " ".join(shlex.quote(x) for x in argv)
    return f"""[Unit]
Description=Keep captured listener :{port} in {container} (PID1 stays sleep infinity)
After=docker.service
Requires=docker.service

[Service]
Type=simple
Restart=on-failure
RestartSec=3
ExecStartPre=/bin/bash -c 'until docker inspect -f {{{{.State.Running}}}} {container} 2>/dev/null | grep -qx true; do sleep 1; done'
ExecStart=/usr/bin/docker exec -u {uid} -w {shlex.quote(cwd)} {container} {cmd}

[Install]
WantedBy=multi-user.target
"""


def _listening(container: str, port: int, *, uid: str | None = None) -> bool:
    meta = capture(container, uid=uid)
    return bool((meta.get("listeners") or {}).get(str(port)))


def _same_identity(live: dict, proc: dict) -> bool:
    return (
        str(live.get("pid")) == str(proc.get("pid"))
        and str(live.get("uid") or "") == str(proc.get("uid") or "")
        and list(live.get("argv") or []) == list(proc.get("argv") or [])
    )


def stop_listener(container: str, proc: dict, port: int, *, sig: int = SIGTERM) -> dict:
    """SIGTERM/SIGKILL the captured listener via python os.kill.

    Re-checks PID/UID/argv and listenership inside the container. Never
    execs a kill binary. Never signals PID 1. Never docker restart.
    """
    pid = str(proc.get("pid") or "")
    if not pid.isdigit() or pid in ("0", "1"):
        raise SystemExit(f"{container}: refuse stop of pid {pid!r} (not a dashboard listener)")
    view_uid = str(proc.get("uid") or "1000")
    live_meta = capture(container, uid=view_uid)
    if not pid1_is_sleep_infinity(live_meta):
        raise SystemExit(
            f"{container}: PID1 is {live_meta.get('pid1', {}).get('argv')!r}; "
            "refusing to signal anything or docker restart"
        )
    live = one_listener(live_meta, port)
    if not _same_identity(live, proc):
        raise SystemExit(
            f"{container}: listener identity changed (pid/uid/argv); refusing stop"
        )
    spec = {
        "pid": pid,
        "uid": view_uid,
        "argv": list(proc.get("argv") or live.get("argv") or []),
        "port": int(port),
        "sig": int(sig),
    }
    # Same UID as the listener so /proc/net and /proc/<pid>/fd are visible.
    r = exec_python(
        container, STOP_PY, uid=view_uid, cwd=proc.get("cwd") or live.get("cwd") or None,
        input_text=json.dumps(spec),
    )
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": True, "signaled": pid, "raw": (r.stdout or "")[:200]}


def stop_pid(container: str, pid: str, *, proc: dict | None = None, port: int | None = None) -> dict:
    """Identity-required stop. Blind ``docker exec kill <pid>`` is gone."""
    if proc is None or port is None:
        raise SystemExit(
            f"{container}: stop_pid requires captured proc+port identity; "
            "refusing blind kill binary and docker restart"
        )
    if str(proc.get("pid")) != str(pid):
        raise SystemExit(f"{container}: pid does not match captured listener")
    return stop_listener(container, proc, port)


def container_fs(container: str, op: str, paths: list[str], *,
                 owner: str = "", recursive: bool = False, uid: str = "0") -> dict:
    """mkdir/unlink/chown/revert inside the container using python, not rm/chown/sh."""
    spec = {"op": op, "paths": list(paths), "owner": owner, "recursive": recursive}
    r = exec_python(container, FS_PY, uid=uid, input_text=json.dumps(spec))
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": True, "raw": (r.stdout or "")[:200]}


def start_captured(container: str, proc: dict) -> None:
    argv = ["docker", "exec", "-d", "-u", str(proc.get("uid") or "1000")]
    if proc.get("cwd"):
        argv += ["-w", proc["cwd"]]
    argv += [container] + list(proc["argv"])
    _run(argv)


def wait_listener(container: str, port: int, timeout_s: float = 30.0,
                  *, uid: str | None = None) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _listening(container, port, uid=uid):
            return
        time.sleep(0.4)
    raise SystemExit(f"{container}: listener {port} did not return")


def wait_listener_gone(container: str, port: int, timeout_s: float = 15.0,
                       *, uid: str | None = None) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _listening(container, port, uid=uid):
            return True
        time.sleep(0.2)
    return not _listening(container, port, uid=uid)


def reload_dashboard(container: str, *, port: int = DASHBOARD_PORT) -> dict:
    """SIGTERM the captured :port process and relaunch the same argv. Never docker restart."""
    before = capture(container)
    if not pid1_is_sleep_infinity(before):
        raise SystemExit(
            f"{container}: PID1 is {before.get('pid1', {}).get('argv')!r}, "
            "not sleep infinity; refusing to kill PID1 or docker restart"
        )
    proc = one_listener(before, port)
    view_uid = str(proc.get("uid") or "1000")
    stop_listener(container, proc, port, sig=SIGTERM)
    if not wait_listener_gone(container, port, timeout_s=8.0, uid=view_uid):
        stop_listener(container, proc, port, sig=SIGKILL)
        if not wait_listener_gone(container, port, timeout_s=7.0, uid=view_uid):
            raise SystemExit(
                f"{container}: old listener {port} still up after os.kill; "
                "refusing docker restart"
            )
    try:
        start_captured(container, proc)
        wait_listener(container, port, uid=view_uid)
    except Exception:
        start_captured(container, proc)
        wait_listener(container, port, uid=view_uid)
    after = capture(container, uid=view_uid)
    if not pid1_is_sleep_infinity(after):
        raise SystemExit(f"{container}: PID1 changed after scoped reload")
    return {"ok": True, "container": container, "port": port, "argv": proc["argv"],
            "docker_restart": False, "stop": "os.kill", "uid": view_uid}


def relaunch_if_sleep_pid1(container: str, saved: dict, *, port: int = DASHBOARD_PORT) -> dict:
    """After container recreate: PID1 sleep infinity will not revive the dashboard."""
    saved_uid = None
    try:
        saved_uid = str(one_listener(saved, port).get("uid") or "1000")
    except SystemExit:
        saved_uid = "1000"
    meta = capture(container, uid=saved_uid)
    if not pid1_is_sleep_infinity(meta):
        return {"ok": True, "skipped": "pid1_not_sleep", "container": container}
    if (meta.get("listeners") or {}).get(str(port)):
        return {"ok": True, "skipped": "already_listening", "container": container}
    proc = one_listener(saved, port)
    start_captured(container, proc)
    wait_listener(container, port, uid=str(proc.get("uid") or saved_uid))
    return {"ok": True, "relaunched": proc["argv"], "container": container}


def start_review_server(container: str = "harvey-sss") -> dict:
    meta = capture(container)
    hits = (meta.get("listeners") or {}).get(str(REVIEW_PORT)) or []
    if len(hits) > 1:
        raise SystemExit(f"{container}: {len(hits)} listeners on {REVIEW_PORT}; refusing duplicate")
    if hits:
        return {"ok": True, "skipped": "already_listening", "port": REVIEW_PORT,
                "argv": hits[0].get("argv")}
    dash = (meta.get("listeners") or {}).get(str(DASHBOARD_PORT)) or []
    view_uid = str((dash[0].get("uid") if dash else None) or "1000")
    unit = "/etc/systemd/system/sss-glm-review.service"
    if os.path.exists(unit):
        subprocess.run(["systemctl", "start", "sss-glm-review.service"], check=True)
        wait_listener(container, REVIEW_PORT, uid=view_uid)
        return {"ok": True, "started": "systemd:sss-glm-review", "port": REVIEW_PORT}
    argv = {
        "argv": ["python3", "/app/data/sss_glm/review_server.py"],
        "cwd": "/app",
        "uid": view_uid,
    }
    start_captured(container, argv)
    wait_listener(container, REVIEW_PORT, uid=view_uid)
    return {"ok": True, "started": argv["argv"], "port": REVIEW_PORT}


def parse_tcp_fixture(text: str, port: int) -> list[str]:
    """Test helper: inodes in LISTEN state for port from a /proc/net/tcp dump."""
    want = "%04X" % int(port)
    inodes = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        hexport = parts[1].rsplit(":", 1)[-1].upper()
        if parts[3] == "0A" and hexport == want:
            inodes.append(parts[9])
    return inodes

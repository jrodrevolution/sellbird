# SPDX-License-Identifier: AGPL-3.0-only
"""Capture and scoped reload of processes that are not PID1.

harvey and harvey-sss PID1 is sleep infinity; the Python dashboard is a
separate process. docker restart would kill it. This module records listener
metadata (no environ, no logs) and relaunches that exact argv.

Hermes PID1 is the image entrypoint (/init + wrapper); do not relaunch its
children. Review server is new and started only if port 18765 is empty.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time

DASHBOARD_PORT = 5555
REVIEW_PORT = 18765
SLEEP_PID1 = ("sleep", "infinity")

# Runs inside the target container. Prints JSON. Never dumps environ.
CAPTURE_PY = r"""
import json, os, glob
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
out = {
    'pid1': {'pid': '1', 'argv': argv_of('1'), 'cwd': cwd_of('1'), 'uid': uid_of('1')},
    'listeners': {
        '5555': listener(5555),
        '18765': listener(18765),
    },
}
print(json.dumps(out))
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def capture(container: str) -> dict:
    """Live process metadata. No environ, no logs."""
    last = None
    for py in ("python3", "python"):
        r = subprocess.run(
            ["docker", "exec", container, py, "-c", CAPTURE_PY],
            capture_output=True, text=True,
        )
        last = r
        if r.returncode == 0:
            data = json.loads(r.stdout)
            data["container"] = container
            return data
    err = (last.stderr or last.stdout or "")[:400] if last else "no python"
    raise SystemExit(
        f"{container}: process capture failed ({err!r}). "
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


def _listening(container: str, port: int) -> bool:
    meta = capture(container)
    return bool((meta.get("listeners") or {}).get(str(port)))


def stop_pid(container: str, pid: str) -> None:
    subprocess.run(["docker", "exec", container, "kill", pid], check=False)


def start_captured(container: str, proc: dict) -> None:
    argv = ["docker", "exec", "-d", "-u", str(proc.get("uid") or "1000")]
    if proc.get("cwd"):
        argv += ["-w", proc["cwd"]]
    argv += [container] + list(proc["argv"])
    _run(argv)


def wait_listener(container: str, port: int, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _listening(container, port):
            return
        time.sleep(0.4)
    raise SystemExit(f"{container}: listener {port} did not return")


def reload_dashboard(container: str, *, port: int = DASHBOARD_PORT) -> dict:
    """SIGTERM the captured :port process and relaunch the same argv. Never docker restart."""
    before = capture(container)
    if not pid1_is_sleep_infinity(before):
        raise SystemExit(
            f"{container}: PID1 is {before.get('pid1', {}).get('argv')!r}, "
            "not sleep infinity; refusing to kill PID1 or docker restart"
        )
    proc = one_listener(before, port)
    stop_pid(container, proc["pid"])
    deadline = time.time() + 15
    while time.time() < deadline and _listening(container, port):
        time.sleep(0.2)
    if _listening(container, port):
        raise SystemExit(f"{container}: old listener {port} still up")
    try:
        start_captured(container, proc)
        wait_listener(container, port)
    except Exception:
        start_captured(container, proc)
        wait_listener(container, port)
    after = capture(container)
    if not pid1_is_sleep_infinity(after):
        raise SystemExit(f"{container}: PID1 changed after scoped reload")
    return {"ok": True, "container": container, "port": port, "argv": proc["argv"],
            "docker_restart": False}


def relaunch_if_sleep_pid1(container: str, saved: dict, *, port: int = DASHBOARD_PORT) -> dict:
    """After container recreate: PID1 sleep infinity will not revive the dashboard."""
    meta = capture(container)
    if not pid1_is_sleep_infinity(meta):
        return {"ok": True, "skipped": "pid1_not_sleep", "container": container}
    if (meta.get("listeners") or {}).get(str(port)):
        return {"ok": True, "skipped": "already_listening", "container": container}
    proc = one_listener(saved, port)
    start_captured(container, proc)
    wait_listener(container, port)
    return {"ok": True, "relaunched": proc["argv"], "container": container}


def start_review_server(container: str = "harvey-sss") -> dict:
    meta = capture(container)
    hits = (meta.get("listeners") or {}).get(str(REVIEW_PORT)) or []
    if len(hits) > 1:
        raise SystemExit(f"{container}: {len(hits)} listeners on {REVIEW_PORT}; refusing duplicate")
    if hits:
        return {"ok": True, "skipped": "already_listening", "port": REVIEW_PORT,
                "argv": hits[0].get("argv")}
    unit = "/etc/systemd/system/sss-glm-review.service"
    if os.path.exists(unit):
        subprocess.run(["systemctl", "start", "sss-glm-review.service"], check=True)
        wait_listener(container, REVIEW_PORT)
        return {"ok": True, "started": "systemd:sss-glm-review", "port": REVIEW_PORT}
    argv = {
        "argv": ["python3", "/app/data/sss_glm/review_server.py"],
        "cwd": "/app",
        "uid": (meta.get("pid1") or {}).get("uid") or "1000",
    }
    start_captured(container, argv)
    wait_listener(container, REVIEW_PORT)
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

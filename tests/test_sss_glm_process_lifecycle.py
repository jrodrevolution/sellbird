# SPDX-License-Identifier: AGPL-3.0-only
"""PID1 sleep infinity: capture the :5555 listener; never docker restart."""

from pathlib import Path

import pytest

from sss_glm.process_lifecycle import (
    one_listener,
    parse_tcp_fixture,
    persist_unit,
    pid1_is_sleep_infinity,
)

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "deploy" / "harvey-sss" / "host" / "apply-dashboard.sh"
RECREATE = ROOT / "deploy" / "harvey-sss" / "host" / "recreate.py"
RELOAD = ROOT / "deploy" / "harvey-sss" / "host" / "reload_process.py"
LIFECYCLE = ROOT / "sss_glm" / "process_lifecycle.py"

# Synthetic /proc/net/tcp: IPv4 LISTEN on 5555 (0x15B3) inode 4242.
TCP_5555 = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:15B3 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 4242 1 0000000000000000 100 0 0 10 0
   1: 0100007F:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 99 1 0000000000000000 100 0 0 10 0
"""


def test_tcp_listen_inode_for_dashboard_port():
    assert parse_tcp_fixture(TCP_5555, 5555) == ["4242"]
    assert parse_tcp_fixture(TCP_5555, 18765) == []


def test_pid1_sleep_infinity_and_one_listener():
    meta = {
        "container": "harvey",
        "pid1": {"pid": "1", "argv": ["sleep", "infinity"], "cwd": "/", "uid": "0"},
        "listeners": {
            "5555": [{
                "pid": "42",
                "argv": ["python3", "-m", "harvey.dashboard"],
                "cwd": "/app",
                "uid": "1000",
                "port": 5555,
            }],
            "18765": [],
        },
    }
    assert pid1_is_sleep_infinity(meta)
    proc = one_listener(meta, 5555)
    assert proc["argv"] == ["python3", "-m", "harvey.dashboard"]
    with pytest.raises(SystemExit, match="exactly one listener on 18765"):
        one_listener(meta, 18765)
    dup = dict(meta)
    dup["listeners"] = {"5555": proc, "x": None}
    dup["listeners"]["5555"] = [proc, proc]
    with pytest.raises(SystemExit, match="exactly one listener on 5555"):
        one_listener(dup, 5555)


def test_persist_unit_docker_exec_not_restart():
    proc = {"argv": ["python3", "-m", "harvey.dashboard"], "cwd": "/app", "uid": "1000"}
    text = persist_unit("harvey", proc, 5555)
    assert "docker restart" not in text
    assert "docker exec" in text
    assert "python3" in text
    assert "-m" in text
    assert "harvey.dashboard" in text
    assert "sleep infinity" not in text.split("ExecStart")[-1]


def test_apply_dashboard_never_docker_restarts():
    text = DASH.read_text()
    assert not any(
        ln.strip().startswith("docker restart") for ln in text.splitlines()
    )
    assert "reload_process.py" in text
    assert "PID1" in text or "sleep infinity" in text
    assert "--reload-dashboard" in text


def test_recreate_uses_engine_api_and_relaunches_sss_dashboard():
    text = RECREATE.read_text()
    assert "create_argv" not in text
    assert "engine_api_create" in text
    assert "relaunch_if_sleep_pid1" in text
    assert not any(
        ln.strip().startswith("docker restart") for ln in text.splitlines()
    )
    assert "start_review_server" in text
    src = LIFECYCLE.read_text()
    assert "Hermes" in src
    assert "do not relaunch" in src.lower() or "Do not relaunch" in src


def test_reload_helper_refuses_unknown_container():
    spec = RELOAD.read_text()
    assert "only harvey or harvey-sss" in spec
    assert "review server only on harvey-sss" in spec

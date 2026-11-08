# SPDX-License-Identifier: AGPL-3.0-only
"""Exact-config recreate must keep the settings the operator dry-run dropped."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from sss_glm.docker_exact import (
    blockers,
    compare_fingerprints,
    create_body,
    extra_mounts_for,
    fingerprint,
    public_plan,
    unsupported_hostconfig,
)

FIX = Path(__file__).resolve().parent / "fixtures"
HERMES_ENTRYPOINT = ["/init", "/opt/hermes/docker/main-wrapper.sh"]
HERMES_CMD = ["gateway", "run"]


def _load(name):
    return json.loads((FIX / name).read_text())


def test_sss_keeps_localhost_5556_and_three_binds():
    info = _load("docker_inspect_harvey_sss.json")
    extra = extra_mounts_for("harvey-sss")
    body = create_body("harvey-sss.glmnew", info, image="snap:local", extra=extra)
    ports = body["HostConfig"]["PortBindings"]
    assert ports["5555/tcp"][0]["HostIp"] == "127.0.0.1"
    assert ports["5555/tcp"][0]["HostPort"] == "5556"
    assert body["Cmd"] == ["sleep", "infinity"]
    assert body["Entrypoint"] == []
    assert body["User"] == "harvey"
    assert body["HostConfig"]["Memory"] == 805306368
    assert body["HostConfig"]["NanoCpus"] == 500000000
    dests = {m["Target"]: m for m in body["HostConfig"]["Mounts"]}
    assert dests["/app/data"]["Source"] == "/opt/harvey-sss/data"
    assert dests["/app/harvey.local.yaml"]["Source"] == "/opt/harvey-sss/harvey.local.yaml"
    assert dests["/home/harvey/.claude"]["Source"] == "/opt/harvey-sss/claude-home"
    assert dests["/var/sss-glm/requests"]["ReadOnly"] is False
    assert dests["/var/sss-glm/responses"]["ReadOnly"] is True
    assert "/var/sss-glm/ledger" not in dests
    plan = public_plan("harvey-sss", info, extra, body=body)
    assert plan["create_method"] == "engine-api"
    assert "127.0.0.1:5556:5555/tcp" in plan["ports"]
    assert plan["entrypoint"] == []
    assert plan["cmd"] == ["sleep", "infinity"]
    assert plan["engine_api_redacted"]["Entrypoint"] == []
    assert plan["engine_api_redacted"]["Cmd"] == ["sleep", "infinity"]
    assert "SMTP_PASSWORD" in plan["env_keys"]
    blob = json.dumps(plan)
    assert "mail.example.test" not in blob
    assert plan["engine_api_redacted"]["Env"] == [
        e.split("=", 1)[0] for e in (info.get("Config") or {}).get("Env") or []
    ]


def test_hermes_keeps_full_entrypoint_array_and_cmd():
    info = _load("docker_inspect_hermes.json")
    extra = extra_mounts_for("hermes-agent")
    body = create_body("hermes-agent.glmnew", info, image="snap:local", extra=extra)
    assert body["Entrypoint"] == HERMES_ENTRYPOINT
    assert body["Cmd"] == HERMES_CMD
    assert body["User"] == "root"
    dests = {m["Target"]: m for m in body["HostConfig"]["Mounts"]}
    assert dests["/opt/data"]["Source"] == "a1b2c3d4e5f6anonymousoptdata"
    assert dests["/opt/hermes"]["Source"] == "hermes_hermes-agent-src"
    assert dests["/var/sss-glm/requests"]["ReadOnly"] is True
    assert dests["/var/sss-glm/responses"]["ReadOnly"] is False
    assert dests["/var/sss-glm/ledger"]["ReadOnly"] is False
    assert body["HostConfig"]["NetworkMode"] == "hermes_hermes-network"
    fp = fingerprint(info)
    assert fp["entrypoint"] == HERMES_ENTRYPOINT
    assert fp["cmd"] == HERMES_CMD
    assert "lobodesk-proxy" in fp["network_extra"]
    plan = public_plan("hermes-agent", info, extra, body=body)
    assert plan["create_method"] == "engine-api"
    assert plan["entrypoint"] == HERMES_ENTRYPOINT
    assert plan["engine_api_redacted"]["Entrypoint"] == HERMES_ENTRYPOINT
    assert plan["engine_api_redacted"]["Cmd"] == HERMES_CMD
    assert isinstance(plan["engine_api_redacted"]["Entrypoint"], list)
    assert len(plan["engine_api_redacted"]["Entrypoint"]) == 2
    blob = json.dumps(plan)
    assert "OPENROUTER_API_KEY" in plan["env_keys"]
    assert "OPENROUTER_API_KEY=" not in blob
    assert plan["engine_api_redacted"]["Env"] == [
        e.split("=", 1)[0] for e in (info.get("Config") or {}).get("Env") or []
    ]


def test_entrypoint_is_never_flattened_to_cli_string():
    src = Path(__file__).resolve().parents[1] / "sss_glm" / "docker_exact.py"
    text = src.read_text()
    assert "create_argv" not in text
    assert '["--entrypoint"' not in text
    assert "multi-arg Entrypoint not reproduced" not in text
    info = _load("docker_inspect_hermes.json")
    extra = extra_mounts_for("hermes-agent")
    body = create_body("hermes-agent.glmnew", info, image="snap:local", extra=extra)
    assert body["Entrypoint"] == ["/init", "/opt/hermes/docker/main-wrapper.sh"]


def test_compare_detects_dropped_port():
    info = _load("docker_inspect_harvey_sss.json")
    extra = extra_mounts_for("harvey-sss")
    before = fingerprint(info)
    after_info = deepcopy(info)
    after_info["HostConfig"]["PortBindings"] = {}
    after = fingerprint(after_info)
    reasons = compare_fingerprints(before, after, extra)
    assert any("ports" in r or "5556" in r for r in reasons)


def test_fail_closed_on_unsupported_hostconfig():
    info = _load("docker_inspect_harvey_sss.json")
    info["HostConfig"]["UnknownFutureFlag"] = "yes"
    assert "UnknownFutureFlag" in unsupported_hostconfig(info)
    assert any("unsupported HostConfig.UnknownFutureFlag" in b
               for b in blockers("harvey-sss", info))


def test_refuses_harvey_name():
    info = _load("docker_inspect_harvey_sss.json")
    with pytest.raises(SystemExit, match="never the custom Harvey dashboard"):
        create_body("harvey", info, image="x", extra=extra_mounts_for("harvey-sss"))

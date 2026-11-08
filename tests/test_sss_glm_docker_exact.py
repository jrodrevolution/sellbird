# SPDX-License-Identifier: AGPL-3.0-only
"""Exact-config recreate must keep the settings the operator dry-run dropped."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from sss_glm.docker_exact import (
    blockers,
    compare_fingerprints,
    create_argv,
    extra_mounts_for,
    fingerprint,
    public_plan,
    unsupported_hostconfig,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


def test_sss_keeps_localhost_5556_and_entrypoint_labels_health():
    info = _load("docker_inspect_harvey_sss.json")
    extra = extra_mounts_for("harvey-sss")
    argv = create_argv("harvey-sss.glmnew", info, image="snap:local",
                       env_file="/root/x.env", extra=extra)
    joined = " ".join(argv)
    assert "-p 127.0.0.1:5556:5555/tcp" in joined
    assert "--entrypoint /app/entrypoint.sh" in joined
    assert "--label app=harvey-sss" in joined
    assert "--health-cmd" in argv
    assert "--security-opt no-new-privileges:true" in joined
    assert "--cap-drop NET_RAW" in joined
    assert "--restart unless-stopped" in joined
    assert "type=bind,destination=/app/data,source=/opt/harvey-sss/data" in joined
    assert "destination=/var/sss-glm/requests" in joined
    assert "destination=/var/sss-glm/responses,source=/opt/sss-glm-channel/responses,readonly" in joined
    plan = public_plan("harvey-sss", info, extra, argv)
    assert "127.0.0.1:5556:5555/tcp" in plan["ports"]
    assert plan["entrypoint"] == ["/app/entrypoint.sh"]
    assert "SMTP_PASSWORD" in plan["env_keys"]
    blob = json.dumps(plan)
    assert "REDACTED" not in blob
    assert "mail.example.test" not in blob


def test_hermes_keeps_anonymous_opt_data_and_lobodesk_proxy():
    info = _load("docker_inspect_hermes.json")
    extra = extra_mounts_for("hermes-agent")
    argv = create_argv("hermes-agent.glmnew", info, image="snap:local",
                       env_file="/root/x.env", extra=extra)
    joined = " ".join(argv)
    assert "--network hermes_hermes-network" in joined
    assert "--entrypoint /opt/hermes/.venv/bin/hermes" in joined
    assert "gateway" in argv and "run" in argv
    assert "source=a1b2c3d4e5f6anonymousoptdata" in joined
    assert "destination=/opt/data" in joined
    assert "source=hermes_hermes-agent-src" in joined
    assert "--user root" in joined
    fp = fingerprint(info)
    assert "lobodesk-proxy" in fp["network_extra"]
    assert ("volume", "a1b2c3d4e5f6anonymousoptdata", "/opt/data", True) in fp["mounts"]
    plan = public_plan("hermes-agent", info, extra, argv)
    blob = json.dumps(plan)
    assert "OPENROUTER_API_KEY" in plan["env_keys"]
    assert "REDACTED" not in blob


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
        create_argv("harvey", info, image="x", env_file=None,
                    extra=extra_mounts_for("harvey-sss"))

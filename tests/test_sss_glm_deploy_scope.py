# SPDX-License-Identifier: AGPL-3.0-only
"""Deploy overlay must not replace the custom Harvey dashboard."""

import importlib.util
from pathlib import Path

import pytest

from sss_glm.approve import approve_batch
from sss_glm.preview import REVIEW_PATH

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "deploy" / "harvey-sss"
RECREATE = OVERLAY / "host" / "recreate.py"
APPLY = OVERLAY / "host" / "apply-vps.sh"


def _recreate():
    spec = importlib.util.spec_from_file_location("sss_glm_recreate", RECREATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recreate_refuses_harvey_dashboard_container():
    rec = _recreate()
    with pytest.raises(SystemExit, match="never the custom Harvey dashboard"):
        rec.allowed_target("harvey")
    with pytest.raises(SystemExit):
        rec.inspect("harvey")


def test_recreate_plan_is_sss_worker_only():
    rec = _recreate()
    info = {
        "Config": {"Image": "harvey-sss:v3", "User": "1000:1000",
                   "Cmd": ["sleep", "infinity"], "WorkingDir": "/app", "Env": []},
        "HostConfig": {"Binds": ["/opt/harvey-sss/data:/app/data:rw"],
                       "NetworkMode": "bridge", "RestartPolicy": {"Name": "no"},
                       "Privileged": False, "CapAdd": []},
        "Mounts": [],
    }
    plan = rec.plan_sss(info)
    assert plan["name"] == "harvey-sss"
    assert plan["scope"] == "sss-worker-only"
    assert "harvey" in plan["leaves_alone"]
    assert any("/var/sss-glm/requests" in m for m in plan["mounts"])
    assert any("/var/sss-glm/responses:ro" in m for m in plan["mounts"])
    assert not any(m.split(":")[1].split(":")[0] == "/app" for m in plan["mounts"]
                   if m.startswith("/opt/sss-glm"))
    argv = rec.docker_run_argv(plan, None)
    assert argv[argv.index("--name") + 1] == "harvey-sss"
    assert "harvey" not in argv or argv[argv.index("--name") + 1] != "harvey"


def test_apply_recreate_refuses_without_confirm(monkeypatch):
    rec = _recreate()
    monkeypatch.delenv("SSS_GLM_CONFIRM_RECREATE", raising=False)
    with pytest.raises(SystemExit, match="SSS_GLM_CONFIRM_RECREATE"):
        rec.apply_recreate("harvey-sss", {"blockers": []}, {}, "/tmp/no-env")


def test_overlay_has_no_custom_dashboard_files():
    forbidden = ("studio.html", "studio.js", "home.js", "home_crm.js",
                 "dashboard.py", "index.html")
    found = []
    for p in OVERLAY.rglob("*"):
        if p.name in forbidden or p.suffix in {".html", ".css"} and "web" in p.parts:
            found.append(str(p.relative_to(OVERLAY)))
    assert found == []
    data = OVERLAY / "overlay" / "data"
    names = {p.name for p in data.iterdir() if p.name != "__pycache__"}
    allowed = {
        "sss_engine.py", "sss_spaced_send.py", "legacy_permission.py",
        "legacy_release.py", "legacy_validate.py", "sss_glm_preview.py",
        "sss_glm_approve.py", "sss_glm",
    }
    assert names <= allowed
    assert "sss_engine.py" in names and "sss_spaced_send.py" in names


def test_apply_script_cannot_target_harvey_container():
    text = APPLY.read_text()
    assert "docker cp" in text
    assert "hermes-agent:" in text
    assert "harvey:" not in text
    assert "NEVER docker cp/recreate container \"harvey\"" in text
    assert "SSS_GLM_CONFIRM_RECREATE=harvey-sss-only" in text
    assert "/opt/harvey-sss/data" in text
    assert "harvey.dmtoclose.com" in text


def test_review_path_does_not_claim_custom_or_upstream_ui():
    low = REVIEW_PATH.lower()
    assert "not wired" in low
    assert "harvey.dmtoclose.com" in low
    assert "sellbird dashboard" not in low
    assert "127.0.0.1:5555" not in REVIEW_PATH
    result = approve_batch  # import used so review_ui stays in this module
    assert result is not None
    from sss_glm.approve import approve_batch as ab
    src = Path(ab.__code__.co_filename).read_text()
    assert "not wired" in src
    assert "sellbird dashboard" not in src

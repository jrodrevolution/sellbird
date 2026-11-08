# SPDX-License-Identifier: AGPL-3.0-only
"""Deploy overlay must not replace the custom Harvey dashboard."""

import importlib.util
from pathlib import Path

import pytest

from sss_glm.approve import REVIEW_UI
from sss_glm.preview import REVIEW_PATH

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "deploy" / "harvey-sss"
RECREATE = OVERLAY / "host" / "recreate.py"
APPLY = OVERLAY / "host" / "apply-vps.sh"
DASH = OVERLAY / "host" / "apply-dashboard.sh"


def _recreate():
    spec = importlib.util.spec_from_file_location("sss_glm_recreate", RECREATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recreate_refuses_harvey_dashboard_container():
    rec = _recreate()
    with pytest.raises(SystemExit, match="never the custom Harvey dashboard"):
        rec.allowed_target("harvey")


def test_apply_recreate_refuses_without_confirm(monkeypatch):
    rec = _recreate()
    monkeypatch.delenv("SSS_GLM_CONFIRM_RECREATE", raising=False)
    with pytest.raises(SystemExit, match="SSS_GLM_CONFIRM_RECREATE"):
        rec.apply_recreate("harvey-sss", {"Config": {}, "HostConfig": {}, "Mounts": []})


def test_overlay_has_no_custom_dashboard_files():
    forbidden = ("studio.html", "studio.js", "home.js", "home_crm.js",
                 "dashboard.py", "index.html")
    found = []
    for p in OVERLAY.rglob("*"):
        if p.name in forbidden or p.suffix in {".html", ".css"} and "web" in p.parts:
            found.append(str(p.relative_to(OVERLAY)))
    assert found == []


def test_apply_vps_does_not_cp_into_harvey():
    text = APPLY.read_text()
    assert "hermes-agent:" in text
    assert "harvey:" not in text
    assert "SSS_GLM_CONFIRM_RECREATE=harvey-sss-only" in text
    assert "/opt/harvey-sss/data" in text


def test_dashboard_script_is_surgical():
    text = DASH.read_text()
    assert "sss_glm_studio.py" in text
    assert "Never recreates container harvey" in text
    assert not any(
        ln.strip().startswith("docker restart") for ln in text.splitlines()
    )
    assert "reload_process.py" in text
    assert "18765" in text
    assert "sss_glm_studio.js" in text


def test_review_path_points_at_studio_not_terminal_outbox():
    low = REVIEW_PATH.lower()
    assert "#sssglm" in low
    assert "sellbird dashboard" not in low
    assert "not this ui" in low
    assert "#sssglm" in REVIEW_UI.lower() or "sssglm" in REVIEW_UI.lower()

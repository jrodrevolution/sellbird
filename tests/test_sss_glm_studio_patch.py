# SPDX-License-Identifier: AGPL-3.0-only
"""Surgical Studio inject uses production anchors and does not add a send API."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "deploy" / "harvey-custom" / "patch_live.py"
STUDIO_PY = ROOT / "deploy" / "harvey-custom" / "harvey" / "sss_glm_studio.py"
STUDIO_JS = ROOT / "deploy" / "harvey-custom" / "harvey" / "web" / "sss_glm_studio.js"


def _patch():
    spec = importlib.util.spec_from_file_location("sss_glm_patch_live", PATCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_patch_injects_anchors_once(tmp_path):
    root = tmp_path / "harvey"
    web = root / "web"
    web.mkdir(parents=True)
    (root / "dashboard.py").write_text(
        "    from harvey import studio_hm9 as _studio_hm9   # v9: fit + qualifying questions\n"
        "    _studio_hm9.register(app, DB_PATH)\n"
        "except Exception as _e:\n"
        "    logger.warning(\"studio hm not loaded: %s\", _e)\n",
        encoding="utf-8",
    )
    (web / "studio.html").write_text(
        '  <a data-v="settings">⚙<span>Settings</span></a>\n'
        ' <script src="/static/studio_hm9.js"></script>\n',
        encoding="utf-8",
    )
    mod = _patch()
    first = mod.patch_files(root, apply=True)
    assert first["changed"]["dashboard.py"] and first["changed"]["studio.html"]
    second = mod.patch_files(root, apply=True)
    assert second["changed"] == {"dashboard.py": False, "studio.html": False}
    dash = (root / "dashboard.py").read_text()
    html = (web / "studio.html").read_text()
    assert "sss_glm_studio" in dash
    assert 'data-v="sssglm"' in html
    assert "sss_glm_studio.js" in html
    assert (root / "dashboard.py.pre-sss-glm").is_file()


def test_studio_proxy_has_no_send_route():
    src = STUDIO_PY.read_text()
    assert "/api/sss-glm/preview" in src
    assert "/api/sss-glm/approve" in src
    assert "/api/sss-glm/reject" in src
    assert "/api/sss-glm/send" not in src
    assert "send_forbidden" in src
    js = STUDIO_JS.read_text()
    assert "No send button" in js or "no send" in js.lower()
    assert "/api/sss-glm/send" not in js
    assert "confirm: 'approve'" in js

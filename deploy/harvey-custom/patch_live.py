#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Surgical inject of SSS GLM review into the live custom dashboard.

Never overwrites dashboard.py / studio.html wholesale (those files in the
sanitized archive are redacted and must not replace production). Fails if
anchors are missing. Default is dry-run.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DASH_ANCHOR = """    from harvey import studio_hm9 as _studio_hm9   # v9: fit + qualifying questions
    _studio_hm9.register(app, DB_PATH)
except Exception as _e:
    logger.warning("studio hm not loaded: %s", _e)
"""

DASH_INSERT = """
# SSS GLM review proxy (approval-only, no send). Internal network to harvey-sss.
try:
    from harvey import sss_glm_studio as _sss_glm_studio
    _sss_glm_studio.register(app)
except Exception as _e:
    logger.warning("sss glm studio not loaded: %s", _e)
"""

STUDIO_NAV_ANCHOR = '  <a data-v="settings">⚙<span>Settings</span></a>'
STUDIO_NAV_INSERT = '  <a data-v="sssglm">✉<span>SSS drafts</span></a>\n  <a data-v="settings">⚙<span>Settings</span></a>'

STUDIO_JS_ANCHOR = '<script src="/static/studio_hm9.js"></script>'
STUDIO_JS_INSERT = '<script src="/static/studio_hm9.js"></script>\n<script src="/static/sss_glm_studio.js"></script>'


def once(text: str, anchor: str, insert: str, already: str) -> str:
    if already in text:
        return text
    if anchor not in text:
        raise SystemExit(f"anchor missing: {anchor[:60]!r}")
    return text.replace(anchor, insert, 1)


def patch_files(root: Path, apply: bool) -> dict:
    dash = root / "dashboard.py"
    html = root / "web" / "studio.html"
    if not dash.is_file() or not html.is_file():
        raise SystemExit(f"custom dashboard files missing under {root}")
    d_src = dash.read_text(encoding="utf-8")
    h_src = html.read_text(encoding="utf-8")
    d_new = once(d_src, DASH_ANCHOR, DASH_ANCHOR + DASH_INSERT, "sss_glm_studio")
    h_new = once(h_src, STUDIO_NAV_ANCHOR, STUDIO_NAV_INSERT, 'data-v="sssglm"')
    h_new = once(h_new, STUDIO_JS_ANCHOR, STUDIO_JS_INSERT, "sss_glm_studio.js")
    changed = {"dashboard.py": d_new != d_src, "studio.html": h_new != h_src}
    if apply:
        if changed["dashboard.py"]:
            shutil.copy2(dash, str(dash) + ".pre-sss-glm")
            dash.write_text(d_new, encoding="utf-8")
        if changed["studio.html"]:
            shutil.copy2(html, str(html) + ".pre-sss-glm")
            html.write_text(h_new, encoding="utf-8")
    return {"apply": apply, "changed": changed, "root": str(root)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="/app/harvey")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)
    print(patch_files(Path(args.root), args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

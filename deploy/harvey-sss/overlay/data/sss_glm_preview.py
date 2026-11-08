#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only GLM batch preview inside harvey-sss. Never approves or sends."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if "/app/data" not in sys.path:
    sys.path.insert(0, "/app/data")
from sss_glm.preview import main
if __name__ == "__main__":
    raise SystemExit(main())

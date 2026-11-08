#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed Hermes entry for the SSS GLM channel. No user paths or commands."""
import os
import sys

# File launch: .../approved/sss_glm/worker_main.py — script dir is the package,
# not the parent that must be on sys.path. Do not rely on PYTHONPATH.
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
from sss_glm.worker import main
if __name__ == "__main__":
    raise SystemExit(main())

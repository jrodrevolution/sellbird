#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only GLM batch preview inside harvey-sss. Never approves or sends."""
import sys
sys.path.insert(0, "/app/data")
from sss_glm.preview import main
if __name__ == "__main__":
    raise SystemExit(main())

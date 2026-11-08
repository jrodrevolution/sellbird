#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit human confirm for a GLM batch. Default is dry. Never sends."""
import sys
sys.path.insert(0, "/app/data")
from sss_glm.approve import main
if __name__ == "__main__":
    raise SystemExit(main())

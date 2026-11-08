#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Fixed Hermes entry for the SSS GLM channel. No user paths or commands."""
import sys
sys.path.insert(0, "/opt/hermes/.hermes/scripts/approved")
from sss_glm.worker import main
if __name__ == "__main__":
    raise SystemExit(main())

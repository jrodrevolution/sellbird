#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Narrow recreate of harvey-sss: keep image, uid, sleep infinity, existing
# binds; add GLM request RW + response RO. Default dry-run.
# Never pass container "harvey" (custom dashboard). Requires
# SSS_GLM_CONFIRM_RECREATE=harvey-sss-only for --apply.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
APPLY=()
if [[ "${1:-}" == "--apply" ]]; then APPLY=(--apply); fi
exec python3 "$HERE/recreate.py" harvey-sss "${APPLY[@]}"

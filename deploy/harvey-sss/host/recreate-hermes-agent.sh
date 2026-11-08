#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Narrow recreate of hermes-agent: add request RO, response RW, ledger RW.
# Default dry-run. Does not copy OpenRouter keys anywhere.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
APPLY=()
if [[ "${1:-}" == "--apply" ]]; then APPLY=(--apply); fi
exec python3 "$HERE/recreate.py" hermes-agent "${APPLY[@]}"

#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# One no-recipient GLM call inside hermes-agent. Production stays untouched:
# does not copy into approved scripts, does not write /var/sss-glm,
# outbox, jobs, or the custom dashboard. Run this BEFORE recreate.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
PKG=""
if [[ -d "$ROOT/overlay/data/sss_glm" ]]; then
  PKG="$ROOT/overlay/data/sss_glm"
elif [[ -d "$ROOT/../../sss_glm" ]]; then
  PKG="$ROOT/../../sss_glm"
fi
if [[ -z "$PKG" || ! -f "$PKG/synthetic_accept.py" ]]; then
  echo "sss_glm/synthetic_accept.py missing in this tree" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1 || ! docker inspect hermes-agent >/dev/null 2>&1; then
  echo "hermes-agent not running; not calling the model" >&2
  exit 1
fi
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
REMOTE="/tmp/sss-glm-synthetic-${STAMP}"
cleanup() {
  docker exec -u 0 hermes-agent /opt/hermes/.venv/bin/python -c \
    "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" \
    "$REMOTE" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker exec -u 0 hermes-agent /opt/hermes/.venv/bin/python -c \
  "import os,sys; os.makedirs(sys.argv[1], exist_ok=True)" "$REMOTE"
docker cp "$PKG/." "hermes-agent:${REMOTE}/sss_glm/"
echo "synthetic accept: hermes-agent ${REMOTE} (prod approved scripts and /var/sss-glm not used)"
# UID matches the live worker identity the operator probed (numeric 1000 / hermes).
set +e
docker exec -u 1000 -w "$REMOTE" hermes-agent /opt/hermes/.venv/bin/python -m sss_glm.synthetic_accept
rc=$?
set -e
echo "synthetic accept exit=$rc (no outbox, no jobs file, no recipient)"
exit "$rc"

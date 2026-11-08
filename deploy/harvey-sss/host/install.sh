#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Stage overlay files for a later parent deploy. Does not touch live containers,
# databases, outbox rows, SMTP, Hermes jobs, DMTC, Telegram, or the 8 PM report.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
STAGE=${1:-/tmp/sss-glm-stage}
mkdir -p "$STAGE/overlay/data" "$STAGE/overlay/hermes" "$STAGE/systemd" "$STAGE/preservation"
cp -a "$HERE/overlay/data/." "$STAGE/overlay/data/"
cp -a "$HERE/overlay/hermes/." "$STAGE/overlay/hermes/"
cp -a "$HERE/systemd/." "$STAGE/systemd/"
cp -a "$HERE/preservation/." "$STAGE/preservation/"
# Package used by SSS (/app/data/sss_glm) and Hermes (approved/sss_glm).
REPO=$(cd "$HERE/../.." && pwd)
if [[ -d "$REPO/sss_glm" ]]; then
  mkdir -p "$STAGE/overlay/data/sss_glm" "$STAGE/overlay/hermes/sss_glm"
  cp -a "$REPO/sss_glm/." "$STAGE/overlay/data/sss_glm/"
  cp -a "$REPO/sss_glm/." "$STAGE/overlay/hermes/sss_glm/"
  cp "$HERE/overlay/hermes/worker_main.py" "$STAGE/overlay/hermes/sss_glm/worker_main.py"
fi
find "$STAGE" -type d -name '__pycache__' -exec rm -rf {} +
find "$STAGE" -name '*.pyc' -delete
if [[ -d "$HERE/../harvey-custom" ]]; then
  mkdir -p "$STAGE/harvey-custom"
  cp -a "$HERE/../harvey-custom/." "$STAGE/harvey-custom/"
fi
cp "$HERE/README.md" "$HERE/SCOPE.md" "$STAGE/"
mkdir -p "$STAGE/host"
cp -a "$HERE/host/." "$STAGE/host/"
cat > "$STAGE/COPY_TO_VPS.txt" <<'EOF'
Read SCOPE.md first.

1. bash host/apply-vps.sh
   bash host/apply-vps.sh --apply
2. bash host/apply-dashboard.sh
   bash host/apply-dashboard.sh --apply --reload-dashboard
   (new Studio files + two anchors only; does not recreate or docker restart
   harvey. Captures :5555 and scoped-relaunches that argv.)
3. Recreate only after dry-run JSON keeps 127.0.0.1:5556, Hermes
   Entrypoint ["/init","/opt/hermes/docker/main-wrapper.sh"] + Cmd
   ["gateway","run"], /opt/data, lobodesk-proxy:
   SSS_GLM_CONFIRM_RECREATE=harvey-sss-only bash host/apply-vps.sh --apply --recreate
4. bash host/apply-vps.sh --apply --enable-timer
5. bash host/verify-install.sh

Owner review path after deploy: Access hostname /#sssglm
SSS :5556 and Sellbird/terminal Outbox are not that UI.
Never copy secrets. Never SMTP-test prospects. Never recreate harvey from scratch.
Never docker restart harvey or harvey-sss (PID1 is sleep infinity).
EOF
echo "staged $STAGE"
echo "This script did not contact Docker, SMTP, or production databases."

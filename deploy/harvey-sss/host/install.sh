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
cp "$HERE/README.md" "$HERE/SCOPE.md" "$STAGE/"
mkdir -p "$STAGE/host"
cp -a "$HERE/host/." "$STAGE/host/"
cat > "$STAGE/COPY_TO_VPS.txt" <<'EOF'
Read SCOPE.md first. This overlay is harvey-sss worker files only.
Do NOT docker cp or recreate container "harvey" (harvey.dmtoclose.com).
Do NOT present Sellbird/terminal Outbox as the custom web UI.

Preferred apply (dry-run, then files only, no recreate):
  bash host/apply-vps.sh
  bash host/apply-vps.sh --apply
  bash host/verify-install.sh

Channel mounts need a later recreate of harvey-sss and hermes-agent only:
  SSS_GLM_CONFIRM_RECREATE=harvey-sss-only bash host/apply-vps.sh --apply --recreate
Confirm first that any SSS localhost :5556 UI lives in the image or a volume.

Never copy secrets. Never modify outbox rows. Never SMTP-test prospects.
EOF
echo "staged $STAGE"
echo "This script did not contact Docker, SMTP, or production databases."

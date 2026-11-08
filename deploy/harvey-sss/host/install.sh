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
cp "$HERE/host/channel-layout.sh" "$HERE/host/rollback.sh" "$HERE/host/recreate-harvey-sss.snippet.sh" "$STAGE/"
cat > "$STAGE/COPY_TO_VPS.txt" <<'EOF'
1. Back up, on the VPS, before any copy:
     cp -a /opt/harvey-sss/data/sss_engine.py /opt/harvey-sss/data/sss_engine.py.pre-glm
     cp -a /opt/harvey-sss/data/sss_spaced_send.py /opt/harvey-sss/data/sss_spaced_send.py.pre-glm
     cp -a /opt/harvey-sss/data/legacy_permission.py /opt/harvey-sss/data/legacy_permission.py.pre-glm
   Do not restore those from any older snapshot than this preservation/ copy.
2. Copy overlay/data/*.py and overlay/data/sss_glm/ to /opt/harvey-sss/data/ (uid 1000).
   Leave legacy_permission.py, legacy_release.py, legacy_validate.py as the
   preservation copies (identical to current production).
3. Copy overlay/hermes/sss_glm/ to the Hermes approved-scripts directory:
     /opt/hermes/.hermes/scripts/approved/sss_glm/
   inside hermes-agent (same place as the existing zero-tool GLM probe).
4. Run channel-layout.sh as root.
5. Recreate harvey-sss with the two extra mounts in recreate-harvey-sss.snippet.sh
   PLUS the three existing mounts. Command remains sleep infinity, image harvey-sss:v3, uid 1000.
6. Recreate or extra-mount hermes-agent: requests RO, responses RW, ledger RW.
7. Install systemd/sss-glm-worker.{service,timer}. Do not change other timers/jobs.
8. Verify: mounts, no keys in SSS, fixture request → pending_review, legacy 83 unchanged.
EOF
echo "staged $STAGE"
echo "This script did not contact Docker, SMTP, or production databases."

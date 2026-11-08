#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Rollback GLM overlay. Does not send mail, does not rewrite outbox rows,
# does not touch DMTC, report job a79ca986e5d2, exporter, Telegram, or
# container "harvey" (custom dashboard at harvey.dmtoclose.com).
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root on the VPS" >&2
  exit 1
fi
systemctl stop sss-glm-worker.timer 2>/dev/null || true
systemctl disable sss-glm-worker.timer 2>/dev/null || true
# Restore writer/sender from the pre-glm backups taken at install.
if [[ -f /opt/harvey-sss/data/sss_engine.py.pre-glm ]]; then
  cp -a /opt/harvey-sss/data/sss_engine.py.pre-glm /opt/harvey-sss/data/sss_engine.py
  chown 1000:1000 /opt/harvey-sss/data/sss_engine.py
fi
if [[ -f /opt/harvey-sss/data/sss_spaced_send.py.pre-glm ]]; then
  cp -a /opt/harvey-sss/data/sss_spaced_send.py.pre-glm /opt/harvey-sss/data/sss_spaced_send.py
  chown 1000:1000 /opt/harvey-sss/data/sss_spaced_send.py
fi
# Never replace legacy_* from a stale tree; leave current files in place.
echo "Restored sss_engine.py and sss_spaced_send.py from pre-glm backups if present."
echo "Recreate harvey-sss WITHOUT the /var/sss-glm mounts, keeping the original three mounts,"
echo "image harvey-sss:v3, uid 1000, command sleep infinity."
echo "Leave /opt/sss-glm-channel in place (unused) or root-archive it; do not delete /opt/harvey-sss/data."
# Swap exact-config pre-glm containers back if present. Do not start extra services.
for name in harvey-sss hermes-agent; do
  if docker inspect "${name}.pre-glm" >/dev/null 2>&1; then
    docker stop "$name" 2>/dev/null || true
    docker rename "$name" "${name}.glmfailed" 2>/dev/null || true
    docker rename "${name}.pre-glm" "$name"
    docker start "$name"
    echo "restored $name from ${name}.pre-glm"
  fi
done
# Revert surgical dashboard patches if backups exist; never replace whole UI.
if docker inspect harvey >/dev/null 2>&1; then
  docker exec -u 0 harvey sh -c '
    if [ -f /app/harvey/dashboard.py.pre-sss-glm ]; then
      cp -a /app/harvey/dashboard.py.pre-sss-glm /app/harvey/dashboard.py
    fi
    if [ -f /app/harvey/web/studio.html.pre-sss-glm ]; then
      cp -a /app/harvey/web/studio.html.pre-sss-glm /app/harvey/web/studio.html
    fi
    rm -f /app/harvey/sss_glm_studio.py /app/harvey/web/sss_glm_studio.js
  ' || true
  echo "dashboard surgical patch reverted if backups were present. restart harvey to unload routes."
fi
echo "Pending GLM drafts stay pending_review/held. Never recreate container harvey from scratch."

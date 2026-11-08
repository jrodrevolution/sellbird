#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Rollback GLM overlay. Does not send mail, does not rewrite outbox rows,
# does not touch DMTC, report job a79ca986e5d2, exporter, Telegram, or
# recreate container "harvey" (custom dashboard). Never docker restart
# (PID1 is sleep infinity; dashboard is a separate process). Slim Harvey
# has no kill/rm/chown/sh; in-container work uses python os.* only.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root on the VPS" >&2
  exit 1
fi
systemctl stop sss-glm-worker.timer 2>/dev/null || true
systemctl disable sss-glm-worker.timer 2>/dev/null || true
SSS_DATA=/opt/harvey-sss/data
# Restore writer/sender/legacy wrappers from the pre-glm backups taken at install.
restore_file() {
  local src=$1
  if [[ -f "${src}.pre-glm" ]]; then
    cp -a "${src}.pre-glm" "$src"
    chown 1000:1000 "$src"
    echo "restored $src"
  fi
}
restore_file "$SSS_DATA/sss_engine.py"
restore_file "$SSS_DATA/sss_spaced_send.py"
restore_file "$SSS_DATA/legacy_permission.py"
restore_file "$SSS_DATA/legacy_release.py"
restore_file "$SSS_DATA/legacy_validate.py"
restore_file "$SSS_DATA/sss_glm_preview.py"
restore_file "$SSS_DATA/sss_glm_approve.py"
if [[ -d "$SSS_DATA/sss_glm.pre-glm" ]]; then
  rm -rf "$SSS_DATA/sss_glm"
  cp -a "$SSS_DATA/sss_glm.pre-glm" "$SSS_DATA/sss_glm"
  chown -R 1000:1000 "$SSS_DATA/sss_glm"
  echo "restored $SSS_DATA/sss_glm"
fi
# Never replace legacy_* from a stale tree if no .pre-glm backup existed.
echo "Restored SSS scripts from pre-glm backups if present."
echo "Recreate harvey-sss WITHOUT the /var/sss-glm mounts, keeping the original three mounts,"
echo "image harvey-sss:v3, uid 1000, command sleep infinity."
echo "Leave /opt/sss-glm-channel in place (unused) or root-archive it; do not delete /opt/harvey-sss/data."
# Swap exact-config pre-glm containers back if present. Do not start extra services.
# Operator apply of 4524bd4 did not recreate; this is a no-op when .pre-glm is absent.
for name in harvey-sss hermes-agent; do
  if docker inspect "${name}.pre-glm" >/dev/null 2>&1; then
    docker stop "$name" 2>/dev/null || true
    docker rename "$name" "${name}.glmfailed" 2>/dev/null || true
    docker rename "${name}.pre-glm" "$name"
    docker start "$name"
    echo "restored $name from ${name}.pre-glm"
  fi
done
# Revert surgical dashboard patches via python (no sh/rm/cp inside slim Harvey).
if docker inspect harvey >/dev/null 2>&1; then
  python3 "$HERE/reload_process.py" fs harvey --op revert-sss-glm-inject || \
    echo "NOTE: dashboard file revert via python failed; not calling sh/rm/kill"
  # Scoped reload so the separately launched dashboard unloads old routes.
  # Same os.kill path as apply; never docker restart.
  if python3 "$HERE/reload_process.py" reload-dashboard harvey; then
    echo "dashboard surgical patch reverted; scoped reload (os.kill, no docker restart)."
  else
    echo "dashboard files reverted on disk if backups were present."
    echo "scoped reload failed; old listener left running. refusing docker restart."
  fi
fi
echo "Pending GLM drafts stay pending_review/held. Never recreate container harvey from scratch."
echo "Never docker restart harvey or harvey-sss."

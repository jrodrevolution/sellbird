#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Install GLM worker overlay onto harvey-sss data + Hermes approved scripts.
# Default: dry-run. --apply writes ONLY:
#   /opt/harvey-sss/data/{sss_glm,sss_engine.py,sss_spaced_send.py,legacy_*,sss_glm_*}
#   hermes-agent:/opt/hermes/.hermes/scripts/approved/sss_glm/
#   optional systemd units
# NEVER docker cp/recreate container "harvey" from this worker script.
# Dashboard inject is host/apply-dashboard.sh (new files + surgical patch only).
# Never sends mail, never approves outbox rows, never copies keys.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
APPLY=0
RECREATE=0
ENABLE_TIMER=0
STAGE=""
SSS_DATA=/opt/harvey-sss/data
HERMES_APPROVED=/opt/hermes/.hermes/scripts/approved
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --recreate) RECREATE=1 ;;
    --enable-timer) ENABLE_TIMER=1 ;;
    --stage=*) STAGE="${arg#--stage=}" ;;
    -h|--help)
      cat <<'EOF'
apply-vps.sh [--apply] [--recreate] [--enable-timer]
  --apply          copy overlay files (with backups). default is dry-run.
  --recreate       after copy, recreate harvey-sss and hermes-agent mounts.
  --enable-timer   install/start sss-glm-worker.timer (does not send).
Never pass secrets. Does not modify outbox rows or recipient manifests.
EOF
      exit 0 ;;
  esac
done

need=(
  "$ROOT/overlay/data/sss_engine.py"
  "$ROOT/overlay/data/sss_spaced_send.py"
  "$ROOT/overlay/data/legacy_permission.py"
  "$ROOT/overlay/data/legacy_release.py"
  "$ROOT/overlay/data/legacy_validate.py"
  "$ROOT/overlay/hermes/worker_main.py"
  "$ROOT/preservation/legacy_permission.py"
)
PKG=""
if [[ -d "$ROOT/overlay/data/sss_glm" ]]; then
  PKG="$ROOT/overlay/data/sss_glm"
elif [[ -d "$ROOT/../../sss_glm" ]]; then
  PKG="$ROOT/../../sss_glm"
fi
if [[ -z "$PKG" || ! -f "$PKG/enqueue.py" ]]; then
  echo "sss_glm package missing next to overlay or at overlay/data/sss_glm" >&2
  exit 1
fi
for f in "${need[@]}"; do
  [[ -f "$f" ]] || { echo "missing $f" >&2; exit 1; }
done

echo "PLAN"
echo "  package: $PKG"
echo "  SSS data: $SSS_DATA"
echo "  copy sss_glm FIRST, then overlay scripts (legacy_* from preservation)"
echo "  backups: $SSS_DATA/*.pre-glm"
echo "  no outbox updates, no SMTP, no env copy"

if command -v docker >/dev/null 2>&1 && docker inspect harvey >/dev/null 2>&1; then
  echo "NOTE: container 'harvey' exists (custom dashboard). This script will not touch it."
fi
echo "  scope: harvey-sss data + hermes approved scripts ONLY"
echo "  will not docker cp/recreate container 'harvey'"
echo "  will not write /app/web, studio, dashboard.py"
if [[ "$RECREATE" -eq 1 && "${SSS_GLM_CONFIRM_RECREATE:-}" != "harvey-sss-only" ]]; then
  echo "refusing --recreate without SSS_GLM_CONFIRM_RECREATE=harvey-sss-only" >&2
  echo "Confirm first: custom dashboard container 'harvey' stays up; harvey-sss" >&2
  echo "/app writable-layer files (any localhost :5556 UI) are in the image or a volume." >&2
  exit 1
fi
if [[ "$APPLY" -ne 1 ]]; then
  echo "dry-run complete; re-run with --apply as root on the VPS"
  exit 0
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "--apply requires root" >&2
  exit 1
fi

"$HERE/channel-layout.sh"

install -d -o 1000 -g 1000 -m 0755 "$SSS_DATA"
# Package first so a half-applied sender cannot freeze legacy 83.
rm -rf "$SSS_DATA/sss_glm"
mkdir -p "$SSS_DATA/sss_glm"
cp -a "$PKG/." "$SSS_DATA/sss_glm/"
# Drop test-only helpers from the live tree if present.
rm -f "$SSS_DATA/sss_glm/fakes.py" "$SSS_DATA/sss_glm/live_dry_run.py"
chown -R 1000:1000 "$SSS_DATA/sss_glm"
find "$SSS_DATA/sss_glm" -type d -exec chmod 0755 {} \;
find "$SSS_DATA/sss_glm" -type f -exec chmod 0644 {} \;

backup() {
  local src=$1
  if [[ -f "$src" && ! -f "${src}.pre-glm" ]]; then
    cp -a "$src" "${src}.pre-glm"
  fi
}
backup "$SSS_DATA/sss_engine.py"
backup "$SSS_DATA/sss_spaced_send.py"
backup "$SSS_DATA/legacy_permission.py"
backup "$SSS_DATA/legacy_release.py"
backup "$SSS_DATA/legacy_validate.py"

# Legacy files: preservation copies (byte-identical current production).
cp -a "$ROOT/preservation/legacy_permission.py" "$SSS_DATA/legacy_permission.py"
cp -a "$ROOT/preservation/legacy_release.py" "$SSS_DATA/legacy_release.py"
cp -a "$ROOT/preservation/legacy_validate.py" "$SSS_DATA/legacy_validate.py"
cp -a "$ROOT/overlay/data/sss_engine.py" "$SSS_DATA/sss_engine.py"
cp -a "$ROOT/overlay/data/sss_spaced_send.py" "$SSS_DATA/sss_spaced_send.py"
cp -a "$ROOT/overlay/data/sss_glm_preview.py" "$SSS_DATA/sss_glm_preview.py"
cp -a "$ROOT/overlay/data/sss_glm_approve.py" "$SSS_DATA/sss_glm_approve.py"
chown 1000:1000 "$SSS_DATA"/{sss_engine.py,sss_spaced_send.py,legacy_permission.py,legacy_release.py,legacy_validate.py,sss_glm_preview.py,sss_glm_approve.py}
chmod 0644 "$SSS_DATA"/{sss_engine.py,sss_spaced_send.py,legacy_permission.py,legacy_release.py,legacy_validate.py,sss_glm_preview.py,sss_glm_approve.py}

if docker inspect hermes-agent >/dev/null 2>&1; then
  # Worker scripts only. Never docker cp into container "harvey".
  docker exec -u 0 hermes-agent mkdir -p /opt/hermes/.hermes/scripts/approved/sss_glm
  docker cp "$PKG/." hermes-agent:/opt/hermes/.hermes/scripts/approved/sss_glm/
  docker cp "$ROOT/overlay/hermes/worker_main.py" \
    hermes-agent:/opt/hermes/.hermes/scripts/approved/sss_glm/worker_main.py
  docker exec -u 0 hermes-agent chown -R 1000:1000 /opt/hermes/.hermes/scripts/approved/sss_glm
  docker exec -u 0 hermes-agent rm -f \
    /opt/hermes/.hermes/scripts/approved/sss_glm/fakes.py \
    /opt/hermes/.hermes/scripts/approved/sss_glm/live_dry_run.py
fi

if [[ "$RECREATE" -eq 1 ]]; then
  python3 "$HERE/recreate.py" harvey-sss --apply
  python3 "$HERE/recreate.py" hermes-agent --apply
fi

if [[ "$ENABLE_TIMER" -eq 1 ]]; then
  install -m 0644 "$ROOT/systemd/sss-glm-worker.service" /etc/systemd/system/sss-glm-worker.service
  install -m 0644 "$ROOT/systemd/sss-glm-worker.timer" /etc/systemd/system/sss-glm-worker.timer
  if [[ -f "$ROOT/systemd/sss-glm-review.service" ]]; then
    install -m 0644 "$ROOT/systemd/sss-glm-review.service" /etc/systemd/system/sss-glm-review.service
  fi
  systemctl daemon-reload
  systemctl enable --now sss-glm-worker.timer
  if [[ -f /etc/systemd/system/sss-glm-review.service ]]; then
    systemctl enable --now sss-glm-review.service
  fi
fi

echo "applied file overlay. outbox rows unchanged. run host/verify-install.sh next."

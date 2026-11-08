#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Narrow custom-dashboard inject. Never recreates container harvey.
# Never docker restart harvey (PID1 is sleep infinity; dashboard is separate).
# Never overwrites studio.js/home.js/dashboard.py wholesale.
# Default dry-run. No SMTP, no outbox writes, no AI/SMTP key copy.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CUSTOM=""
if [[ -d "$HERE/../harvey-custom" ]]; then
  CUSTOM=$(cd "$HERE/../harvey-custom" && pwd)
elif [[ -d "$HERE/../../harvey-custom" ]]; then
  CUSTOM=$(cd "$HERE/../../harvey-custom" && pwd)
fi
if [[ -z "$CUSTOM" || ! -f "$CUSTOM/harvey/sss_glm_studio.py" ]]; then
  echo "harvey-custom overlay missing next to this script" >&2
  exit 1
fi
APPLY=0
RELOAD=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --reload-dashboard) RELOAD=1 ;;
    -h|--help)
      cat <<'EOF'
apply-dashboard.sh [--apply] [--reload-dashboard]
  Copies sss_glm_studio.py/js into container harvey and surgically patches
  dashboard.py + studio.html. Connects harvey and harvey-sss to internal
  network sss-glm-review. Writes a review token into each existing data dir.
  Does not recreate harvey, does not docker restart harvey, does not
  publish 18765, does not send mail. --reload-dashboard captures the :5555
  listener and scoped-relaunches that argv (PID1 stays sleep infinity).
EOF
      exit 0 ;;
  esac
done

echo "PLAN dashboard inject"
echo "  custom: $CUSTOM"
echo "  docker cp only sss_glm_studio.py and sss_glm_studio.js"
echo "  patch dashboard.py + studio.html (anchors required)"
echo "  docker network create --internal sss-glm-review (no host ports)"
echo "  tokens: /opt/harvey/data and /opt/harvey-sss/data (existing binds)"
echo "  will not docker recreate harvey, will not touch cloudflared"
echo "  will not docker restart harvey (PID1 is sleep infinity; dashboard is a separate process)"
echo "  --reload-dashboard captures :5555 argv and scoped-relaunches it; persists systemd keep-dashboard@"
echo "  review server on harvey-sss :18765 starts only if that port is empty (no duplicate)"

if command -v docker >/dev/null 2>&1 && docker inspect harvey >/dev/null 2>&1; then
  python3 "$HERE/reload_process.py" capture harvey 2>/dev/null || \
    echo "NOTE: live capture of harvey :5555 failed; --reload-dashboard will refuse rather than docker restart"
fi
if command -v docker >/dev/null 2>&1 && docker inspect harvey-sss >/dev/null 2>&1; then
  python3 "$HERE/reload_process.py" capture harvey-sss 2>/dev/null || \
    echo "NOTE: live capture of harvey-sss :5555 failed; recreate will refuse without listener metadata"
fi

if [[ "$APPLY" -ne 1 ]]; then
  echo "dry-run complete; re-run with --apply as root on the VPS"
  exit 0
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "--apply requires root" >&2
  exit 1
fi
if ! docker inspect harvey >/dev/null 2>&1; then
  echo "container harvey not found" >&2
  exit 1
fi
if ! docker inspect harvey-sss >/dev/null 2>&1; then
  echo "container harvey-sss not found" >&2
  exit 1
fi

docker cp "$CUSTOM/harvey/sss_glm_studio.py" harvey:/app/harvey/sss_glm_studio.py
docker cp "$CUSTOM/harvey/web/sss_glm_studio.js" harvey:/app/harvey/web/sss_glm_studio.js
docker cp "$CUSTOM/patch_live.py" harvey:/tmp/sss_glm_patch_live.py
docker exec -u 0 harvey python3 /tmp/sss_glm_patch_live.py --root /app/harvey --apply
python3 "$HERE/reload_process.py" fs harvey --op unlink --path /tmp/sss_glm_patch_live.py
python3 "$HERE/reload_process.py" fs harvey --op chown --owner 1000:1000 \
  --path /app/harvey/sss_glm_studio.py --path /app/harvey/web/sss_glm_studio.js

docker network inspect sss-glm-review >/dev/null 2>&1 || \
  docker network create --internal --driver bridge sss-glm-review
docker network connect sss-glm-review harvey 2>/dev/null || true
docker network connect sss-glm-review harvey-sss 2>/dev/null || true

TOKEN=$(openssl rand -hex 24)
umask 077
printf '%s\n' "$TOKEN" > /opt/harvey-sss/data/sss-glm-review.token
printf '%s\n' "$TOKEN" > /opt/harvey/data/sss-glm-review.token
chown 1000:1000 /opt/harvey-sss/data/sss-glm-review.token /opt/harvey/data/sss-glm-review.token
chmod 0640 /opt/harvey-sss/data/sss-glm-review.token /opt/harvey/data/sss-glm-review.token
unset TOKEN

if [[ "$RELOAD" -eq 1 ]]; then
  mkdir -p /root/sss-glm-snapshots/latest
  chmod 0700 /root/sss-glm-snapshots /root/sss-glm-snapshots/latest
  python3 "$HERE/reload_process.py" capture harvey \
    --write /root/sss-glm-snapshots/latest/harvey.procs.json
  python3 "$HERE/reload_process.py" persist harvey \
    --unit /etc/systemd/system/sss-glm-keep-dashboard@harvey.service
  python3 "$HERE/reload_process.py" capture harvey-sss \
    --write /root/sss-glm-snapshots/latest/harvey-sss.procs.json
  python3 "$HERE/reload_process.py" persist harvey-sss \
    --unit /etc/systemd/system/sss-glm-keep-dashboard@harvey-sss.service
  systemctl daemon-reload
  systemctl enable sss-glm-keep-dashboard@harvey.service
  systemctl enable sss-glm-keep-dashboard@harvey-sss.service
  python3 "$HERE/reload_process.py" reload-dashboard harvey
  if [[ -f "$HERE/../systemd/sss-glm-review.service" ]]; then
    install -m 0644 "$HERE/../systemd/sss-glm-review.service" \
      /etc/systemd/system/sss-glm-review.service
    systemctl daemon-reload
    systemctl enable sss-glm-review.service
  fi
  if python3 "$HERE/reload_process.py" listening harvey-sss --port 18765 >/dev/null; then
    echo "review server already listening on harvey-sss :18765; not starting a second"
  else
    python3 "$HERE/reload_process.py" start-review harvey-sss
  fi
  echo "scoped dashboard reload on harvey (no docker restart). Cloudflare Access unchanged."
else
  echo "files patched; later --reload-dashboard captures :5555 and scoped-relaunches it"
  echo "do not docker restart harvey — that would kill the separately launched dashboard"
fi
echo "dashboard inject applied. no sends. review URL path: /#sssglm"

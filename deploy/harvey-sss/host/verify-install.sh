#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Post-install checks. No SMTP, no outbox status changes, no recipient dump
# unless --show-copy (owner, on the VPS, to read GLM drafts from SSS DB).
# Does not open harvey.dmtoclose.com or Sellbird Outbox.
set -euo pipefail
SHOW=0
[[ "${1:-}" == "--show-copy" ]] && SHOW=1
fail=0
say() { echo "$*"; }
bad() { echo "FAIL: $*" >&2; fail=1; }

[[ -d /opt/sss-glm-channel/requests ]] || bad "channel requests missing (run channel-layout.sh)"
[[ -d /opt/sss-glm-channel/responses ]] || bad "channel responses missing"
[[ -d /opt/sss-glm-channel/ledger ]] || bad "channel ledger missing"
[[ "$(stat -c %u:%a /opt/sss-glm-channel 2>/dev/null || echo x)" == 0:* ]] || say "NOTE: channel parent should be root-owned"

if docker inspect harvey-sss >/dev/null 2>&1; then
  mounts=$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{.RW}} {{.Source}};{{end}}' harvey-sss)
  echo "$mounts" | grep -q '/var/sss-glm/requests true' || bad "SSS requests mount not RW"
  echo "$mounts" | grep -q '/var/sss-glm/responses false' || bad "SSS responses mount not RO"
  echo "$mounts" | grep -q '/var/sss-glm/ledger' && bad "SSS must not mount the ledger"
  echo "$mounts" | grep -q docker.sock && bad "SSS has docker.sock"
  env=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' harvey-sss)
  echo "$env" | grep -qi OPENROUTER && bad "OPENROUTER present in SSS env"
  echo "$env" | grep -qi HERMES && say "NOTE: inspect SSS env for accidental Hermes keys"
  docker exec -u 1000 harvey-sss python3 -c "import sss_glm.enqueue,sss_glm.bind; print('sss_glm import ok')" \
    || bad "sss_glm not importable in SSS (need /app/data on path)"
else
  say "NOTE: harvey-sss not running here; skip mount checks"
fi

if docker inspect hermes-agent >/dev/null 2>&1; then
  hm=$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{.RW}};{{end}}' hermes-agent)
  echo "$hm" | grep -q '/var/sss-glm/requests false' || say "NOTE: hermes requests should be RO"
  echo "$hm" | grep -q '/var/sss-glm/responses true' || say "NOTE: hermes responses should be RW"
  echo "$hm" | grep -q '/var/sss-glm/ledger true' || say "NOTE: hermes ledger should be RW"
  docker exec -u hermes hermes-agent test -f /opt/hermes/.hermes/scripts/approved/sss_glm/worker_main.py \
    || bad "Hermes worker_main.py missing"
else
  say "NOTE: hermes-agent not running here; skip Hermes checks"
fi

if [[ "$SHOW" -eq 1 ]]; then
  if docker inspect harvey-sss >/dev/null 2>&1; then
    docker exec -u 1000 harvey-sss python3 /app/data/sss_glm_preview.py --show-copy || true
  else
    python3 "$(dirname "$0")/../overlay/data/sss_glm_preview.py" --show-copy --db /app/data/harvey.db || true
  fi
else
  say "copy hidden (hashes/status only). Re-run with --show-copy for CLI copy. Owner Studio path after deploy: /#sssglm (runtime unverified here)."
fi

if [[ "$fail" -ne 0 ]]; then
  echo "verify failed" >&2
  exit 1
fi
echo "verify ok (no sends, no approval transitions)"
exit 0

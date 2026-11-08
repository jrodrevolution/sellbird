#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Create the host GLM channel OUTSIDE /opt/harvey-sss/data.
# Run as root on the VPS. Does not start containers or send mail.
set -euo pipefail
ROOT=/opt/sss-glm-channel
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi
mkdir -p "$ROOT/requests" "$ROOT/responses" "$ROOT/ledger"
# Parent is root-owned so SSS cannot replace or symlink it.
chown root:root "$ROOT"
chmod 0751 "$ROOT"
# Both containers use uid 1000; isolation is the bind-mount flags, not DAC.
chown 1000:1000 "$ROOT/requests" "$ROOT/responses" "$ROOT/ledger"
chmod 0750 "$ROOT/requests"
chmod 0750 "$ROOT/responses"
chmod 0700 "$ROOT/ledger"
echo "channel ready at $ROOT"
echo "SSS mounts: requests RW, responses RO"
echo "Hermes mounts: requests RO, responses RW, ledger RW (not mounted into SSS)"

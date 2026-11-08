#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Build a sanitized SSS-worker overlay tarball. No secrets, no live DB,
# no recipient manifests, no custom Harvey dashboard files.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
OUT_DIR=${1:-/opt/cursor/artifacts}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="sss-glm-harvey-sss-worker-${STAMP}"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

bash "$REPO/deploy/harvey-sss/host/install.sh" "$STAGE/$NAME"
mkdir -p "$STAGE/$NAME/tests"
cp -a "$REPO/tests"/test_sss_glm_*.py "$REPO/tests/sss_glm_db.py" "$STAGE/$NAME/tests/"
find "$STAGE/$NAME" -type d -name '__pycache__' -exec rm -rf {} +
find "$STAGE/$NAME" -name '*.pyc' -delete
# Identity files at tarball root.
cp -a "$REPO/deploy/harvey-sss/SCOPE.md" "$STAGE/$NAME/SCOPE.md"
cp -a "$REPO/deploy/harvey-sss/README.md" "$STAGE/$NAME/README.md"
# Refuse if dashboard/studio files slipped in.
if find "$STAGE/$NAME" \( -iname '*studio*' -o -iname 'dashboard.py' -o -iname 'home_crm.js' \) \
   | grep -q .; then
  echo "refusing tarball: custom dashboard files present" >&2
  find "$STAGE/$NAME" \( -iname '*studio*' -o -iname 'dashboard.py' -o -iname 'home_crm.js' \)
  exit 1
fi
mkdir -p "$OUT_DIR"
TAR="$OUT_DIR/${NAME}.tar.gz"
tar -C "$STAGE" -czf "$TAR" "$NAME"
( cd "$OUT_DIR" && sha256sum "$(basename "$TAR")" | tee "${NAME}.tar.gz.sha256" )
echo "artifact $TAR"
echo "This archive overlays harvey-sss worker files only; it cannot replace container harvey."

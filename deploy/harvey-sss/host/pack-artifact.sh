#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Build a sanitized SSS-worker overlay tarball. No secrets, no live DB,
# no recipient manifests. Includes surgical Studio SSS-drafts files only.
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
mkdir -p "$STAGE/$NAME/tests/fixtures"
cp -a "$REPO/tests/fixtures"/docker_inspect_*.json "$STAGE/$NAME/tests/fixtures/" 2>/dev/null || true
find "$STAGE/$NAME" -type d -name '__pycache__' -exec rm -rf {} +
find "$STAGE/$NAME" -name '*.pyc' -delete
# Identity files at tarball root.
cp -a "$REPO/deploy/harvey-sss/SCOPE.md" "$STAGE/$NAME/SCOPE.md"
cp -a "$REPO/deploy/harvey-sss/README.md" "$STAGE/$NAME/README.md"
# Refuse wholesale custom UI copies. sss_glm_studio.py/js are the allowed inject.
if find "$STAGE/$NAME" \( -iname 'studio.html' -o -iname 'studio.js' -o -iname 'home.js' \
     -o -iname 'dashboard.py' -o -iname 'home_crm.js' \) | grep -q .; then
  echo "refusing tarball: wholesale custom dashboard files present" >&2
  find "$STAGE/$NAME" \( -iname 'studio.html' -o -iname 'studio.js' -o -iname 'home.js' \
       -o -iname 'dashboard.py' -o -iname 'home_crm.js' \)
  exit 1
fi
mkdir -p "$OUT_DIR"
TAR="$OUT_DIR/${NAME}.tar.gz"
tar -C "$STAGE" -czf "$TAR" "$NAME"
( cd "$OUT_DIR" && sha256sum "$(basename "$TAR")" | tee "${NAME}.tar.gz.sha256" )
echo "artifact $TAR"
echo "This archive overlays harvey-sss worker files and a surgical Studio SSS-drafts inject; it cannot replace container harvey."

#!/usr/bin/env bash
# Auto-bootstrap a plugin-local venv on first activation, then exec the
# bridge from it. Subsequent activations skip the install and start fast.
set -euo pipefail
DIR="${CLAUDE_PLUGIN_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
VENV="${DIR}/.venv"
if [ ! -x "${VENV}/bin/python" ]; then
  echo "[let-him-cook] first run — creating venv at ${VENV}" >&2
  python3 -m venv "${VENV}"
  "${VENV}/bin/pip" install --quiet --upgrade pip
  "${VENV}/bin/pip" install --quiet -r "${DIR}/requirements.txt"
fi
exec "${VENV}/bin/python" -u "${DIR}/bridge.py" \
  --bed-dir "${DIR}/samples"

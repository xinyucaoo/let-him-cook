#!/usr/bin/env bash
# Read the bridge's current port and hit a control endpoint.
#
# Usage: hook-curl.sh <endpoint> [query-string]
#   e.g. hook-curl.sh ignite
#        hook-curl.sh mode/fixed "sample=frying_pan_2.mp3"
#
# Silently no-ops if the bridge isn't running (no port file, empty port,
# or curl fails). Hooks and skills both rely on this — never block a
# user prompt or end-of-turn over a missing bridge.
set -eu
PORT_FILE="${HOME}/.cache/let-him-cook/port"
[ -r "$PORT_FILE" ] || exit 0
PORT=$(cat "$PORT_FILE" 2>/dev/null || echo "")
[ -n "$PORT" ] || exit 0
ENDPOINT="${1:-}"
[ -n "$ENDPOINT" ] || exit 0
QUERY="${2:-}"
URL="http://127.0.0.1:${PORT}/${ENDPOINT}"
[ -n "$QUERY" ] && URL="${URL}?${QUERY}"
curl -s --max-time 2 "$URL" || true

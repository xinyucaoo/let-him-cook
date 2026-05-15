#!/usr/bin/env bash
# Read the bridge's current port and hit a control endpoint.
#
# Usage: hook-curl.sh <endpoint> [query-string]
#   e.g. hook-curl.sh ignite
#        hook-curl.sh mode/fixed "sample=frying_pan_2.mp3"
#
# Always exits 0 so a missing/stopped bridge never blocks a user prompt
# or end-of-turn. Curl output is discarded so the hook contributes no
# stdout to Claude Code (which would otherwise inject it back into the
# prompt context for UserPromptSubmit hooks).
PORT_FILE="${HOME}/.cache/let-him-cook/port"
if [ ! -r "$PORT_FILE" ]; then
  exit 0
fi
PORT=$(cat "$PORT_FILE" 2>/dev/null || true)
if [ -z "$PORT" ]; then
  exit 0
fi
ENDPOINT="${1:-}"
if [ -z "$ENDPOINT" ]; then
  exit 0
fi
URL="http://127.0.0.1:${PORT}/${ENDPOINT}"
if [ -n "${2:-}" ]; then
  URL="${URL}?${2}"
fi
curl -s --max-time 2 -o /dev/null "$URL" || true
exit 0

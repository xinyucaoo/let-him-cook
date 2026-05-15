#!/usr/bin/env bash
# Read the bridge's current port and hit a control endpoint, passing the
# session's transcript path as a query param so the bridge can scope its
# JSONL tailing to one session (avoids cross-session audio bleed).
#
# Usage: hook-curl.sh <endpoint> [extra-query-string]
#   e.g. hook-curl.sh ignite
#        hook-curl.sh mode/fixed "sample=frying_pan_2.mp3"
#
# Always exits 0 so a missing/stopped bridge never blocks a user prompt
# or end-of-turn.

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

# Hooks receive a JSON event on stdin that includes transcript_path. Pull
# it out so the bridge knows which session is driving. python3 is always
# present on macOS/Linux. Skip cleanly if stdin is empty/non-JSON.
TRANSCRIPT=""
if [ ! -t 0 ]; then
  STDIN_DATA=$(cat 2>/dev/null || true)
  if [ -n "$STDIN_DATA" ]; then
    TRANSCRIPT=$(printf '%s' "$STDIN_DATA" | python3 -c '
import sys, json, urllib.parse
try:
    d = json.load(sys.stdin)
    p = d.get("transcript_path", "") or ""
    print(urllib.parse.quote(p, safe=""))
except Exception:
    pass
' 2>/dev/null || true)
  fi
fi

URL="http://127.0.0.1:${PORT}/${ENDPOINT}"
EXTRA_Q="${2:-}"
QS=""
if [ -n "$EXTRA_Q" ]; then
  QS="$EXTRA_Q"
fi
if [ -n "$TRANSCRIPT" ]; then
  if [ -n "$QS" ]; then
    QS="${QS}&transcript=${TRANSCRIPT}"
  else
    QS="transcript=${TRANSCRIPT}"
  fi
fi
if [ -n "$QS" ]; then
  URL="${URL}?${QS}"
fi

curl -s --max-time 2 -o /dev/null "$URL" || true
exit 0

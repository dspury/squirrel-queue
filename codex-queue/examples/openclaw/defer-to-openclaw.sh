#!/usr/bin/env sh
set -eu

STATE_DIR="${CODEX_QUEUE_HOME:-$HOME/.codex-queue}"
REQUEST_DIR="$STATE_DIR/requests"

LATEST_REQUEST="$(ls -t "$REQUEST_DIR"/cq_*.json 2>/dev/null | head -n 1 || true)"

if [ -z "$LATEST_REQUEST" ]; then
  echo "No deferred request found in $REQUEST_DIR" >&2
  exit 1
fi

EVENT_TEXT="Deferred Codex request queued. Read $LATEST_REQUEST and dispatch it from the stored envelope."

openclaw system event --mode next-heartbeat --json --text "$EVENT_TEXT"

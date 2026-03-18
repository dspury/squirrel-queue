#!/usr/bin/env sh
set -eu

STATE_DIR="${CODEX_QUEUE_HOME:-$HOME/.codex-queue}"
REQUEST_DIR="$STATE_DIR/requests"
LOG_FILE="$STATE_DIR/receipts/deferred-requests.log"

LATEST_REQUEST="$(ls -t "$REQUEST_DIR"/cq_*.json 2>/dev/null | head -n 1 || true)"

if [ -z "$LATEST_REQUEST" ]; then
  echo "No deferred request found in $REQUEST_DIR" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"
printf '%s\n' "$LATEST_REQUEST" >> "$LOG_FILE"
printf 'queued %s\n' "$LATEST_REQUEST"

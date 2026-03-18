#!/usr/bin/env bash
# Squirrel v1.5 tmux operator console
#
# Usage: ./scripts/squirrel-console.sh
#
# Creates a tmux session with panes for:
#   1. Commander (run/control)
#   2. Live system watch
#   3. Event log (follow mode)
#   4. Lane status (refreshing)
#
# One command to see everything.

set -euo pipefail

SESSION="squirrel"
SQUIRREL="python -m squirrel.cli"

# Kill existing session if present
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Create session with first pane: Commander
tmux new-session -d -s "$SESSION" -n "commander"

# Pane 0: Commander shell (for run/submit/retry/cancel)
tmux send-keys -t "$SESSION:0.0" "echo '--- Squirrel Commander ---'" Enter
tmux send-keys -t "$SESSION:0.0" "echo 'Use: $SQUIRREL run --agent claude'" Enter
tmux send-keys -t "$SESSION:0.0" "echo 'Or:  $SQUIRREL submit \"objective\"'" Enter

# Split horizontally: Pane 1 — Live watch
tmux split-window -h -t "$SESSION:0"
tmux send-keys -t "$SESSION:0.1" "$SQUIRREL watch" Enter

# Split Pane 0 vertically: Pane 2 — Event log
tmux split-window -v -t "$SESSION:0.0"
tmux send-keys -t "$SESSION:0.2" "$SQUIRREL events -f" Enter

# Split Pane 1 vertically: Pane 3 — Lane status
tmux split-window -v -t "$SESSION:0.1"
tmux send-keys -t "$SESSION:0.3" "watch -n 2 '$SQUIRREL lanes'" Enter

# Select commander pane
tmux select-pane -t "$SESSION:0.0"

# Attach
if [ -n "${TMUX:-}" ]; then
    tmux switch-client -t "$SESSION"
else
    tmux attach-session -t "$SESSION"
fi

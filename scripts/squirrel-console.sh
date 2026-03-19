#!/usr/bin/env bash
# Squirrel v1.8 tmux operator console
#
# Usage: ./scripts/squirrel-console.sh
#
# Creates a tmux session with labeled panes for:
#   1. Commander (run/control)
#   2. Live watch (system state)
#   3. Event log (follow mode)
#   4. Lane status (refreshing)
#
# One command to see everything.

set -euo pipefail

if ! command -v tmux &>/dev/null; then
    echo "ERROR: tmux is not installed."
    echo "  Install with: brew install tmux (macOS) or apt install tmux (Linux)"
    exit 1
fi

SESSION="squirrel"
SQUIRREL="python -m squirrel.cli"

# If session already exists, offer to reattach instead of destroying it
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already exists."
    read -rp "Kill and recreate? [y/N] " answer
    if [[ "${answer,,}" != "y" ]]; then
        echo "Attaching to existing session..."
        if [ -n "${TMUX:-}" ]; then
            tmux switch-client -t "$SESSION"
        else
            tmux attach-session -t "$SESSION"
        fi
        exit 0
    fi
    tmux kill-session -t "$SESSION"
fi

# Create session with first pane: Commander
tmux new-session -d -s "$SESSION" -n "commander"

# Pane 0: Commander shell (for run/submit/retry/cancel)
tmux send-keys -t "$SESSION:0.0" "clear" Enter
tmux send-keys -t "$SESSION:0.0" "echo '╔══════════════════════════════════════╗'" Enter
tmux send-keys -t "$SESSION:0.0" "echo '║     SQUIRREL v1.8 — Commander       ║'" Enter
tmux send-keys -t "$SESSION:0.0" "echo '╚══════════════════════════════════════╝'" Enter
tmux send-keys -t "$SESSION:0.0" "echo ''" Enter
tmux send-keys -t "$SESSION:0.0" "echo 'Commands:'" Enter
tmux send-keys -t "$SESSION:0.0" "echo '  $SQUIRREL run --agent claude'" Enter
tmux send-keys -t "$SESSION:0.0" "echo '  $SQUIRREL submit \"objective\" --criteria \"...\"'" Enter
tmux send-keys -t "$SESSION:0.0" "echo '  $SQUIRREL status'" Enter
tmux send-keys -t "$SESSION:0.0" "echo '  $SQUIRREL history'" Enter
tmux send-keys -t "$SESSION:0.0" "echo ''" Enter

# Split horizontally: Pane 1 — Live watch
tmux split-window -h -t "$SESSION:0"
tmux send-keys -t "$SESSION:0.1" "$SQUIRREL watch" Enter

# Split Pane 0 vertically: Pane 2 — Event log
tmux split-window -v -t "$SESSION:0.0"
tmux send-keys -t "$SESSION:0.2" "$SQUIRREL events -f" Enter

# Split Pane 1 vertically: Pane 3 — Lane status
tmux split-window -v -t "$SESSION:0.1"
tmux send-keys -t "$SESSION:0.3" "watch -n 2 '$SQUIRREL lanes'" Enter

# Label panes with border titles (tmux 3.2+)
tmux set-option -t "$SESSION" pane-border-status top 2>/dev/null || true
tmux select-pane -t "$SESSION:0.0" -T "Commander" 2>/dev/null || true
tmux select-pane -t "$SESSION:0.1" -T "Watch" 2>/dev/null || true
tmux select-pane -t "$SESSION:0.2" -T "Events" 2>/dev/null || true
tmux select-pane -t "$SESSION:0.3" -T "Lanes" 2>/dev/null || true

# Select commander pane
tmux select-pane -t "$SESSION:0.0"

# Attach
if [ -n "${TMUX:-}" ]; then
    tmux switch-client -t "$SESSION"
else
    tmux attach-session -t "$SESSION"
fi

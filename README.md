# Squirrel

A deterministic task execution pipeline that dispatches structured work to AI agents (Claude, Codex, Gemini) through a schema-driven state machine. Work goes in as JSON tasks, comes out as validated artifacts with auditable receipts.

## How It Works

Squirrel processes tasks through a six-phase pipeline:

```
INTAKE → REGISTRY → DECOMPOSE → DISPATCH → VALIDATE → RECEIPT
```

1. **Intake** — Scans the inbox for task files, validates against schema, moves to registry
2. **Registry** — Loads queued tasks, sorts by priority
3. **Decompose** — Breaks tasks into work packets, assigns agent roles
4. **Dispatch** — Executes packets in parallel lanes via pluggable handlers
5. **Validate** — Binary pass/fail check against success criteria
6. **Receipt** — Generates structured output with a 5-line summary

Every task follows a strict state machine (`queued → active → validating → complete/failed/blocked`) with no implicit transitions, no skipped states, and mandatory receipts.

## Quick Start

```bash
# Install
pip install -e .

# Submit a task
squirrel submit "Build a CLI calculator" \
  --priority normal \
  --criteria "calculator.py exists" \
  --criteria "supports add, subtract, multiply, divide"

# Run one execution cycle
squirrel run --agent claude

# Check status
squirrel status
```

### Requirements

- Python 3.11+
- Node.js 18+ (for codex-queue agent dispatch)
- Optional: `codex` or `claude` CLI for local agent execution

## CLI

```bash
squirrel submit <objective> [options]    # Submit a new task
squirrel status [task_id]                # View task status
squirrel run [options]                   # Run one execution cycle
squirrel watch [--interval N]            # Live system state
squirrel lanes [-v]                      # Lane status
squirrel events [--tail N] [-f]          # Event log
squirrel task <task_id>                  # Deep task inspection
squirrel retry <task_id> [--full]        # Re-queue a failed task
squirrel cancel <task_id>                # Cancel a task
squirrel purge [target]                  # Cleanup (inbox|registry|outbox|lanes|all)
```

## Operator Console

A tmux-based console with four panes for real-time observability:

```bash
./scripts/squirrel-console.sh
```

| Pane | View |
|------|------|
| Top-left | Commander shell |
| Top-right | Live watch (system state) |
| Bottom-left | Event log (streaming) |
| Bottom-right | Lane status (refreshing) |

## Architecture

### Task Lifecycle

```
            ┌─────────────────────────────────┐
            │          queued                  │
            └──────┬──────────────────────────┘
                   │ lane_pickup
            ┌──────▼──────────────────────────┐
            │          active                  │
            └──┬───────────────┬──────────────┘
               │               │ dependency_missing
               │          ┌────▼─────────┐
               │          │   blocked    │──── blocker_resolved ───→ queued
               │          └──────────────┘
               │ execution_complete
            ┌──▼──────────────────────────────┐
            │        validating                │
            └──┬───────────────┬──────────────┘
               │               │
       validation_pass    validation_fail
               │               │
            ┌──▼───┐      ┌───▼────┐
            │complete│     │ failed │──── manual_retry ───→ queued
            └───────┘      └────────┘
```

### Directory Layout

```
.squirrel/
├── config/          # Constitution, role definition, control interface spec
│   ├── CONSTITUTION.md
│   ├── ROLE.md
│   └── control_interface.md
├── schemas/         # JSON Schema contracts
│   ├── task.schema.json
│   ├── work_packet.schema.json
│   ├── receipt.schema.json
│   └── state_machine.json
├── inbox/           # External systems write tasks here
├── registry/        # Source of truth for all task state
├── outbox/          # Completed/failed receipts land here
├── control/         # Pause, cancel, retry signals
├── lanes/           # Active work packet state
└── runtime/         # Ephemeral observability (events, commander state, lane state)
```

### Planner & Decomposition

The planner analyzes each task and decides how to split it:

- **Single packet** — When all criteria target the same area of work
- **Multiple packets** — When criteria target independent artifacts (e.g., separate directories)

Each packet is assigned a role based on the objective:

| Role | Trigger signals |
|------|----------------|
| `builder` | build, create, implement, scaffold (default) |
| `researcher` | investigate, research, analyze, report |
| `reviewer` | audit, review, verify, validate |
| `operator` | deploy, provision, configure, migrate |

### Validation

Three-layer validation with fail-safe defaults:

1. **Lane results** — Did all lanes report success?
2. **Filesystem heuristics** — Do expected files exist? Do they contain expected content?
3. **Verify commands** — Shell commands for complex checks (format: `"description :: command"`)

If a criterion can't be verified programmatically, the task fails. Unverifiable means untestable.

### Codex-Queue Integration

`codex-queue` bridges Squirrel work packets to agent CLIs:

```
Work Packet → codex-queue → Agent CLI (claude/codex/gemini) → Result → Lane
```

Execution modes: `local` (immediate), `cloud` (remote), `defer` (write envelope for later).

### Control Interface

External systems interact with Squirrel through the filesystem:

| Operation | How |
|-----------|-----|
| Submit task | Write JSON to `.squirrel/inbox/` |
| Query state | Read from `.squirrel/registry/` |
| Read receipts | Read from `.squirrel/outbox/` |
| Pause pipeline | Write `{"state": "paused"}` to `.squirrel/control/pipeline.json` |
| Resume | Write `{"state": "running"}` to `.squirrel/control/pipeline.json` |
| Cancel task | Write to `.squirrel/control/cancel_{task_id}.json` |
| Retry task | Write to `.squirrel/control/retry_{task_id}.json` |

## Design Principles

1. **Artifacts over chat** — Work is measured in files and diffs, not conversation
2. **One source of truth** — Task registry is canonical
3. **Fail fast** — Reject ambiguity immediately; block rather than guess
4. **No scope creep** — Execute only what's specified
5. **Validation before completion** — No task completes without passing validation
6. **Explicit state transitions** — Every status change follows the state machine
7. **Lanes are disposable** — They execute and return; no persistent state
8. **Receipts are mandatory** — A task without a receipt doesn't exist

## Testing

```bash
pip install -e .
pytest tests/ -v
```

## Project Structure

```
squirrel/
├── runner.py             # Core 6-phase execution loop
├── planner.py            # Task decomposition and role inference
├── lanes.py              # Lane dispatch and handler execution
├── intake.py             # Task ingestion and schema validation
├── validation.py         # Three-layer criteria verification
├── receipts.py           # Receipt generation and summaries
├── state.py              # State machine enforcement
├── events.py             # Event logging and observability
├── lane_codex_queue.py   # Codex-queue agent bridge
└── cli.py                # Full CLI interface
```

## License

MIT

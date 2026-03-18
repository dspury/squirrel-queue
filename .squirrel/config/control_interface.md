# Squirrel Control Interface

Defines the external API surface for vOS (primary) and OpenClaw (future).

## Operations

Any external system interacts with Squirrel through the filesystem.

### Submit Task

Write a valid task JSON file to `.squirrel/inbox/`.
- Filename: `{task_id}.json`
- Must validate against `schemas/task.schema.json`
- Status must be `queued`

### Query State

Read files from `.squirrel/registry/`.
- Each task has a file: `registry/{task_id}.json`
- File reflects current state (updated by Squirrel after transitions)
- External systems: read-only access

### Read Receipts

Read files from `.squirrel/outbox/`.
- Each completed/failed task produces: `outbox/{task_id}_receipt.json`
- Conforms to `schemas/receipt.schema.json`

### Retry Failed Task

Write a control file to `.squirrel/control/`.
- Filename: `retry_{task_id}.json`
- Contents: `{ "action": "retry", "task_id": "sq_YYYY_NNNN" }`
- Squirrel picks this up and re-enqueues the task (if retry count < max)

### Cancel Task

Write a control file to `.squirrel/control/`.
- Filename: `cancel_{task_id}.json`
- Contents: `{ "action": "cancel", "task_id": "sq_YYYY_NNNN" }`
- Squirrel moves task to `failed` with cancellation receipt

### Pause / Resume Pipeline

Write to `.squirrel/control/pipeline.json`:
- `{ "state": "paused" }` — Squirrel stops picking up new tasks from inbox
- `{ "state": "running" }` — Squirrel resumes normal operation

## Access Rules

| System   | inbox/ | registry/ | outbox/ | control/ |
|----------|--------|-----------|---------|----------|
| vOS      | write  | read      | read    | write    |
| OpenClaw | write  | read      | read    | write    |
| Squirrel | read   | read/write| write   | read     |
| Lanes    | —      | —         | —       | —        |

Lanes interact only with Squirrel Commander, never with the filesystem directly.

# OpenClaw Adapter Example

This repository keeps the queue core generic. OpenClaw-specific behavior belongs in an adapter layer.

## What The Adapter Does

For `defer` mode, the queue writes a deferred request envelope like:

- `~/.codex-queue/requests/cq_<id>.json`

An OpenClaw adapter can then:

1. watch or receive the deferred request path
2. emit an OpenClaw system event
3. let the next heartbeat or runtime process dispatch the envelope

## Example Pattern

Run `codex-queue` with a defer command:

```bash
node ./bin/codex-queue.js \
  --payload-file task.json \
  --cwd /path/to/repo \
  --defer-command "./examples/openclaw/defer-to-openclaw.sh"
```

The adapter script can inspect `CODEX_QUEUE_HOME`, discover the newest deferred request, and send a notification into OpenClaw.

## Why Keep It Separate

Keeping OpenClaw outside the core preserves:

- a portable open-source queue contract
- clean support for non-OpenClaw systems
- simpler testing and clearer boundaries

That is the intended architecture:

- `codex-queue`: validation, routing, receipts, deferred-envelope writing
- OpenClaw adapter: heartbeat integration and runtime wake-up behavior

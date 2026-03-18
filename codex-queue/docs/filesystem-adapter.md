# Filesystem Adapter Example

This example shows the smallest possible integration: write a deferred envelope, then append a line to a local log or queue file that another process can watch.

## Use Case

This is useful when you want to integrate `codex-queue` with:

- a cron-driven poller
- a launchd or systemd watcher
- a simple local agent that scans a queue directory
- a custom orchestration service that does not need OpenClaw

## Example Pattern

Run `codex-queue` with:

```bash
node ./bin/codex-queue.js \
  --payload-file task.json \
  --cwd /path/to/repo \
  --defer-command "./examples/filesystem/defer-to-log.sh"
```

The example script appends the newest deferred request path to a log file under the queue state directory.

Another process can then:

1. read the appended request path
2. load the deferred envelope
3. decide when and how to dispatch the task

## Why Include This

This adapter makes the core architecture more obvious:

- `codex-queue` is responsible for the stable dispatch contract
- your runtime is responsible for what happens after deferral

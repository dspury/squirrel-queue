# codex-queue

> A lightweight dispatcher for Codex CLI work across local, cloud, and deferred execution modes.

`codex-queue` accepts one JSON request, validates it, chooses an execution path, records a receipt, and returns one concise JSON result. It stays intentionally narrow: dispatch planning and status semantics live here; repo-specific execution behavior stays in your harness or adapter layer.

## Quick Links

- [Easy Mode](#easy-mode)
- [Manual Setup](#manual-setup)
- [Quick Run](#quick-run)
- [Request Contract](#request-contract)
- [Execution Modes](#execution-modes)
- [Adapters](#adapters)
- [Development](#development)

## At a Glance

| Area | Purpose |
| --- | --- |
| `bin/codex-queue.js` | CLI entry point |
| `index.js` | Validation, dispatch planning, execution, and receipts |
| `test/checks.js` | End-to-end local checks |
| `docs/` | Adapter notes and operator docs |
| `examples/` | Example defer hooks |

## Easy Mode

> Fastest path: install dependencies, run the packaged checks, then copy the smoke-test flow in the setup guide.

1. Install dependencies:

```bash
npm install
```

2. Run the built-in checks:

```bash
npm run check
```

3. Open [`docs/reference/EASY_MODE_SETUP.md`](docs/reference/EASY_MODE_SETUP.md) and run the dry-run smoke test.

4. When that looks right, switch the sample request to a real repo and execution mode.

Manual setup is still documented below if you want to wire it into a larger harness or scheduler more deliberately.

## What It Does

- `local`: runs `codex exec` immediately on your machine
- `cloud`: submits a task with `codex cloud exec --env ...`
- `defer`: writes a deferred request envelope and can optionally notify an external system

## Positioning

`codex-queue` sits above a repo-level Codex harness.

- a harness defines how Codex should operate inside a project
- `codex-queue` defines how outside systems route work into the right project and execution mode

That makes it useful for schedulers, automation wrappers, heartbeat systems, and LLM supervisors that need a stable dispatch contract without embedding project logic into the queue itself.

## Manual Setup

1. Ensure Node.js 18+ is installed.
2. Install dependencies:

```bash
npm install
```

3. Run the local validation suite:

```bash
npm run check
```

4. Inspect CLI help:

```bash
node ./bin/codex-queue.js --help
```

5. If you plan to use `local` or `cloud` execution, make sure the `codex` CLI is already installed and authenticated in the environment where the queue will run.

GitHub Actions CI is included and runs the local check script on pushes and pull requests.

## Quick Run

Create a request file:

```json
{
  "schema": "codex-queue-request@v1",
  "task_type": "repo-triage",
  "repo": "oak-street-site",
  "prompt": "Triage this repo and return a concise fix plan.",
  "execution_mode": "local",
  "return_contract": "summary+artifacts",
  "priority": "normal",
  "origin": "scheduler"
}
```

Dry run first:

```bash
cat task.json | node ./bin/codex-queue.js --cwd /path/to/repo --dry-run
```

Then run it for real:

```bash
cat task.json | node ./bin/codex-queue.js --cwd /path/to/repo
```

Inline payload also works:

```bash
node ./bin/codex-queue.js \
  --payload '{"schema":"codex-queue-request@v1","task_type":"repo-triage","repo":"oak-street-site","prompt":"Triage this repo and return a concise fix plan.","execution_mode":"local","return_contract":"summary+artifacts","priority":"normal","origin":"scheduler"}' \
  --cwd /path/to/repo \
  --dry-run
```

## Request Contract

```json
{
  "schema": "codex-queue-request@v1",
  "task_type": "repo-triage",
  "repo": "oak-street-site",
  "prompt": "Triage this repo and return a concise fix plan.",
  "execution_mode": "local",
  "return_contract": "summary+artifacts",
  "priority": "normal",
  "origin": "scheduler",
  "dedupe_key": "oak-street-site:repo-triage:normal"
}
```

Rules:

- `schema` must exactly match `codex-queue-request@v1`
- `execution_mode` must be `local`, `cloud`, or `defer`
- `priority` must be `low`, `normal`, `high`, or `urgent`
- `prompt` is caller-owned task text
- the wrapper prepends one invariant line before executing Codex

## State Layout

By default, state lives under `~/.codex-queue`.

- `receipts/codex-queue.jsonl`
- `requests/<request-id>.json`

Override with `--state-dir <dir>` or `CODEX_QUEUE_HOME`.

## Execution Modes

- `local`: run `codex exec` immediately and summarize the local result
- `cloud`: submit `codex cloud exec` and return a submission receipt
- `defer`: write a deferred envelope and optionally call an external notifier

This split is deliberate. The queue owns validation, receipts, dispatch planning, and result semantics. Scheduling, retries, wake-ups, and downstream orchestration stay outside the core.

## Adapters

The core intentionally avoids baking in one orchestration system.

If you want to plug it into another runtime:

- use `--defer-command` to trigger an external notifier after a deferred envelope is written
- point `--state-dir` at a location your system watches
- keep repo-specific execution behavior in your harness, not in the queue

Included examples:

- [docs/openclaw-adapter.md](docs/openclaw-adapter.md)
- [examples/openclaw/defer-to-openclaw.sh](examples/openclaw/defer-to-openclaw.sh)
- [docs/filesystem-adapter.md](docs/filesystem-adapter.md)
- [examples/filesystem/defer-to-log.sh](examples/filesystem/defer-to-log.sh)

## Status Semantics

- `dry_run`: routing was selected but nothing executed
- `completed`: a local Codex run finished successfully
- `submitted`: a cloud task or deferred envelope was accepted successfully
- `failed`: command execution failed
- `invalid`: the request or arguments were invalid

## Development

Run the checks:

```bash
npm run check
```

The check script covers:

- valid local request
- valid cloud request
- valid defer request
- malformed and invalid input cases
- dispatch plan generation without command execution
- deferred envelope file writing
- result status semantics
- CLI dry-run behavior

## Notes

- Cloud submission is not treated as remote task completion.
- Deferred submission is not treated as Codex task completion.
- `repo` is logical metadata. `--cwd` remains the execution location.
- The queue is designed to be composed with an LLM supervisor or external automation, not to replace either one.

## Supporting Docs

- [docs/reference/EASY_MODE_SETUP.md](docs/reference/EASY_MODE_SETUP.md)
- [docs/openclaw-adapter.md](docs/openclaw-adapter.md)
- [docs/filesystem-adapter.md](docs/filesystem-adapter.md)

## License

MIT

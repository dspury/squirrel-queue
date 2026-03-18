# Easy Mode Setup

This is the fastest way to prove `codex-queue` is wired correctly before you plug it into a real scheduler or harness.

## 1. Install and validate

```bash
npm install
npm run check
```

## 2. Run a dry-run smoke test

From the repo root:

```bash
node ./bin/codex-queue.js \
  --payload '{"schema":"codex-queue-request@v1","task_type":"repo-triage","repo":"codex-queue","prompt":"Inspect this repo and return a concise plan.","execution_mode":"local","return_contract":"summary+artifacts","priority":"normal","origin":"easy-mode"}' \
  --cwd "$PWD" \
  --dry-run
```

Expected result:

- the command exits successfully
- the JSON result contains `"status":"dry_run"`
- the JSON result contains `"command_label":"codex exec"`

This proves request parsing, validation, dispatch selection, and receipt writing are working locally.

## 3. Switch to a real repo target

Once the smoke test passes, point `--cwd` at the actual repo you want to dispatch against and update the request payload fields:

- `repo`
- `prompt`
- `execution_mode`
- `origin`
- `dedupe_key` if you use one

## 4. Move from dry run to execution

For local execution:

```bash
node ./bin/codex-queue.js \
  --payload-file task.json \
  --cwd /path/to/repo
```

For cloud execution:

```bash
node ./bin/codex-queue.js \
  --payload-file task.json \
  --cwd /path/to/repo \
  --env env_123
```

For deferred execution:

```bash
node ./bin/codex-queue.js \
  --payload-file task.json \
  --cwd /path/to/repo \
  --defer-command "./examples/filesystem/defer-to-log.sh"
```

## 5. Adopt it cleanly

If you are integrating `codex-queue` into a larger system:

- keep repo-specific instructions in the target repo, not in the queue
- start with `--dry-run` in automation until your request shape is stable
- treat `defer` mode as the clean extension point for external orchestrators

'use strict';

const fs = require('fs/promises');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

const REQUEST_SCHEMA = 'codex-queue-request@v1';
const DEFERRED_SCHEMA = 'codex-queue-deferred@v1';
const RECEIPT_SCHEMA = 'codex-queue-receipt@v1';
const RESULT_SCHEMA = 'codex-queue-result@v1';
const DEFAULT_PREFIX_LINE = 'Constraint: make the smallest production-credible change that satisfies the request.';
const SUPPORTED_EXECUTION_MODES = new Set(['local', 'cloud', 'defer']);
const SUPPORTED_PRIORITIES = new Set(['low', 'normal', 'high', 'urgent']);
const SUPPORTED_AGENTS = new Set(['codex', 'claude', 'gemini']);
const DEFAULT_AGENT = 'codex';
const TIMEOUT_EXIT_CODE = 124;
const RESULT_SUMMARY_LIMIT = 180;
const RECEIPT_PREVIEW_LIMIT = 400;
const TMUX_SESSION = 'squirrel-lanes';
const TMUX_POLL_MS = 500;

async function main(argv = process.argv.slice(2), env = process.env) {
  if (argv.includes('--help') || argv.includes('-h')) {
    printHelp();
    process.exit(0);
  }

  const options = parseArgs(argv, env);
  const requestId = createRequestId();
  const stateDir = path.resolve(options.stateDir);
  const receiptPath = path.join(stateDir, 'receipts', 'codex-queue.jsonl');
  const requestsDir = path.join(stateDir, 'requests');
  const cwd = path.resolve(options.cwd || process.cwd());

  let parsedInput = null;
  let dispatchPlan = null;

  try {
    await assertDirectoryExists(cwd, `Working directory not found: ${cwd}`);

    const rawPayload = await readPayload(options);
    parsedInput = parseRequestJson(rawPayload);
    const request = validateRequest(parsedInput);
    dispatchPlan = buildDispatchPlan({
      request,
      cwd,
      cloudEnvId: options.cloudEnvId || env.CODEX_CLOUD_ENV_ID || '',
      requestId,
      tempDir: os.tmpdir(),
      requestsDir,
      deferCommand: options.deferCommand,
      prefixLine: options.prefixLine,
    });

    const runner = options.tmux ? runCommandTmux : runCommand;
    const outcome = options.dryRun
      ? buildDryRunOutcome(dispatchPlan)
      : await executePreparedDispatchPlan(dispatchPlan, request, requestId, runner);

    const receipt = {
      schema: RECEIPT_SCHEMA,
      ts: new Date().toISOString(),
      request_id: requestId,
      cwd,
      payload: request,
      dispatch: buildReceiptDispatch(dispatchPlan),
      outcome,
    };
    await appendReceipt(receiptPath, receipt);

    const result = buildResult({
      requestId,
      request,
      dispatchPlan,
      outcome,
      receiptPath,
    });

    process.stdout.write(`${JSON.stringify(result)}\n`);
    process.exit(exitCodeForStatus(result.status));
  } catch (error) {
    const failure = normalizeError(error);
    const receipt = {
      schema: RECEIPT_SCHEMA,
      ts: new Date().toISOString(),
      request_id: requestId,
      cwd,
      ...(isRecord(parsedInput) ? { payload: parsedInput } : {}),
      ...(dispatchPlan ? { dispatch: buildReceiptDispatch(dispatchPlan) } : {}),
      outcome: {
        ok: false,
        status: failure.status,
        summary: failure.summary,
        error: failure.error,
      },
    };
    await appendReceipt(receiptPath, receipt);

    process.stdout.write(`${JSON.stringify(buildInvalidResult({
      requestId,
      parsedInput,
      failure,
      receiptPath,
    }))}\n`);
    process.exit(failure.exitCode);
  }
}

function printHelp() {
  console.error(`Usage:
  codex-queue [--payload <json> | --payload-file <file> | --stdin] [--cwd <dir>] [--env <env_id>] [--state-dir <dir>] [--defer-command <command>] [--prefix-line <line>] [--dry-run]

Options:
  --payload <json>       Inline JSON request payload
  --payload-file <file>  Read request payload JSON from file
  --stdin                Force request payload read from stdin
  --cwd <dir>            Repo working directory for agent execution
  --env <env_id>         Codex Cloud env id (or set CODEX_CLOUD_ENV_ID)
  --state-dir <dir>      Receipt and deferred-envelope root directory
  --defer-command <cmd>  Shell command run after writing a deferred request
  --prefix-line <line>   Invariant line prepended to the caller-owned prompt
  --dry-run              Validate and choose a command without executing it
  --tmux                 Run agent in a visible tmux pane (session: squirrel-lanes)
  --help                 Show this help

Request fields:
  agent                  Agent CLI to use: codex (default), claude, gemini
  timeout_ms             Kill agent process after this many milliseconds`);
}

function parseArgs(argv, env) {
  const options = {
    payload: null,
    payloadFile: null,
    readStdin: false,
    cwd: null,
    cloudEnvId: null,
    stateDir: env.CODEX_QUEUE_HOME || path.join(os.homedir(), '.codex-queue'),
    deferCommand: env.CODEX_QUEUE_DEFER_COMMAND || '',
    prefixLine: env.CODEX_QUEUE_PREFIX_LINE || DEFAULT_PREFIX_LINE,
    dryRun: false,
    tmux: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--payload') {
      options.payload = requireValue(arg, argv[++i]);
      continue;
    }
    if (arg === '--payload-file') {
      options.payloadFile = requireValue(arg, argv[++i]);
      continue;
    }
    if (arg === '--stdin') {
      options.readStdin = true;
      continue;
    }
    if (arg === '--cwd') {
      options.cwd = requireValue(arg, argv[++i]);
      continue;
    }
    if (arg === '--env') {
      options.cloudEnvId = requireValue(arg, argv[++i]);
      continue;
    }
    if (arg === '--state-dir') {
      options.stateDir = requireValue(arg, argv[++i]);
      continue;
    }
    if (arg === '--defer-command') {
      options.deferCommand = requireValue(arg, argv[++i]);
      continue;
    }
    if (arg === '--prefix-line') {
      options.prefixLine = requireValue(arg, argv[++i]);
      continue;
    }
    if (arg === '--dry-run') {
      options.dryRun = true;
      continue;
    }
    if (arg === '--tmux') {
      options.tmux = true;
      continue;
    }
    throw invalidError(`Unknown argument: ${arg}`);
  }

  if (options.payload === null && options.payloadFile === null && !process.stdin.isTTY) {
    options.readStdin = true;
  }

  const sources = [options.payload !== null, options.payloadFile !== null, options.readStdin].filter(Boolean);
  if (sources.length !== 1) {
    throw invalidError('Choose exactly one payload source: --payload, --payload-file, or stdin.');
  }

  return options;
}

async function readPayload(options) {
  if (options.payload !== null) {
    return options.payload;
  }
  if (options.payloadFile !== null) {
    return fs.readFile(path.resolve(options.payloadFile), 'utf8');
  }
  return readStdin();
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));
  }
  return Buffer.concat(chunks).toString('utf8');
}

function parseRequestJson(rawPayload) {
  try {
    return JSON.parse(rawPayload);
  } catch (_error) {
    throw invalidError('Request payload is not valid JSON.');
  }
}

function validateRequest(input) {
  if (!isRecord(input)) {
    throw invalidError('Request payload must be a JSON object.');
  }

  const schema = requireNonEmptyString(input.schema, 'schema');
  if (schema !== REQUEST_SCHEMA) {
    throw invalidError(`schema must be exactly ${REQUEST_SCHEMA}.`);
  }

  const request = {
    schema,
    task_type: requireNonEmptyString(input.task_type, 'task_type'),
    repo: requireNonEmptyString(input.repo, 'repo'),
    prompt: requireNonEmptyString(input.prompt, 'prompt'),
    execution_mode: requireNonEmptyString(input.execution_mode, 'execution_mode'),
    return_contract: requireNonEmptyString(input.return_contract, 'return_contract'),
    priority: requireNonEmptyString(input.priority, 'priority'),
    origin: requireNonEmptyString(input.origin, 'origin'),
  };

  if (input.dedupe_key !== undefined) {
    request.dedupe_key = requireNonEmptyString(input.dedupe_key, 'dedupe_key');
  }

  if (input.agent !== undefined) {
    request.agent = requireNonEmptyString(input.agent, 'agent');
  } else {
    request.agent = DEFAULT_AGENT;
  }

  if (input.timeout_ms !== undefined) {
    if (typeof input.timeout_ms !== 'number' || !Number.isFinite(input.timeout_ms) || input.timeout_ms <= 0) {
      throw invalidError('timeout_ms must be a positive finite number.');
    }
    request.timeout_ms = Math.floor(input.timeout_ms);
  }

  if (!SUPPORTED_AGENTS.has(request.agent)) {
    throw invalidError(`agent must be one of: ${Array.from(SUPPORTED_AGENTS).join(', ')}`);
  }
  if (!SUPPORTED_EXECUTION_MODES.has(request.execution_mode)) {
    throw invalidError(`execution_mode must be one of: ${Array.from(SUPPORTED_EXECUTION_MODES).join(', ')}`);
  }
  if (!SUPPORTED_PRIORITIES.has(request.priority)) {
    throw invalidError(`priority must be one of: ${Array.from(SUPPORTED_PRIORITIES).join(', ')}`);
  }

  validateLength(request.task_type, 'task_type', 80);
  validateLength(request.repo, 'repo', 120);
  validateLength(request.prompt, 'prompt', 16000);
  validateLength(request.return_contract, 'return_contract', 80);
  validateLength(request.priority, 'priority', 20);
  validateLength(request.origin, 'origin', 120);
  if (request.dedupe_key) {
    validateLength(request.dedupe_key, 'dedupe_key', 200);
  }

  return request;
}

function buildEffectivePrompt(prompt, prefixLine = DEFAULT_PREFIX_LINE) {
  return `${prefixLine.trim()}\n${prompt.trim()}`;
}

function buildAgentLocalPlan(agent, cwd, prompt, outputPath) {
  switch (agent) {
    case 'codex':
      return {
        command: ['codex', 'exec', '-C', cwd, '--skip-git-repo-check', '--full-auto', '-o', outputPath, prompt],
        command_label: 'codex exec',
        output_path: outputPath,
        capture_stdout: false,
        cwd,
        success_summary: 'Completed local Codex run.',
      };
    case 'claude':
      return {
        command: ['claude', '-p', prompt, '--dangerously-skip-permissions'],
        command_label: 'claude',
        output_path: null,
        capture_stdout: true,
        cwd,
        success_summary: 'Completed local Claude run.',
      };
    case 'gemini':
      return {
        command: ['gemini', '-p', prompt],
        command_label: 'gemini',
        output_path: null,
        capture_stdout: true,
        cwd,
        success_summary: 'Completed local Gemini run.',
      };
    default:
      throw invalidError(`Unsupported agent: ${agent}`);
  }
}

function buildDispatchPlan(params) {
  const promptPreview = truncate(params.request.prompt, RECEIPT_PREVIEW_LIMIT);
  const agent = params.request.agent || DEFAULT_AGENT;
  const timeoutMs = params.request.timeout_ms || 0;

  if (params.request.execution_mode === 'defer') {
    const deferredRequestPath = path.join(params.requestsDir, `${params.requestId}.json`);
    const command = params.deferCommand
      ? ['sh', '-lc', params.deferCommand]
      : null;
    return {
      execution_mode: 'defer',
      agent,
      command_label: command ? 'defer command' : 'deferred envelope',
      command,
      prompt_preview: promptPreview,
      success_status: 'submitted',
      success_summary: command
        ? `Deferred ${agent} request and executed external notifier.`
        : `Deferred ${agent} request by writing an envelope file.`,
      timeout_ms: timeoutMs,
      deferred_request_path: deferredRequestPath,
      deferred_context: {
        cwd: params.cwd,
        cloud_env_id: params.cloudEnvId.trim() || null,
      },
    };
  }

  const prompt = buildEffectivePrompt(params.request.prompt, params.prefixLine);

  if (params.request.execution_mode === 'local') {
    const outputPath = path.join(params.tempDir, `codex-queue-${params.requestId}.txt`);
    const agentPlan = buildAgentLocalPlan(agent, params.cwd, prompt, outputPath);
    return {
      execution_mode: 'local',
      agent,
      command_label: agentPlan.command_label,
      command: agentPlan.command,
      prompt_preview: promptPreview,
      success_status: 'completed',
      success_summary: agentPlan.success_summary,
      output_path: agentPlan.output_path,
      capture_stdout: agentPlan.capture_stdout,
      cwd: agentPlan.cwd,
      timeout_ms: timeoutMs,
    };
  }

  if (agent !== 'codex') {
    throw invalidError(`Cloud execution is only supported for the codex agent. Got: ${agent}`);
  }

  const cloudEnvId = params.cloudEnvId.trim();
  if (!cloudEnvId) {
    throw invalidError('Cloud execution requires --env <ENV_ID> or CODEX_CLOUD_ENV_ID.');
  }

  return {
    execution_mode: 'cloud',
    agent,
    command_label: 'codex cloud exec',
    command: [
      'codex',
      '-C',
      params.cwd,
      'cloud',
      'exec',
      '--env',
      cloudEnvId,
      prompt,
    ],
    prompt_preview: promptPreview,
    success_status: 'submitted',
    success_summary: 'Submitted Codex cloud task.',
    cloud_env_id: cloudEnvId,
    timeout_ms: timeoutMs,
  };
}

function buildDryRunOutcome(dispatchPlan) {
  return {
    ok: true,
    status: 'dry_run',
    summary: `Dry run only. Selected ${dispatchPlan.command_label}.`,
  };
}

async function executePreparedDispatchPlan(dispatchPlan, request, requestId, runner = runCommand) {
  if (dispatchPlan.execution_mode === 'defer') {
    await writeDeferredRequestFile(dispatchPlan, request, requestId);
    if (!dispatchPlan.command) {
      return {
        ok: true,
        status: dispatchPlan.success_status,
        summary: dispatchPlan.success_summary,
        exit_code: 0,
        stdout_preview: null,
        stderr_preview: null,
        assistant_preview: null,
      };
    }
  }
  // Pass request metadata for tmux window naming
  const meta = {
    agent: request.agent || dispatchPlan.agent,
    dedupe_key: request.dedupe_key || '',
    task_type: request.task_type || '',
  };
  return executeDispatchPlan(dispatchPlan, runner, meta);
}

async function writeDeferredRequestFile(dispatchPlan, request, requestId) {
  if (!dispatchPlan.deferred_request_path) {
    return null;
  }

  const envelope = {
    schema: DEFERRED_SCHEMA,
    ts: new Date().toISOString(),
    request_id: requestId,
    request,
    dispatch_context: dispatchPlan.deferred_context,
  };

  await fs.mkdir(path.dirname(dispatchPlan.deferred_request_path), { recursive: true });
  await fs.writeFile(dispatchPlan.deferred_request_path, `${JSON.stringify(envelope, null, 2)}\n`, 'utf8');
  return dispatchPlan.deferred_request_path;
}

async function executeDispatchPlan(dispatchPlan, runner = runCommand, meta = {}) {
  const result = await runner(dispatchPlan.command, dispatchPlan.timeout_ms || 0, dispatchPlan.cwd, meta);
  let assistantPreview = null;

  if (dispatchPlan.output_path) {
    assistantPreview = await readOptionalFile(dispatchPlan.output_path);
    await removeOptionalFile(dispatchPlan.output_path);
  } else if (dispatchPlan.capture_stdout && result.stdout) {
    assistantPreview = result.stdout;
  }

  if (result.exitCode !== 0) {
    return {
      ok: false,
      status: 'failed',
      summary: `${dispatchPlan.command_label} failed with exit code ${result.exitCode}.`,
      exit_code: result.exitCode,
      stdout_preview: truncate(result.stdout, RECEIPT_PREVIEW_LIMIT),
      stderr_preview: truncate(result.stderr, RECEIPT_PREVIEW_LIMIT),
      assistant_preview: truncate(assistantPreview, RECEIPT_PREVIEW_LIMIT),
    };
  }

  return {
    ok: true,
    status: dispatchPlan.success_status,
    summary: buildSuccessSummary(dispatchPlan, assistantPreview),
    exit_code: result.exitCode,
    stdout_preview: truncate(result.stdout, RECEIPT_PREVIEW_LIMIT),
    stderr_preview: truncate(result.stderr, RECEIPT_PREVIEW_LIMIT),
    assistant_preview: truncate(assistantPreview, RECEIPT_PREVIEW_LIMIT),
  };
}

function buildSuccessSummary(dispatchPlan, assistantPreview) {
  if (dispatchPlan.success_status === 'submitted') {
    return dispatchPlan.success_summary;
  }
  if (assistantPreview) {
    return truncate(singleLine(assistantPreview), RESULT_SUMMARY_LIMIT);
  }
  return dispatchPlan.success_summary;
}

function buildReceiptDispatch(dispatchPlan) {
  return {
    execution_mode: dispatchPlan.execution_mode,
    command_label: dispatchPlan.command_label,
    command: dispatchPlan.command,
    prompt_preview: dispatchPlan.prompt_preview,
    ...(dispatchPlan.deferred_request_path ? { deferred_request_path: dispatchPlan.deferred_request_path } : {}),
  };
}

function buildResult(params) {
  return {
    schema: RESULT_SCHEMA,
    ok: params.outcome.ok,
    request_id: params.requestId,
    task_type: params.request.task_type,
    repo: params.request.repo,
    execution_mode: params.request.execution_mode,
    status: params.outcome.status,
    command_label: params.dispatchPlan.command_label,
    summary: params.outcome.summary,
    receipt_path: params.receiptPath,
    ...(params.request.dedupe_key ? { dedupe_key: params.request.dedupe_key } : {}),
    ...(params.dispatchPlan.deferred_request_path ? { deferred_request_path: params.dispatchPlan.deferred_request_path } : {}),
  };
}

function buildInvalidResult(params) {
  const source = isRecord(params.parsedInput) ? params.parsedInput : null;
  return {
    schema: RESULT_SCHEMA,
    ok: false,
    request_id: params.requestId,
    ...(typeof source?.task_type === 'string' ? { task_type: source.task_type } : {}),
    ...(typeof source?.repo === 'string' ? { repo: source.repo } : {}),
    ...(typeof source?.execution_mode === 'string' ? { execution_mode: source.execution_mode } : {}),
    status: params.failure.status,
    summary: params.failure.summary,
    receipt_path: params.receiptPath,
    ...(typeof source?.dedupe_key === 'string' ? { dedupe_key: source.dedupe_key } : {}),
  };
}

function exitCodeForStatus(status) {
  if (status === 'dry_run' || status === 'submitted' || status === 'completed') {
    return 0;
  }
  if (status === 'invalid') {
    return 2;
  }
  return 3;
}

function shellQuote(s) {
  if (/^[a-zA-Z0-9._\-/=:@]+$/.test(s)) return s;
  return "'" + s.replace(/'/g, "'\\''") + "'";
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function ensureTmuxSession() {
  return new Promise((resolve) => {
    const check = spawn('tmux', ['has-session', '-t', TMUX_SESSION], {
      stdio: 'ignore',
    });
    check.on('close', (code) => {
      if (code === 0) { resolve(); return; }
      const create = spawn('tmux', ['new-session', '-d', '-s', TMUX_SESSION], {
        stdio: 'ignore',
      });
      create.on('close', () => resolve());
      create.on('error', () => resolve());
    });
    check.on('error', () => resolve());
  });
}

async function runCommandTmux(command, timeoutMs = 0, cwd = undefined, meta = {}) {
  const id = crypto.randomUUID().slice(0, 8);
  const outFile = path.join(os.tmpdir(), `sq-tmux-${id}.out`);
  const exitFile = path.join(os.tmpdir(), `sq-tmux-${id}.exit`);
  const scriptFile = path.join(os.tmpdir(), `sq-tmux-${id}.sh`);

  const escaped = command.map(shellQuote).join(' ');

  // Build a descriptive window name from metadata when available.
  // Format: "agent:lane_01:sq_2026_0001" instead of "lane-<uuid>"
  const windowName = buildTmuxWindowName(meta, id);

  // Wrapper: run command with output visible in pane AND captured to file.
  // pipefail ensures the agent's exit code propagates through tee.
  const script = [
    '#!/bin/bash',
    'set -o pipefail',
    ...(cwd ? [`cd ${shellQuote(cwd)}`] : []),
    `echo "--- Squirrel Lane: ${windowName} ---"`,
    `echo "Started: $(date -u +%H:%M:%S)"`,
    `echo ""`,
    `${escaped} 2>&1 | tee ${shellQuote(outFile)}`,
    `echo $? > ${shellQuote(exitFile)}`,
    `echo ""`,
    `echo "--- Completed: $(date -u +%H:%M:%S) ---"`,
    // Keep the pane open so the operator can scroll back through output.
    // Without this, the pane closes immediately on completion.
    'echo "Press Enter to close this pane."',
    'read -r',
    '',
  ].join('\n');

  await fs.writeFile(scriptFile, script, { mode: 0o755 });
  await ensureTmuxSession();

  // Set remain-on-exit so the pane persists even if the script exits
  // before the operator presses Enter (e.g. on crash).
  await new Promise((resolve, reject) => {
    const child = spawn('tmux', [
      'new-window', '-t', TMUX_SESSION, '-n', windowName, 'bash', scriptFile,
    ], { stdio: 'ignore' });
    child.on('close', (code) => {
      if (code !== 0) reject(new Error(`tmux new-window exited ${code}`));
      else resolve();
    });
    child.on('error', reject);
  });

  // Enable remain-on-exit for this window so output is preserved on crash
  spawn('tmux', [
    'set-option', '-t', `${TMUX_SESSION}:${windowName}`, 'remain-on-exit', 'on',
  ], { stdio: 'ignore' });

  // Poll for completion with elapsed time progress
  const startTime = Date.now();
  let timedOut = false;
  let pollCount = 0;

  while (true) {
    try {
      await fs.access(exitFile);
      break;
    } catch {
      // not done yet
    }

    if (timeoutMs > 0 && Date.now() - startTime > timeoutMs) {
      timedOut = true;
      try {
        const kill = spawn('tmux', [
          'kill-window', '-t', `${TMUX_SESSION}:${windowName}`,
        ], { stdio: 'ignore' });
        await new Promise(r => kill.on('close', r));
      } catch { /* best effort */ }
      break;
    }

    // Progress indicator every 10 polls (~5 seconds)
    pollCount++;
    if (pollCount % 10 === 0) {
      const elapsed = Math.round((Date.now() - startTime) / 1000);
      process.stderr.write(`  [tmux:${windowName}] running... ${elapsed}s elapsed\n`);
    }

    await sleep(TMUX_POLL_MS);
  }

  // Read results
  let stdout = '';
  let exitCode = timedOut ? TIMEOUT_EXIT_CODE : 1;

  try {
    stdout = (await fs.readFile(outFile, 'utf8')).trim();
  } catch { /* no output */ }

  if (!timedOut) {
    try {
      exitCode = parseInt((await fs.readFile(exitFile, 'utf8')).trim(), 10);
      if (Number.isNaN(exitCode)) exitCode = 1;
    } catch { /* default to 1 */ }
  }

  await removeOptionalFile(scriptFile);
  await removeOptionalFile(outFile);
  // Don't remove exitFile here — keep it for debugging if needed.
  // It's in tmpdir and will be cleaned up by the OS.

  return {
    exitCode,
    stdout,
    stderr: timedOut ? `Process killed after ${timeoutMs}ms timeout.` : '',
  };
}

function buildTmuxWindowName(meta, fallbackId) {
  // Build a human-readable window name from dispatch metadata.
  // meta may contain: agent, dedupe_key (format: "task_id:packet_id"), task_type
  const parts = [];

  if (meta.agent) {
    parts.push(meta.agent);
  }

  if (meta.dedupe_key) {
    // dedupe_key format: "sq_2026_0001:wp_2026_0001_01"
    // Extract lane-relevant portion
    const segments = meta.dedupe_key.split(':');
    if (segments.length === 2) {
      // Use packet_id which includes the lane step number
      parts.push(segments[1]);
    } else {
      parts.push(meta.dedupe_key);
    }
  }

  if (parts.length === 0) {
    return `lane-${fallbackId}`;
  }

  // tmux window names can't contain periods or colons
  return parts.join('-').replace(/[.:]/g, '_');
}

async function runCommand(command, timeoutMs = 0, cwd = undefined, _meta = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command[0], command.slice(1), {
      stdio: ['ignore', 'pipe', 'pipe'],
      ...(cwd ? { cwd } : {}),
    });

    let stdout = '';
    let stderr = '';
    let killed = false;
    let timer = null;

    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        killed = true;
        child.kill('SIGTERM');
      }, timeoutMs);
    }

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('error', (error) => reject(error));
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      resolve({
        exitCode: killed ? TIMEOUT_EXIT_CODE : (code ?? 1),
        stdout: stdout.trim(),
        stderr: killed
          ? `Process killed after ${timeoutMs}ms timeout.${stderr ? ' ' + stderr.trim() : ''}`
          : stderr.trim(),
      });
    });
  });
}

async function appendReceipt(receiptPath, receipt) {
  await fs.mkdir(path.dirname(receiptPath), { recursive: true });
  await fs.appendFile(receiptPath, `${JSON.stringify(receipt)}\n`, 'utf8');
}

async function assertDirectoryExists(dirPath, message) {
  try {
    const stat = await fs.stat(dirPath);
    if (!stat.isDirectory()) {
      throw new Error(message);
    }
  } catch (_error) {
    throw invalidError(message);
  }
}

async function readOptionalFile(filePath) {
  try {
    return await fs.readFile(filePath, 'utf8');
  } catch (_error) {
    return null;
  }
}

async function removeOptionalFile(filePath) {
  try {
    await fs.rm(filePath, { force: true });
  } catch (_error) {
    // Best-effort temp cleanup only.
  }
}

function requireValue(flag, value) {
  if (value === undefined) {
    throw invalidError(`Missing value for ${flag}`);
  }
  return value;
}

function requireNonEmptyString(value, field) {
  if (typeof value !== 'string' || !value.trim()) {
    throw invalidError(`${field} must be a non-empty string.`);
  }
  return value.trim();
}

function validateLength(value, field, maxLength) {
  if (value.length > maxLength) {
    throw invalidError(`${field} must be ${maxLength} characters or fewer.`);
  }
}

function truncate(value, maxLength) {
  if (!value) {
    return null;
  }
  const normalized = String(value).trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 3)}...`;
}

function singleLine(value) {
  return String(value).replace(/\s+/g, ' ').trim();
}

function createRequestId() {
  return `cq_${crypto.randomUUID()}`;
}

function invalidError(message) {
  const error = new Error(message);
  error.kind = 'invalid';
  return error;
}

function normalizeError(error) {
  if (error && error.kind === 'invalid') {
    return {
      status: 'invalid',
      summary: error.message,
      error: error.message,
      exitCode: 2,
    };
  }
  return {
    status: 'failed',
    summary: 'Wrapper execution failed.',
    error: error instanceof Error ? error.message : String(error),
    exitCode: 3,
  };
}

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

module.exports = {
  REQUEST_SCHEMA,
  DEFERRED_SCHEMA,
  RECEIPT_SCHEMA,
  RESULT_SCHEMA,
  DEFAULT_PREFIX_LINE,
  SUPPORTED_AGENTS,
  DEFAULT_AGENT,
  TIMEOUT_EXIT_CODE,
  main,
  parseArgs,
  parseRequestJson,
  validateRequest,
  buildEffectivePrompt,
  buildAgentLocalPlan,
  buildDispatchPlan,
  buildDryRunOutcome,
  executePreparedDispatchPlan,
  writeDeferredRequestFile,
  executeDispatchPlan,
  buildReceiptDispatch,
  buildResult,
  buildInvalidResult,
  exitCodeForStatus,
  runCommand,
  runCommandTmux,
  ensureTmuxSession,
  buildTmuxWindowName,
  shellQuote,
  normalizeError,
};

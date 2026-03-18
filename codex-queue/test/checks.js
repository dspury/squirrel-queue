#!/usr/bin/env node
'use strict';

const assert = require('assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const cliPath = path.join(repoRoot, 'bin', 'codex-queue.js');
const queue = require(path.join(repoRoot, 'index.js'));

const baseRequest = {
  schema: queue.REQUEST_SCHEMA,
  task_type: 'repo-triage',
  repo: 'oak-street-site',
  prompt: 'Triage this repo and return a concise fix plan.',
  execution_mode: 'local',
  return_contract: 'summary+artifacts',
  priority: 'normal',
  origin: 'scheduler',
  dedupe_key: 'oak-street-site:repo-triage:normal',
};

async function main() {
  testDispatchPlanGeneration();
  testMultiAgentDispatchPlans();
  testTimeoutValidation();
  await testExecuteDispatchPlanStatuses();
  await testTimeoutExecution();
  await testStdoutCapture();
  testCliDryRunLocal();
  testCliDryRunCloud();
  testCliDefer();
  testCliDryRunClaude();
  testCliDryRunGemini();
  testCliCloudNonCodexAgent();
  testMissingSchema();
  testUnsupportedSchema();
  testMalformedJson();
  testMissingRequiredField();
  testMissingCloudEnv();
  testUnsupportedAgent();
  process.stdout.write('codex-queue checks passed\n');
}

function testDispatchPlanGeneration() {
  const request = queue.validateRequest(baseRequest);
  const requestsDir = path.join(os.tmpdir(), `codex-queue-requests-${Date.now()}`);

  const localPlan = queue.buildDispatchPlan({
    request,
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_local',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  assert.equal(localPlan.command_label, 'codex exec');
  assert.equal(localPlan.execution_mode, 'local');
  assert.equal(localPlan.success_status, 'completed');
  assert.equal(localPlan.command[0], 'codex');

  const cloudPlan = queue.buildDispatchPlan({
    request: { ...request, execution_mode: 'cloud' },
    cwd: repoRoot,
    cloudEnvId: 'env_demo_123',
    requestId: 'cq_test_cloud',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  assert.equal(cloudPlan.command_label, 'codex cloud exec');
  assert.equal(cloudPlan.success_status, 'submitted');
  assert.equal(cloudPlan.command[4], 'exec');

  const deferPlan = queue.buildDispatchPlan({
    request: { ...request, execution_mode: 'defer' },
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_defer',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  assert.equal(deferPlan.command_label, 'deferred envelope');
  assert.equal(deferPlan.success_status, 'submitted');
  assert.equal(deferPlan.command, null);
  assert.match(deferPlan.deferred_request_path, /cq_test_defer\.json$/);
}

function testMultiAgentDispatchPlans() {
  const requestsDir = path.join(os.tmpdir(), `codex-queue-requests-agents-${Date.now()}`);

  // Claude local plan
  const claudeRequest = queue.validateRequest({ ...baseRequest, agent: 'claude' });
  const claudePlan = queue.buildDispatchPlan({
    request: claudeRequest,
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_claude',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  assert.equal(claudePlan.command_label, 'claude');
  assert.equal(claudePlan.agent, 'claude');
  assert.equal(claudePlan.command[0], 'claude');
  assert.equal(claudePlan.capture_stdout, true);
  assert.equal(claudePlan.output_path, null);

  // Gemini local plan
  const geminiRequest = queue.validateRequest({ ...baseRequest, agent: 'gemini' });
  const geminiPlan = queue.buildDispatchPlan({
    request: geminiRequest,
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_gemini',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  assert.equal(geminiPlan.command_label, 'gemini');
  assert.equal(geminiPlan.agent, 'gemini');
  assert.equal(geminiPlan.command[0], 'gemini');
  assert.equal(geminiPlan.capture_stdout, true);
  assert.equal(geminiPlan.output_path, null);

  // Default agent (no agent field) should be codex
  const defaultRequest = queue.validateRequest({ ...baseRequest });
  assert.equal(defaultRequest.agent, 'codex');
  const defaultPlan = queue.buildDispatchPlan({
    request: defaultRequest,
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_default',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  assert.equal(defaultPlan.command[0], 'codex');
  assert.equal(defaultPlan.agent, 'codex');
}

function testTimeoutValidation() {
  // Valid timeout
  const withTimeout = queue.validateRequest({ ...baseRequest, timeout_ms: 30000 });
  assert.equal(withTimeout.timeout_ms, 30000);

  // No timeout (optional)
  const noTimeout = queue.validateRequest({ ...baseRequest });
  assert.equal(noTimeout.timeout_ms, undefined);

  // Invalid timeout values
  assert.throws(() => queue.validateRequest({ ...baseRequest, timeout_ms: -1 }), /positive/);
  assert.throws(() => queue.validateRequest({ ...baseRequest, timeout_ms: 0 }), /positive/);
  assert.throws(() => queue.validateRequest({ ...baseRequest, timeout_ms: 'fast' }), /positive/);
  assert.throws(() => queue.validateRequest({ ...baseRequest, timeout_ms: Infinity }), /positive/);
}

async function testExecuteDispatchPlanStatuses() {
  const request = queue.validateRequest(baseRequest);
  const requestsDir = await fs.mkdtemp(path.join(os.tmpdir(), 'codex-queue-'));

  const localPlan = queue.buildDispatchPlan({
    request,
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_exec',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });

  await fs.writeFile(localPlan.output_path, 'Concise local completion summary.', 'utf8');
  const localOutcome = await queue.executeDispatchPlan(localPlan, async () => ({
    exitCode: 0,
    stdout: '',
    stderr: '',
  }));
  assert.equal(localOutcome.status, 'completed');
  assert.equal(localOutcome.ok, true);
  assert.match(localOutcome.summary, /Concise local completion summary/);

  const cloudPlan = queue.buildDispatchPlan({
    request: { ...request, execution_mode: 'cloud' },
    cwd: repoRoot,
    cloudEnvId: 'env_demo_123',
    requestId: 'cq_test_submit',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  const cloudOutcome = await queue.executeDispatchPlan(cloudPlan, async () => ({
    exitCode: 0,
    stdout: 'task_123',
    stderr: '',
  }));
  assert.equal(cloudOutcome.status, 'submitted');
  assert.equal(cloudOutcome.summary, 'Submitted Codex cloud task.');

  const failedOutcome = await queue.executeDispatchPlan(cloudPlan, async () => ({
    exitCode: 17,
    stdout: '',
    stderr: 'boom',
  }));
  assert.equal(failedOutcome.status, 'failed');
  assert.equal(failedOutcome.ok, false);

  const dryRunOutcome = queue.buildDryRunOutcome(cloudPlan);
  assert.equal(dryRunOutcome.status, 'dry_run');
  assert.equal(dryRunOutcome.ok, true);

  const deferPlan = queue.buildDispatchPlan({
    request: { ...request, execution_mode: 'defer' },
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_defer_exec',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  const deferFile = await queue.writeDeferredRequestFile(
    deferPlan,
    { ...request, execution_mode: 'defer' },
    'cq_test_defer_exec',
  );
  const envelope = JSON.parse(await fs.readFile(deferFile, 'utf8'));
  assert.equal(envelope.schema, queue.DEFERRED_SCHEMA);
  assert.equal(envelope.request.execution_mode, 'defer');
  assert.equal(envelope.dispatch_context.cwd, repoRoot);

  const deferOutcome = await queue.executePreparedDispatchPlan(
    deferPlan,
    { ...request, execution_mode: 'defer' },
    'cq_test_defer_exec',
    async () => ({
      exitCode: 0,
      stdout: '{"queued":true}',
      stderr: '',
    }),
  );
  assert.equal(deferOutcome.status, 'submitted');
  assert.equal(deferOutcome.summary, 'Deferred codex request by writing an envelope file.');
}

async function testTimeoutExecution() {
  const requestsDir = await fs.mkdtemp(path.join(os.tmpdir(), 'codex-queue-timeout-'));
  const request = queue.validateRequest({ ...baseRequest, timeout_ms: 100 });
  const plan = queue.buildDispatchPlan({
    request,
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_timeout',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });
  assert.equal(plan.timeout_ms, 100);

  // Mock a slow command that takes longer than timeout
  const outcome = await queue.executeDispatchPlan(plan, async (_cmd, timeoutMs) => {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        resolve({ exitCode: 0, stdout: 'done', stderr: '' });
      }, 5000);
      // Simulate the timeout killing the process
      if (timeoutMs > 0) {
        setTimeout(() => {
          clearTimeout(timer);
          resolve({
            exitCode: queue.TIMEOUT_EXIT_CODE,
            stdout: '',
            stderr: `Process killed after ${timeoutMs}ms timeout.`,
          });
        }, timeoutMs);
      }
    });
  });
  assert.equal(outcome.ok, false);
  assert.equal(outcome.status, 'failed');
  assert.equal(outcome.exit_code, queue.TIMEOUT_EXIT_CODE);
}

async function testStdoutCapture() {
  const requestsDir = await fs.mkdtemp(path.join(os.tmpdir(), 'codex-queue-stdout-'));
  const request = queue.validateRequest({ ...baseRequest, agent: 'claude' });
  const plan = queue.buildDispatchPlan({
    request,
    cwd: repoRoot,
    cloudEnvId: '',
    requestId: 'cq_test_stdout',
    tempDir: os.tmpdir(),
    requestsDir,
    deferCommand: '',
    prefixLine: queue.DEFAULT_PREFIX_LINE,
  });

  // Claude captures from stdout, not output file
  assert.equal(plan.capture_stdout, true);
  assert.equal(plan.output_path, null);

  const outcome = await queue.executeDispatchPlan(plan, async () => ({
    exitCode: 0,
    stdout: 'Claude response content here.',
    stderr: '',
  }));
  assert.equal(outcome.ok, true);
  assert.equal(outcome.status, 'completed');
  assert.match(outcome.assistant_preview, /Claude response content/);
  assert.match(outcome.summary, /Claude response content/);
}

function testCliDryRunLocal() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-local`);
  const response = runCli([
    '--payload',
    JSON.stringify(baseRequest),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 0);
  assert.equal(response.json.status, 'dry_run');
  assert.equal(response.json.command_label, 'codex exec');
  assert.equal(response.json.dedupe_key, baseRequest.dedupe_key);
}

function testCliDryRunCloud() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-cloud`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, execution_mode: 'cloud' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--env',
    'env_demo_123',
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 0);
  assert.equal(response.json.status, 'dry_run');
  assert.equal(response.json.command_label, 'codex cloud exec');
}

function testCliDefer() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-defer`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, execution_mode: 'defer' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 0);
  assert.equal(response.json.status, 'dry_run');
  assert.equal(response.json.command_label, 'deferred envelope');
  assert.match(response.json.deferred_request_path, /requests\/cq_/);
}

function testCliDryRunClaude() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-claude`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, agent: 'claude' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 0);
  assert.equal(response.json.status, 'dry_run');
  assert.equal(response.json.command_label, 'claude');
}

function testCliDryRunGemini() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-gemini`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, agent: 'gemini' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 0);
  assert.equal(response.json.status, 'dry_run');
  assert.equal(response.json.command_label, 'gemini');
}

function testCliCloudNonCodexAgent() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-cloud-claude`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, execution_mode: 'cloud', agent: 'claude' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--env',
    'env_demo_123',
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 2);
  assert.equal(response.json.status, 'invalid');
}

function testUnsupportedAgent() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-bad-agent`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, agent: 'gpt' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 2);
  assert.equal(response.json.status, 'invalid');
}

function testMissingSchema() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-missing-schema`);
  const request = { ...baseRequest };
  delete request.schema;
  const response = runCli([
    '--payload',
    JSON.stringify(request),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 2);
  assert.equal(response.json.status, 'invalid');
}

function testUnsupportedSchema() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-schema`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, schema: 'codex-queue-request@v999' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 2);
  assert.equal(response.json.status, 'invalid');
}

function testMalformedJson() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-json`);
  const response = runCli([
    '--payload',
    '{"schema":',
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 2);
  assert.equal(response.json.status, 'invalid');
}

function testMissingRequiredField() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-field`);
  const request = { ...baseRequest };
  delete request.prompt;
  const response = runCli([
    '--payload',
    JSON.stringify(request),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 2);
  assert.equal(response.json.status, 'invalid');
}

function testMissingCloudEnv() {
  const stateDir = path.join(os.tmpdir(), `codex-queue-state-${Date.now()}-env`);
  const response = runCli([
    '--payload',
    JSON.stringify({ ...baseRequest, execution_mode: 'cloud' }),
    '--cwd',
    repoRoot,
    '--state-dir',
    stateDir,
    '--dry-run',
  ]);
  assert.equal(response.exitCode, 2);
  assert.equal(response.json.status, 'invalid');
}

function runCli(args) {
  const result = spawnSync(process.execPath, [cliPath, ...args], {
    cwd: repoRoot,
    encoding: 'utf8',
  });
  assert.ok(result.stdout, `Expected stdout JSON for args: ${args.join(' ')}`);
  return {
    exitCode: result.status ?? 1,
    json: JSON.parse(result.stdout.trim()),
  };
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exit(1);
});

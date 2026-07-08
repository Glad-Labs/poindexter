'use strict';

// Task-Trace API methods (PX.api.traceActive / traceSummary / traceDetail).
// Mock mode must return the honest board/summary/deep-dive shapes, and honest-
// EMPTY (never fabricated) under sim=empty. The live branch lets http() throw on
// transport failure (like findings()/traces()) so usePolledResource can mark the
// board STALE — a blank board must be distinguishable from an unreachable worker
// — so it isn't exercised here; the mock branch is the contract this file pins.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'api.js'), 'utf8');

function makeLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  };
}

// Load api.js in MOCK mode (PX_API_LIVE unset → cfg.live=false) with window.PX
// pre-seeded the way data.js seeds it at load. Minimal but shape-faithful stubs.
function loadApi() {
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController,
    performance,
    fetch: () => Promise.reject(new Error('ECONNREFUSED')),
    // Seeds data.js would have attached to window.PX before api.js runs.
    PX: {
      traceActive: {
        runs: [
          {
            task_id: 'task-4412',
            topic: 'demo',
            template_slug: 'canonical_blog',
            status: 'in_progress',
            stage: 'qa.critic',
            percentage: 68,
            nodes_done: 21,
          },
        ],
        recent: [
          {
            task_id: 'task-4405',
            topic: 'demo2',
            template_slug: 'canonical_blog',
            status: 'rejected',
          },
        ],
      },
      traceSummary: {
        window_hours: 24,
        tasks: 37,
        by_status: { published: 8 },
        pass_rate: 0.706,
        avg_quality: 82.4,
        avg_cost_usd: 0.1874,
      },
      traceDetail: {
        task_id: 'task-4405',
        run_id: 'task-4405',
        runs: [
          {
            run_id: 'task-4405',
            template_slug: 'canonical_blog',
            kind: 'content',
            status: 'halted',
            node_count: 28,
            halted_at: 28,
          },
        ],
        task: { task_id: 'task-4405', status: 'rejected' },
        nodes: [
          {
            seq: 1,
            atom: 'stage.verify_task',
            node_id: 'verify_task',
            status: 'ok',
            tier: 'free',
            model: 'rule-based',
            latency_ms: 40,
            cost: 0,
            output_keys: ['task_verified'],
            output_preview: '{"ok":true}',
          },
        ],
        corpus: '- source → https://example.com',
        decisions: [],
        qa: [
          {
            seq: 28,
            atom: 'qa.aggregate',
            status: 'halted',
            preview: '{"approved":false}',
          },
        ],
        cost_rollup: {
          by_model: [
            {
              model: 'claude-sonnet-5',
              provider: 'anthropic',
              usd: 0.17,
              tokens: 12300,
              calls: 1,
            },
          ],
          total_usd: 0.17,
        },
        final: { title: 'demo', quality_score: 58, content_length: 6820 },
        halt: { seq: 28, atom: 'qa.aggregate', reason: '{"approved":false}' },
        langfuse: { session_id: 'task-4405', run_id: 'task-4405' },
      },
    },
  };
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage();
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return sandbox.PX.api;
}

test('traceActive returns the board shape {runs, recent} in mock mode', async () => {
  const api = loadApi();
  const r = await api.traceActive();
  assert.ok(Array.isArray(r.runs) && r.runs.length >= 1);
  assert.ok(Array.isArray(r.recent) && r.recent.length >= 1);
});

test('traceActive is honest-empty under sim=empty (blank ≠ unreachable is the freshness job, not a fake row)', async () => {
  const api = loadApi();
  api.setSim('empty');
  const r = await api.traceActive();
  assert.ok(Array.isArray(r.runs) && r.runs.length === 0);
  assert.ok(Array.isArray(r.recent) && r.recent.length === 0);
});

test('traceSummary returns the 24h KPI keys in mock mode', async () => {
  const api = loadApi();
  const r = await api.traceSummary();
  assert.ok(
    'tasks' in r &&
      'pass_rate' in r &&
      'avg_quality' in r &&
      'avg_cost_usd' in r
  );
  assert.equal(r.window_hours, 24);
});

test('traceSummary is honest-empty under sim=empty', async () => {
  const api = loadApi();
  api.setSim('empty');
  const r = await api.traceSummary();
  assert.equal(r.tasks, 0);
  assert.equal(r.pass_rate, null);
});

test('traceDetail returns the deep-dive shape in mock mode', async () => {
  const api = loadApi();
  const r = await api.traceDetail('task-4405');
  assert.ok(Array.isArray(r.nodes) && r.nodes.length >= 1);
  assert.ok(Array.isArray(r.runs));
  assert.ok(r.cost_rollup && Array.isArray(r.cost_rollup.by_model));
  assert.ok(r.langfuse && typeof r.langfuse.session_id === 'string');
});

test('traceDetail is honest-empty under sim=empty (never a fabricated spine)', async () => {
  const api = loadApi();
  api.setSim('empty');
  const r = await api.traceDetail('task-x');
  assert.ok(Array.isArray(r.nodes) && r.nodes.length === 0);
  assert.equal(r.task, null);
  assert.equal(r.cost_rollup.total_usd, 0);
});

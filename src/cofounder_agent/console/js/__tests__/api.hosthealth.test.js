'use strict';

// Host-process liveness in serviceHealth().
//
// A host process (ollama at :11434) has no container and so no cAdvisor
// series. serviceHealth() used to return a flat `off` / 'host · not scraped'
// for those rows — correct in that it fabricated nothing, but it meant the
// runtime every LLM call in the pipeline goes through rendered permanently
// dark on the Services page and the System Map, whether it was serving or
// stopped. The brain daemon has probed it on every 5-minute cycle all along;
// GET /api/services/host-health is the read side that was missing.
//
// The rule these tests exist to hold: `stale` and `unknown` must stay NEUTRAL,
// never green. The probe row is ON CONFLICT DO UPDATE, so it outlives its
// writer — rendering its last value as current would turn a stopped brain
// daemon into a permanently healthy Ollama, which is worse than the dark node
// it replaced.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadApiWithRecorder } = require('./contracts/contract-runtime.js');

const OPTS = { preload: ['data.js'] };

function responder(hostServices, opts = {}) {
  return ({ url }) => {
    if (url.includes('/api/services/host-health')) {
      if (opts.hostHealthFails) return { status: 500 };
      return { payload: { services: hostServices, staleness_seconds: 900 } };
    }
    if (url.includes('/api/health')) return { payload: { ok: true } };
    // No cAdvisor data — container rows aren't what these tests are about.
    return { payload: { data: { result: [] } } };
  };
}

async function ollamaRow(hostServices, opts) {
  const { api } = loadApiWithRecorder(responder(hostServices, opts), {}, OPTS);
  const rows = await api.serviceHealth();
  return rows.find((r) => r.name === 'ollama');
}

test('a live host probe lights the row up with its detail', async () => {
  const row = await ollamaRow({
    ollama: {
      status: 'ok',
      detail: '13 models',
      probe: 'ollama_models',
      age_seconds: 41,
    },
  });
  assert.equal(row.status, 'ok');
  assert.match(row.metric, /13 models/);
  assert.match(row.metric, /41s ago/, 'the reading carries its own age');
});

test('a failing host probe reports down', async () => {
  const row = await ollamaRow({
    ollama: {
      status: 'err',
      detail: 'Ollama unreachable: connection refused',
      probe: 'ollama_models',
      age_seconds: 12,
    },
  });
  assert.equal(row.status, 'err');
  assert.match(row.metric, /unreachable/);
});

test('a STALE probe is neutral, never healthy', async () => {
  // The load-bearing case: the brain daemon stopped, the row froze at ok.
  const row = await ollamaRow({
    ollama: {
      status: 'stale',
      detail: '13 models',
      probe: 'ollama_models',
      age_seconds: 4200,
    },
  });
  assert.notEqual(row.status, 'ok', 'a frozen row must not read as up');
  assert.equal(row.status, 'off');
  assert.match(row.metric, /stale/);
  assert.match(
    row.metric,
    /70m ago/,
    'says how long ago, so staleness is legible rather than just labelled'
  );
});

test('a never-probed host says so rather than going green or red', async () => {
  const row = await ollamaRow({
    ollama: {
      status: 'unknown',
      detail: '',
      probe: 'ollama_models',
      age_seconds: null,
    },
  });
  assert.equal(row.status, 'off');
  assert.match(row.metric, /never probed/);
});

test('an unreachable host-health endpoint falls back, it does not fabricate', async () => {
  const row = await ollamaRow({}, { hostHealthFails: true });
  assert.equal(row.status, 'off');
  assert.equal(row.metric, 'host · not scraped');
});

test('a host row the endpoint does not cover keeps the honest fallback', async () => {
  const row = await ollamaRow({ 'some-other-host-thing': { status: 'ok' } });
  assert.equal(row.status, 'off');
  assert.equal(row.metric, 'host · not scraped');
});

test('host rows are still never given a cAdvisor-derived status', async () => {
  // Regression guard: the host branch must return before the container path,
  // or a host row would read `down` off a missing container series.
  const row = await ollamaRow({
    ollama: {
      status: 'ok',
      detail: '2 models',
      probe: 'ollama_models',
      age_seconds: 5,
    },
  });
  assert.equal(row.status, 'ok');
  assert.ok(!/down/.test(row.metric));
});

test('container rows are untouched by the host-health overlay', async () => {
  const { api } = loadApiWithRecorder(
    responder({ ollama: { status: 'ok', detail: 'x', age_seconds: 1 } }),
    {},
    OPTS
  );
  const rows = await api.serviceHealth();
  const worker = rows.find((r) => r.container === 'poindexter-worker');
  // No cAdvisor series in this responder, so the container path still says down.
  assert.equal(worker.status, 'err');
  assert.equal(worker.metric, 'down');
});

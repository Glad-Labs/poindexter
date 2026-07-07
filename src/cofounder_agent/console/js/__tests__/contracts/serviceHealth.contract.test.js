'use strict';
// Bespoke contract tests for the two composite PX.api surfaces. serviceHealth
// and gpu fan out to many cAdvisor / nvidia_gpu Prometheus queries and merge
// over display scaffolding, so their contract is the SET of outgoing queries
// (a typo'd metric name silently breaks the live panel) rather than a single
// request row. Prometheus is replayed honest-empty ([] data), which is a real
// prod state (exporter down) and must not throw.
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadApiWithRecorder,
  requestMatches,
} = require('./contract-runtime.js');

// Replay every Prometheus instant-query as an empty result set, /api/health OK.
const emptyProm = () => ({ status: 200, payload: { data: { result: [] } } });

function promQueries(calls) {
  return calls
    .filter((c) => c.url.includes('/api/v1/query'))
    .map((c) => new URL(c.url).searchParams.get('query'));
}

test('serviceHealth issues the 4 cAdvisor queries + /api/health, no throw on empty', async () => {
  const { api, calls } = loadApiWithRecorder(
    emptyProm,
    {},
    { preload: ['data.js'] }
  );
  const out = await api.serviceHealth();
  const q = promQueries(calls);
  const sel = '{name=~"poindexter.+"}';
  assert.ok(
    q.includes('time() - container_last_seen' + sel),
    'container_last_seen age query'
  );
  assert.ok(
    q.includes('rate(container_cpu_usage_seconds_total' + sel + '[1m]) * 100'),
    'cpu rate query'
  );
  assert.ok(q.includes('container_memory_usage_bytes' + sel), 'memory query');
  assert.ok(
    q.includes('time() - container_start_time_seconds' + sel),
    'uptime query'
  );
  assert.ok(
    calls.some((c) =>
      requestMatches(c, { method: 'GET', path: '/api/health' })
    ),
    'worker /api/health overlay'
  );
  // Merge over mock().services must not throw; host rows render neutral.
  assert.ok(Array.isArray(out), 'serviceHealth returns an array of rows');
});

test('gpu issues the 8 nvidia_gpu_* scalar queries, no throw on empty', async () => {
  const { api, calls } = loadApiWithRecorder(
    emptyProm,
    {},
    { preload: ['data.js'] }
  );
  const out = await api.gpu();
  const q = promQueries(calls);
  [
    'nvidia_gpu_utilization_percent',
    'nvidia_gpu_temperature_celsius',
    'nvidia_gpu_power_draw_watts',
    'nvidia_gpu_power_limit_watts',
    'nvidia_gpu_memory_used_mib',
    'nvidia_gpu_memory_total_mib',
    'nvidia_gpu_fan_speed_percent',
    'nvidia_gpu_clock_graphics_mhz',
  ].forEach((metric) => assert.ok(q.includes(metric), `gpu queries ${metric}`));
  assert.equal(
    typeof out.util,
    'number',
    'gpu util falls back to a number on empty'
  );
  assert.ok(
    Array.isArray(out.procs),
    'gpu procs is an array (empty, not fabricated)'
  );
});

'use strict';

// serviceHealth() must report what is RUNNING, not only what data.js declares.
//
// The live branch used to end in `mock().services.map(...)`, so the curated
// roster was also the ceiling: the cAdvisor vector was queried in full
// (`{name=~"poindexter.+"}`) and every container the roster didn't list was
// read and thrown away. Measured on 2026-09-19, five real containers were up
// and unseeable on the Services page — comfyui, rife, stable-audio,
// nut-exporter, grafana-renderer — and unrestartable with them, since the
// restart action is driven off the same rows. Nothing surfaced the gap; the
// page looked complete because it listed everything it had been told about.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadApiWithRecorder } = require('./contracts/contract-runtime.js');

const OPTS = { preload: ['data.js'] };

function series(name, value, image) {
  return {
    metric: { __name__: 'x', name, image: image || 'img/' + name },
    value: [0, String(value)],
  };
}

// cAdvisor answers with the roster's worker plus containers the roster has
// never heard of. `age` is the discovery source (container_last_seen).
function promResponder(extra = []) {
  return ({ url }) => {
    const q = new URL(url, 'http://local').searchParams.get('query') || '';
    const names = ['poindexter-worker', ...extra];
    if (q.startsWith('time() - container_last_seen')) {
      return {
        payload: {
          status: 'success',
          data: { result: names.map((n) => series(n, 5)) },
        },
      };
    }
    if (q.startsWith('time() - container_start_time_seconds')) {
      return {
        payload: {
          status: 'success',
          data: { result: names.map((n) => series(n, 90000)) },
        },
      };
    }
    if (
      q.startsWith('rate(container_cpu') ||
      q.startsWith('container_memory')
    ) {
      return {
        payload: {
          status: 'success',
          data: { result: names.map((n) => series(n, 7)) },
        },
      };
    }
    return { payload: { data: { result: [] } } };
  };
}

const UNROSTERED = ['poindexter-comfyui-spike', 'poindexter-brand-new-sidecar'];

test('a running container absent from the roster still surfaces', async () => {
  const { api } = loadApiWithRecorder(promResponder(UNROSTERED), {}, OPTS);
  const rows = await api.serviceHealth();
  const byContainer = Object.fromEntries(rows.map((r) => [r.container, r]));

  for (const c of UNROSTERED) {
    const row = byContainer[c];
    assert.ok(row, `${c} is running but was dropped from serviceHealth()`);
    assert.equal(row.status, 'ok', 'a fresh cAdvisor sample means it is up');
    assert.equal(row.discovered, true, 'flagged as found rather than declared');
    assert.equal(
      row.name,
      c.replace(/^poindexter-/, ''),
      'display name drops the prefix'
    );
    assert.ok(
      row.container,
      'container name is carried so restart can target it'
    );
  }
});

test('discovery never displaces or reorders the curated roster', async () => {
  const { api } = loadApiWithRecorder(promResponder(UNROSTERED), {}, OPTS);
  const rows = await api.serviceHealth();
  const rostered = rows.filter((r) => !r.discovered);
  const firstDiscovered = rows.findIndex((r) => r.discovered);

  assert.ok(rostered.length > 20, 'the roster is still fully present');
  assert.equal(
    firstDiscovered,
    rostered.length,
    'discovered rows are appended after the roster, not interleaved'
  );
  // The curated metadata is what the roster is FOR — discovery must not
  // overwrite a declared sub/port with a synthesized one.
  const worker = rows.find((r) => r.container === 'poindexter-worker');
  assert.equal(worker.sub, 'FastAPI API');
  assert.equal(worker.port, 8002);
  assert.ok(!worker.discovered);
});

test('with nothing discovered the result is exactly the roster', async () => {
  const { api } = loadApiWithRecorder(promResponder(), {}, OPTS);
  const rows = await api.serviceHealth();
  assert.equal(rows.filter((r) => r.discovered).length, 0);
});

test('a Prometheus outage degrades to the roster, it does not empty the page', async () => {
  // Every promVector call is caught → {} upstream, so discovery finds nothing.
  // The roster must still render (as down), never vanish.
  const { api } = loadApiWithRecorder(() => ({ status: 500 }), {}, OPTS);
  const rows = await api.serviceHealth();
  assert.ok(
    rows.length > 20,
    'roster still renders when Prometheus is unreachable'
  );
  assert.equal(rows.filter((r) => r.discovered).length, 0);
});

test('a rostered container with no cAdvisor series still reads down', async () => {
  // The union must not soften the existing signal: a declared service that
  // is not reporting is the thing the page exists to show.
  const { api } = loadApiWithRecorder(promResponder(UNROSTERED), {}, OPTS);
  const rows = await api.serviceHealth();
  const loki = rows.find((r) => r.container === 'poindexter-loki');
  assert.equal(loki.status, 'err');
  assert.equal(loki.metric, 'down');
});

test('host processes are still shown neutral, never faked or discovered', async () => {
  const { api } = loadApiWithRecorder(promResponder(UNROSTERED), {}, OPTS);
  const rows = await api.serviceHealth();
  const ollama = rows.find((r) => r.name === 'ollama');
  assert.equal(ollama.status, 'off');
  assert.equal(ollama.metric, 'host · not scraped');
});

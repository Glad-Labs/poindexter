'use strict';

// System-map topology + per-card GPU contract (window.PXMap).
//
// Three defects this pins, all of them live on the Map page until 2026-09-19
// and all of them the same species: a STATIC claim standing in for a LIVE fact.
//
//   1. MAP_EDGES carried literal 'err' and 'amber' kinds, so the link out of
//      prefect-server rendered permanently red and the one out of
//      image-gen-server permanently amber — on healthy infrastructure. Edge
//      colour must be DERIVED from endpoint status.
//   2. The GPU was a single node with the model name 'RTX 5090' hardcoded,
//      reading only the lowest-indexed card's scalars. api.gpu() has returned a
//      per-card `gpus` array since poindexter#921 and deliberately returns
//      name:'' ("model isn't exported — don't fabricate"), so the second card
//      was invisible and the label was an invention.
//   3. A card was flagged `warn` at util > 90 — i.e. whenever it was busy
//      doing the work it exists for.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// map-helpers.js is a browser classic script (window.PXMap), same as
// image-helpers/qa-helpers — load it the way those tests do.
function loadMapHelpers() {
  const sandbox = { console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '..', 'map-helpers.js'), 'utf8'),
    sandbox
  );
  return sandbox.PXMap;
}

const M = loadMapHelpers();

test('no edge declares a health colour — colour is derived, not authored', () => {
  // .join/.length, not deepEqual: these arrays are minted inside the vm realm
  // and deepStrictEqual's cross-realm prototype check rejects equal contents
  // (same caveat as api.gpu.multi.test.js).
  const declared = M.MAP_EDGES.filter(
    ([, , kind]) => kind === 'err' || kind === 'amber'
  ).map(([a, b, kind]) => `${a}>${b}:${kind}`);
  assert.equal(
    declared.join(','),
    '',
    'MAP_EDGES must not hardcode err/amber: that paints a permanent alarm on ' +
      'infrastructure whose real status is already known at render time'
  );
});

test('flowKind derives edge colour from endpoint status', () => {
  assert.equal(M.flowKind('ok', 'ok'), '');
  assert.equal(M.flowKind('ok', 'warn'), 'amber');
  assert.equal(M.flowKind('warn', 'ok'), 'amber');
  assert.equal(M.flowKind('ok', 'err'), 'err');
  assert.equal(M.flowKind('err', 'warn'), 'err', 'down outranks degraded');
  // An endpoint the roster doesn't know contributes nothing rather than a guess.
  assert.equal(M.flowKind('ok', null), '');
  assert.equal(M.flowKind(null, null), '');
});

test('poolFlowKind describes the GPU pool, not one card', () => {
  assert.equal(M.poolFlowKind(['ok', 'ok']), '');
  assert.equal(M.poolFlowKind(['ok', 'warn']), 'amber');
  assert.equal(
    M.poolFlowKind(['ok', 'off']),
    'err',
    'a dark card is a down pool'
  );
  assert.equal(M.poolFlowKind([]), '');
});

test('gpuCardNodes returns one node per card, labelled by index not model', () => {
  const nodes = M.gpuCardNodes({ gpus: [{ index: 0 }, { index: 1 }] });
  assert.equal(nodes.length, 2, 'both cards get a node');
  assert.equal(nodes.map((n) => n.key).join(','), 'gpu-0,gpu-1');
  // Cards share the column and are vertically distinct.
  assert.equal(nodes[0].x, nodes[1].x);
  assert.notEqual(nodes[0].y, nodes[1].y);
  // Centred on the cluster anchor, so the fan-in edges stay short.
  const mid = (nodes[0].y + nodes[1].y) / 2;
  assert.equal(mid, M.GPU_ANCHOR.y);
});

test('gpuCardNodes falls back to a single card when gpus is absent', () => {
  // The panels.jsx `gpu.gpus && gpu.gpus.length ? gpu.gpus : [gpu]` fallback:
  // a one-card install must still render.
  const nodes = M.gpuCardNodes({ util: 40, temp: 50 });
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].y, M.GPU_ANCHOR.y, 'the lone card sits on the anchor');
});

test('a card is named by its index and never by a model string', () => {
  const svc = M.gpuCardService({ util: 12, temp: 44, power: 90 }, 1);
  assert.equal(svc.name, 'GPU 1');
  assert.ok(
    !/RTX|GeForce|\d{4}/.test(svc.name),
    'nvidia_gpu_* exports no model name — naming one here fabricates it'
  );
});

test('a busy card is healthy; a HOT card is the one that warns', () => {
  // The old rule was util > 90 → warn, which amber-flagged every render.
  assert.equal(M.gpuCardService({ util: 100, temp: 60 }, 0).status, 'ok');
  assert.equal(M.gpuCardService({ util: 3, temp: 40 }, 0).status, 'ok');
  assert.equal(
    M.gpuCardService({ util: 3, temp: M.GPU_TEMP_WARN_C }, 0).status,
    'warn',
    'matches threshold.gpu_temperature_celsius / GpuTemperatureHigh'
  );
});

test('a card with no telemetry reads off, never healthy', () => {
  const svc = M.gpuCardService({}, 0);
  assert.equal(svc.status, 'off');
  assert.equal(svc.metric, 'no reading');
});

test('card metric reports only the fields actually present', () => {
  const svc = M.gpuCardService(
    { util: 50, temp: 61, vramUsed: 20.2, vramTotal: 24 },
    1
  );
  assert.equal(
    svc.metric,
    '50% · 61°C · 20.2/24 GB',
    'absent power is omitted, not zeroed'
  );
});

test('the map covers the media/render tier and the real pipeline runner', () => {
  const keys = M.MAP_NODES.map((n) => n.key);
  // prefect-worker is where a content task actually executes; the map used to
  // draw prefect-server straight at the FastAPI worker instead.
  assert.ok(
    keys.includes('prefect-worker'),
    'the pipeline runner must be on the map'
  );
  for (const svc of [
    'comfyui',
    'wan-server',
    'chatterbox',
    'speaches',
    'rife',
    'stable-audio',
  ]) {
    assert.ok(
      keys.includes(svc),
      `${svc} (media/render tier) missing from the map`
    );
  }
});

test('every edge endpoint is a real node, and postgres is drawn as the bus', () => {
  const keys = new Set(M.MAP_NODES.map((n) => n.key));
  for (const [a, b] of M.MAP_EDGES) {
    assert.ok(keys.has(a), `edge references unknown node ${a}`);
    assert.ok(keys.has(b), `edge references unknown node ${b}`);
  }
  // "PostgreSQL as spinal cord": the brain's bus is the DB. The old map drew
  // brain-daemon → worker and gave it no edge to postgres at all.
  const pg = M.MAP_EDGES.filter(
    ([a, b]) => a === 'postgres-local' || b === 'postgres-local'
  );
  assert.ok(
    pg.some(([a, b]) => a === 'brain-daemon' || b === 'brain-daemon'),
    'brain-daemon must be drawn talking to postgres'
  );
  assert.ok(
    pg.length >= 3,
    'postgres is the bus — most core components reach it'
  );
});

test('GPU consumers are all real nodes and none is wired to a specific card', () => {
  const keys = new Set(M.MAP_NODES.map((n) => n.key));
  for (const c of M.GPU_CONSUMERS) {
    assert.ok(keys.has(c), `GPU consumer ${c} is not a node`);
  }
  // Which card a consumer lands on is a scheduling fact this surface doesn't
  // have; a consumer→card edge would assert a pinning we'd be inventing.
  for (const [a, b] of M.MAP_EDGES) {
    assert.ok(
      !a.startsWith('gpu-') && !b.startsWith('gpu-'),
      'no per-card edges in the table'
    );
  }
});

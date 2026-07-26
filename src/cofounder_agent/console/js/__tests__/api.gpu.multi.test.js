'use strict';

// Multi-GPU contract for api.gpu() (poindexter#921).
//
// The GPU tile used to build its gauges from 8 `promScalar` calls, each taking
// `result[0]`. That was unambiguous only while the exporter published a single
// card. Once poindexter#919 fixed the exporter to publish every GPU, `result[0]`
// became "whichever series Prometheus happened to return first" — an ordering
// the HTTP API does not guarantee. Two distinct defects followed:
//
//   1. the second card vanished from the console entirely, and
//   2. `memory_used` and `memory_total` are INDEPENDENT queries, so a differing
//      order between them could pair one card's usage with another's capacity
//      and render a VRAM percentage describing no real card.
//
// These tests pin both. The mixing case deliberately serves the two memory
// families in OPPOSITE orders — the exact shape that produced a nonsense gauge.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadApiWithRecorder } = require('./contracts/contract-runtime.js');

// gpu() merges Prometheus over the data.js mock scaffolding.
const OPTS = { preload: ['data.js'] };

const GPU0 = {
  util: 16,
  temp: 38,
  power: 96,
  powerMax: 600,
  used: 31177,
  total: 32607,
  fan: 0,
  clock: 2790,
};
const GPU1 = {
  util: 6,
  temp: 32,
  power: 127,
  powerMax: 390,
  used: 20733,
  total: 24576,
  fan: 30,
  clock: 1695,
};

function series(gpu, value) {
  return {
    metric: { __name__: 'x', gpu: String(gpu) },
    value: [0, String(value)],
  };
}

// Build a responder mapping each nvidia_gpu_* family to its vector. `flip` names
// families whose series order is reversed, to prove ordering can't leak in.
function promResponder(flip = []) {
  const fam = {
    nvidia_gpu_utilization_percent: ['util'],
    nvidia_gpu_temperature_celsius: ['temp'],
    nvidia_gpu_power_draw_watts: ['power'],
    nvidia_gpu_power_limit_watts: ['powerMax'],
    nvidia_gpu_memory_used_mib: ['used'],
    nvidia_gpu_memory_total_mib: ['total'],
    nvidia_gpu_fan_speed_percent: ['fan'],
    nvidia_gpu_clock_graphics_mhz: ['clock'],
  };
  return ({ url }) => {
    const q = new URL(url, 'http://local').searchParams.get('query') || '';
    const key = Object.keys(fam).find((k) => q === k);
    if (!key) return { payload: { data: { result: [] } } };
    const field = fam[key][0];
    let result = [series(0, GPU0[field]), series(1, GPU1[field])];
    if (flip.includes(key)) result = result.slice().reverse();
    return {
      payload: { status: 'success', data: { resultType: 'vector', result } },
    };
  };
}

test('gpu() returns one entry per card, values keyed by the gpu label', async () => {
  const { api } = loadApiWithRecorder(promResponder(), {}, OPTS);
  const out = await api.gpu();

  assert.equal(out.gpus.length, 2, 'both cards surface');
  // .join, not deepEqual: the array is minted inside the vm realm, so
  // deepStrictEqual's cross-realm prototype check rejects equal contents.
  assert.equal(
    out.gpus.map((c) => c.index).join(','),
    '0,1',
    'cards are ordered by numeric gpu index'
  );

  const [c0, c1] = out.gpus;
  assert.equal(c0.util, 16);
  assert.equal(c0.temp, 38);
  assert.equal(c0.powerMax, 600);
  assert.equal(c1.util, 6);
  assert.equal(c1.temp, 32);
  assert.equal(c1.powerMax, 390, "the 3090's limit is not the 5090's");
  // MiB -> GB, one decimal.
  assert.equal(c0.vramUsed, 30.4);
  assert.equal(c0.vramTotal, 31.8);
  assert.equal(c1.vramUsed, 20.2);
  assert.equal(c1.vramTotal, 24);
});

test('gpu() never pairs one card VRAM used with another card total', async () => {
  // memory_total arrives gpu=1-first while memory_used arrives gpu=0-first.
  // Under the old result[0] code this produced 30.4 GB used / 24 GB total —
  // over 100% of a card that does not exist.
  const { api } = loadApiWithRecorder(
    promResponder(['nvidia_gpu_memory_total_mib']),
    {},
    OPTS
  );
  const out = await api.gpu();

  const [c0, c1] = out.gpus;
  assert.equal(c0.vramUsed, 30.4);
  assert.equal(c0.vramTotal, 31.8, 'card 0 total came from card 0');
  assert.equal(c1.vramUsed, 20.2);
  assert.equal(c1.vramTotal, 24, 'card 1 total came from card 1');
  out.gpus.forEach((c) =>
    assert.ok(
      c.vramUsed <= c.vramTotal,
      `card ${c.index} usage must not exceed its own capacity`
    )
  );
});

test('gpu() top-level scalars track the lowest-indexed card, not series order', async () => {
  // Every family served in reverse — the top-level tile must still describe
  // GPU 0 deterministically, since modes.jsx / the drawer read these.
  const flipAll = [
    'nvidia_gpu_utilization_percent',
    'nvidia_gpu_temperature_celsius',
    'nvidia_gpu_power_draw_watts',
    'nvidia_gpu_power_limit_watts',
    'nvidia_gpu_memory_used_mib',
    'nvidia_gpu_memory_total_mib',
    'nvidia_gpu_fan_speed_percent',
    'nvidia_gpu_clock_graphics_mhz',
  ];
  const { api } = loadApiWithRecorder(promResponder(flipAll), {}, OPTS);
  const out = await api.gpu();

  assert.equal(out.util, 16, 'top-level util is GPU 0');
  assert.equal(out.temp, 38);
  assert.equal(out.powerMax, 600);
  assert.equal(out.vramTotal, 31.8);
  assert.equal(out.gpus[0].index, 0);
});

test('gpu() does not fabricate a card model or driver in live mode', async () => {
  const { api } = loadApiWithRecorder(promResponder(), {}, OPTS);
  const out = await api.gpu();
  // nvidia_gpu_* carries neither; inheriting the mock's "RTX 5090" would label
  // a second, different card with the first card's model.
  assert.equal(out.name, '');
  assert.equal(out.driver, '');
  assert.equal(
    out.gpus.map((c) => c.name).join('|'),
    'GPU 0|GPU 1',
    'cards are labelled by index, not a guessed model'
  );
});

test('gpu() survives a single-GPU host and an empty Prometheus', async () => {
  const single = ({ url }) => {
    const q = new URL(url, 'http://local').searchParams.get('query') || '';
    if (q === 'nvidia_gpu_utilization_percent') {
      return { payload: { data: { result: [series(0, 42)] } } };
    }
    return { payload: { data: { result: [] } } };
  };
  const { api } = loadApiWithRecorder(single, {}, OPTS);
  const one = await api.gpu();
  assert.equal(one.gpus.length, 1, 'one card in, one card out');
  assert.equal(one.util, 42);

  // Prometheus returns nothing at all → mock fallback, no throw, no cards.
  const { api: api2 } = loadApiWithRecorder(
    () => ({ payload: { data: { result: [] } } }),
    {},
    OPTS
  );
  const none = await api2.gpu();
  assert.equal(none.gpus.length, 0);
  assert.equal(typeof none.util, 'number', 'falls back to a number');
  assert.ok(Array.isArray(none.procs));
});

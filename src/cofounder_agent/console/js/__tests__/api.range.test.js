'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadApiWithFetch,
  loadApiWithRecorder,
} = require('./contracts/contract-runtime.js');

// api.js references window.PX.ts — preload timeseries.js into the sandbox first.
const OPTS = { preload: ['timeseries.js'] };

test('promRange maps a query_range matrix to canonical series', async () => {
  let captured = '';
  const fetchImpl = async (url) => {
    captured = String(url);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          resultType: 'matrix',
          result: [
            {
              metric: {},
              values: [
                [1000, '1.5'],
                [1015, '2.5'],
              ],
            },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(
    fetchImpl,
    { px_prom: 'http://prom:9091' },
    OPTS
  );
  const out = await api.promRange('up', {
    rangeSeconds: 3600,
    stepSeconds: 15,
  });
  assert.ok(captured.includes('/api/v1/query_range'));
  assert.ok(captured.includes('step=15'));
  assert.equal(out.series[0].points[0][0], 1000 * 1000);
  assert.equal(out.series[0].points[0][1], 1.5);
});

test('promRange returns {series:[]} when the fetch throws (never throws)', async () => {
  const fetchImpl = async () => {
    throw new Error('prometheus down');
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.promRange('up', {
    rangeSeconds: 3600,
    stepSeconds: 15,
  });
  // Array.isArray is cross-realm-safe; deepEqual is not (out is a vm-realm object).
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});

test('promRange returns {series:[]} on a non-200', async () => {
  const fetchImpl = async () => ({
    ok: false,
    status: 503,
    json: async () => ({}),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.promRange('up', {
    rangeSeconds: 3600,
    stepSeconds: 15,
  });
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});

test('httpRateSeries issues a query_range for the request-rate PromQL', async () => {
  let q = '';
  const fetchImpl = async (url) => {
    q = decodeURIComponent(new URL(url).searchParams.get('query') || '');
    return {
      ok: true,
      status: 200,
      json: async () => ({ data: { result: [] } }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.httpRateSeries('1h');
  assert.match(q, /rate\(poindexter_http_requests_total\[/);
  assert.ok(Array.isArray(out.series) && out.series.length === 0); // empty → honest-empty
});

test('httpLatencySeries merges p95 + p99 into two labeled series', async () => {
  const fetchImpl = async (url) => {
    const query = decodeURIComponent(
      new URL(url).searchParams.get('query') || ''
    );
    const quant = query.includes('0.95') ? '0.95' : '0.99';
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [{ metric: { quantile: quant }, values: [[1000, '0.2']] }],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.httpLatencySeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('p95') && labels.includes('p99'));
});

test('qaTrend GETs /api/qa/trend with derived range_seconds+step_seconds', async () => {
  const { api, calls } = loadApiWithRecorder(
    () => ({
      status: 200,
      payload: { series: [{ label: 'pass %', points: [] }] },
    }),
    {},
    { preload: ['timeseries.js'] }
  );
  const out = await api.qaTrend('1h');
  const call = calls.find((c) => c.url.includes('/api/qa/trend'));
  assert.ok(call, 'hit /api/qa/trend');
  assert.match(call.url, /range_seconds=3600/);
  assert.match(call.url, /step_seconds=15/);
  assert.ok(Array.isArray(out.series));
});

test('promRange labels multi-series output via labelBy/labelPrefix', async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: {
        resultType: 'matrix',
        result: [
          { metric: { gpu: '0', job: 'nvidia-smi' }, values: [[1000, '2']] },
          { metric: { gpu: '1', job: 'nvidia-smi' }, values: [[1000, '0']] },
        ],
      },
    }),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.promRange(
    'max by (gpu) (nvidia_gpu_utilization_percent)',
    {
      rangeSeconds: 3600,
      stepSeconds: 15,
      labelBy: 'gpu',
      labelPrefix: 'GPU ',
    }
  );
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('GPU 0') && labels.includes('GPU 1'));
});

test('gpuUtilSeries issues the max-by-gpu utilization query with GPU 0/1 labels', async () => {
  const fetchImpl = async (url) => {
    const q = decodeURIComponent(new URL(url).searchParams.get('query') || '');
    assert.equal(q, 'max by (gpu) (nvidia_gpu_utilization_percent)');
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [
            { metric: { gpu: '0' }, values: [[1000, '2']] },
            { metric: { gpu: '1' }, values: [[1000, '0']] },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.gpuUtilSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('GPU 0') && labels.includes('GPU 1'));
});

test('gpu/vram/system methods issue their exact PromQL', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({ data: { result: [] } }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  await api.gpuTempSeries('1h');
  await api.gpuPowerSeries('1h');
  await api.vramUsedSeries('1h');
  await api.systemPowerSeries('1h');
  assert.ok(seen.includes('max by (gpu) (nvidia_gpu_temperature_celsius)'));
  assert.ok(seen.includes('max by (gpu) (nvidia_gpu_power_draw_watts)'));
  assert.ok(seen.includes('max by (gpu) (nvidia_gpu_memory_used_mib) / 1024'));
  assert.ok(seen.includes('system_total_power_estimate_watts'));
});

test('systemPowerSeries forces a single "total" label', async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: {
        result: [
          { metric: { instance: 'x', job: 'y' }, values: [[1000, '208']] },
        ],
      },
    }),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.systemPowerSeries('1h');
  assert.equal(out.series.length, 1);
  assert.equal(out.series[0].label, 'total');
});

test('vramUsedSeries returns honest-empty when Prometheus throws', async () => {
  const api = loadApiWithFetch(
    async () => {
      throw new Error('prometheus down');
    },
    {},
    OPTS
  );
  const out = await api.vramUsedSeries('6h');
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});

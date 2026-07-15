'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadApiWithFetch,
  loadApiWithRecorder,
} = require('./contracts/contract-runtime.js');

// api.js references window.PX.ts — preload timeseries.js into the sandbox first.
const OPTS = { preload: ['timeseries.js'] };

// http()-backed calls (e.g. /api/settings) mint an OAuth token first; Prometheus
// calls don't. Tests that hit http() need both a client_id/secret seed and a
// /token responder — wrap the caller's fetchImpl to add both.
const CREDS = { px_client_id: 'test-client', px_client_secret: 'test-secret' };
function withToken(fetchImpl) {
  return async (url, opts) => {
    if (String(url).endsWith('/token')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ access_token: 'test-jwt', expires_in: 3600 }),
      };
    }
    return fetchImpl(url, opts);
  };
}

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
  assert.ok(
    seen.includes('psu_total_power_watts or system_total_power_estimate_watts')
  );
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

test('systemPowerSeries prefers psu_total_power_watts over the estimate', async () => {
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
  await api.systemPowerSeries('1h');
  assert.ok(
    seen.includes('psu_total_power_watts or system_total_power_estimate_watts')
  );
});

test('electricityRateKwh parses a numeric settings value', async () => {
  const fetchImpl = withToken(async (url) => {
    assert.ok(String(url).includes('/api/settings'));
    assert.ok(String(url).includes('search=electricity_rate_kwh'));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        items: [{ key: 'electricity_rate_kwh', value: '0.1523' }],
        total: 1,
        limit: 10,
        offset: 0,
      }),
    };
  });
  const api = loadApiWithFetch(fetchImpl, CREDS, OPTS);
  const out = await api.electricityRateKwh();
  assert.equal(out, 0.1523);
});

test('electricityRateKwh returns null when the key is missing or malformed', async () => {
  const missing = loadApiWithFetch(
    withToken(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ items: [], total: 0, limit: 10, offset: 0 }),
    })),
    CREDS,
    OPTS
  );
  assert.equal(await missing.electricityRateKwh(), null);

  const malformed = loadApiWithFetch(
    withToken(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        items: [{ key: 'electricity_rate_kwh', value: 'not-a-number' }],
        total: 1,
        limit: 10,
        offset: 0,
      }),
    })),
    CREDS,
    OPTS
  );
  assert.equal(await malformed.electricityRateKwh(), null);
});

test('electricityCostSeries interpolates the fetched rate into the PromQL', async () => {
  const seen = [];
  const fetchImpl = withToken(async (url) => {
    const u = String(url);
    if (u.includes('/api/settings')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [{ key: 'electricity_rate_kwh', value: '0.2' }],
          total: 1,
          limit: 10,
          offset: 0,
        }),
      };
    }
    seen.push(decodeURIComponent(new URL(u).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({ data: { result: [] } }),
    };
  });
  const api = loadApiWithFetch(fetchImpl, CREDS, OPTS);
  await api.electricityCostSeries('1h');
  assert.ok(
    seen.includes(
      '(psu_total_power_watts or system_total_power_estimate_watts) / 1000 * 0.2 * 24'
    )
  );
});

test('electricityCostSeries is honest-empty when the rate is unavailable', async () => {
  const api = loadApiWithFetch(
    withToken(async (url) => {
      if (String(url).includes('/api/settings')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ items: [], total: 0, limit: 10, offset: 0 }),
        };
      }
      throw new Error('should not query Prometheus without a rate');
    }),
    CREDS,
    OPTS
  );
  const out = await api.electricityCostSeries('1h');
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});

test('electricitySourceNote maps each source to an operator-legible note', async () => {
  const api = loadApiWithFetch(
    async () => ({ ok: true, status: 200, json: async () => ({}) }),
    {},
    OPTS
  );
  assert.equal(
    api.electricitySourceNote('measured', 98.3),
    'measured, live wall power'
  );
  assert.equal(
    api.electricitySourceNote('estimated', 42),
    'estimated — 42% sensor coverage this window'
  );
  assert.equal(
    api.electricitySourceNote('mixed', 55.6),
    'estimated — 56% sensor coverage this window'
  );
  assert.equal(api.electricitySourceNote('none', 0), '— pending');
  assert.equal(api.electricitySourceNote(null, null), '— pending');
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

test('dbConnStateSeries issues the by-state query and labels each state', async () => {
  const fetchImpl = async (url) => {
    const q = decodeURIComponent(new URL(url).searchParams.get('query') || '');
    assert.equal(
      q,
      'sum by (state) (pg_stat_activity_count{state=~"active|idle|idle in transaction"})'
    );
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [
            { metric: { state: 'active' }, values: [[1000, '1']] },
            { metric: { state: 'idle' }, values: [[1000, '29']] },
            { metric: { state: 'idle in transaction' }, values: [[1000, '0']] },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbConnStateSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 3);
  assert.ok(
    labels.includes('active') &&
      labels.includes('idle') &&
      labels.includes('idle in transaction')
  );
});

test('cache-hit / size / dead-tuples issue their exact PromQL', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [
            { metric: { datname: 'poindexter' }, values: [[1000, '1']] },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  await api.dbCacheHitSeries('1h');
  await api.dbSizeSeries('1h');
  await api.dbDeadTuplesSeries('1h');
  assert.ok(
    seen.includes(
      'sum(pg_stat_database_blks_hit) / (sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100'
    )
  );
  assert.ok(
    seen.includes(
      'pg_database_size_bytes{datname=~"poindexter|poindexter_brain"} / 1073741824'
    )
  );
  assert.ok(seen.includes('sum(pg_stat_user_tables_n_dead_tup)'));
});

test('dbSizeSeries labels series by datname', async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: {
        result: [
          { metric: { datname: 'poindexter' }, values: [[1000, '2']] },
          { metric: { datname: 'poindexter_brain' }, values: [[1000, '1.5']] },
        ],
      },
    }),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbSizeSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.ok(
    labels.includes('poindexter') && labels.includes('poindexter_brain')
  );
});

test('dbConnectionsSeries merges in-use + max into two labeled series', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: { result: [{ metric: {}, values: [[1000, '1']] }] },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbConnectionsSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('in use') && labels.includes('max'));
  assert.ok(seen.includes('sum(pg_stat_database_numbackends)'));
  assert.ok(seen.includes('pg_settings_max_connections'));
});

test('dbTxnRateSeries merges commits + rollbacks with a rate window', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: { result: [{ metric: {}, values: [[1000, '0']] }] },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbTxnRateSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('commits/s') && labels.includes('rollbacks/s'));
  assert.ok(seen.includes('sum(rate(pg_stat_database_xact_commit[60s]))'));
  assert.ok(seen.includes('sum(rate(pg_stat_database_xact_rollback[60s]))'));
});

test('dbCacheHitSeries returns honest-empty when Prometheus throws', async () => {
  const api = loadApiWithFetch(
    async () => {
      throw new Error('prometheus down');
    },
    {},
    OPTS
  );
  const out = await api.dbCacheHitSeries('6h');
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});

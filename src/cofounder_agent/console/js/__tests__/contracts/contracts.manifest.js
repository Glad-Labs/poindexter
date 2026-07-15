'use strict';
// The one source of truth for the console live-contract net. Each row pins a
// PX.api network surface: its outgoing request always; its adapter output shape
// and OpenAPI schema where it transforms a response. Doubles as the executable
// endpoint map. Local config setters (setLive/setClient/setBase/setPrometheus/
// setSim/getSim/setScope/grafanaBase/setGrafanaEmbed/isLive/config) touch no
// network and are intentionally excluded.
//
// `assert` is used by the tier-3 `shape` functions below.
const assert = require('node:assert/strict');

module.exports = [
  // ══ TIER 1 — read pass-through (request contract; +openapi where a
  //    response_model exists and the branch does not reshape) ══
  {
    name: 'health',
    invoke: (api) => api.health(),
    request: { method: 'GET', path: '/api/health' },
  },
  {
    name: 'listApprovals',
    invoke: (api) => api.listApprovals(),
    request: {
      method: 'GET',
      path: '/api/tasks/pending-approval',
      query: { limit: 50 },
    },
  },
  {
    name: 'listTasks',
    invoke: (api) => api.listTasks(),
    request: { method: 'GET', path: '/api/tasks' },
  },
  {
    name: 'getTask',
    invoke: (api) => api.getTask('task_1'),
    request: { method: 'GET', path: '/api/tasks/task_1' },
  },
  {
    name: 'listTopicProposals',
    invoke: (api) => api.listTopicProposals(),
    request: { method: 'GET', path: '/api/topics/proposals' },
  },
  {
    name: 'findings',
    invoke: (api) => api.findings(),
    request: { method: 'GET', path: '/api/findings' },
  },
  {
    name: 'logs',
    invoke: (api) => api.logs(),
    request: { method: 'GET', path: '/api/logs' },
  },
  {
    name: 'traces',
    invoke: (api) => api.traces(),
    request: { method: 'GET', path: '/api/traces' },
  },
  {
    name: 'seo',
    invoke: (api) => api.seo(),
    request: { method: 'GET', path: '/api/seo' },
  },
  {
    name: 'socialDrafts',
    invoke: (api) => api.socialDrafts(),
    request: { method: 'GET', path: '/api/social/drafts' },
  },
  // These reads carry no `openapi` anchor: their worker routes declare no
  // response_model (verified against the live /api/openapi.json — only budget /
  // settings / media-approval / scheduling do), so the schema layer would no-op.
  // Request-only keeps them honest and out of the recorder (no dead fixtures;
  // memory/search is also >8s, past api.js's client timeout).
  {
    name: 'memorySearch',
    invoke: (api) => api.memorySearch('hello'),
    request: {
      method: 'GET',
      path: '/api/memory/search',
      query: { q: 'hello' },
    },
  },
  {
    name: 'posts',
    invoke: (api) => api.posts(),
    request: { method: 'GET', path: '/api/posts' },
  },
  {
    name: 'analyticsViews',
    invoke: (api) => api.analyticsViews(),
    request: { method: 'GET', path: '/api/analytics/views' },
  },
  {
    // The one tier-1 read with a response_model → the schema layer is live here.
    name: 'budget',
    invoke: (api) => api.budget(),
    request: { method: 'GET', path: '/api/metrics/costs/budget' },
    openapi: { path: '/api/metrics/costs/budget', method: 'get' },
  },
  {
    name: 'newsletter',
    invoke: (api) => api.newsletter(),
    request: { method: 'GET', path: '/api/newsletter/stats' },
  },
  {
    name: 'brainActivity',
    invoke: (api) => api.brainActivity(),
    request: { method: 'GET', path: '/api/brain/stats' },
  },

  // ══ TIER 2 — write / mutation (request contract incl. body; never recorded) ══
  {
    name: 'updateSetting',
    invoke: (api) => api.updateSetting('site_title', 'Glad Labs'),
    request: {
      method: 'PUT',
      path: '/api/settings/site_title',
      body: { value: 'Glad Labs' },
    },
  },
  {
    name: 'approve',
    invoke: (api) => api.approve('task_1'),
    request: {
      method: 'POST',
      path: '/api/tasks/task_1/approve',
      body: { approved: true, auto_publish: false },
    },
  },
  {
    name: 'reject',
    invoke: (api) => api.reject('task_1', 'needs sources'),
    // Regression anchor: reject must send RejectionRequest {reason, feedback,
    // allow_revisions}, NOT the approve route's {human_feedback} (audit bug #3).
    request: {
      method: 'POST',
      path: '/api/tasks/task_1/reject',
      body: {
        reason: 'operator_rejected',
        feedback: 'needs sources',
        allow_revisions: true,
      },
    },
  },
  {
    name: 'publishTask',
    invoke: (api) => api.publishTask('task_1'),
    request: { method: 'POST', path: '/api/tasks/task_1/publish' },
  },
  {
    name: 'retryTask',
    invoke: (api) => api.retryTask('task_1'),
    request: {
      method: 'PUT',
      path: '/api/tasks/task_1/status',
      body: { status: 'pending' },
    },
  },
  {
    name: 'killTask',
    invoke: (api) => api.killTask('task_1'),
    request: { method: 'DELETE', path: '/api/tasks/task_1' },
  },
  {
    name: 'rankTopicBatch',
    invoke: (api) => api.rankTopicBatch('batch_1', ['cand_a', 'cand_b']),
    request: {
      method: 'POST',
      path: '/api/topics/batch_1/rank',
      body: { ordered_candidate_ids: ['cand_a', 'cand_b'] },
    },
  },
  {
    name: 'resolveTopicBatch',
    invoke: (api) => api.resolveTopicBatch('batch_1'),
    request: { method: 'POST', path: '/api/topics/batch_1/resolve' },
  },
  {
    name: 'rejectTopicBatch',
    invoke: (api) => api.rejectTopicBatch('batch_1', 'off_topic'),
    request: {
      method: 'POST',
      path: '/api/topics/batch_1/reject',
      body: { reason: 'off_topic' },
    },
  },
  {
    name: 'mediaDecide',
    invoke: (api) => api.mediaDecide('post_1', 'video', false),
    request: {
      method: 'POST',
      path: '/api/media-approval/post_1/video/decide',
      body: { approved: false, notes: null },
    },
  },
  {
    name: 'scheduleShift',
    invoke: (api) => api.scheduleShift('1 hour', ['post_1']),
    request: {
      method: 'PATCH',
      path: '/api/scheduling/shift',
      body: { by_delta: '1 hour', post_ids: ['post_1'] },
    },
  },
  {
    name: 'socialDraftAction',
    invoke: (api) => api.socialDraftAction('draft_1', 'approve'),
    request: { method: 'POST', path: '/api/social/drafts/draft_1/approve' },
  },
  {
    name: 'restartService',
    invoke: (api) => api.restartService('poindexter-worker'),
    request: {
      method: 'POST',
      path: '/api/admin/restart',
      body: { service: 'poindexter-worker' },
    },
  },
  {
    name: 'rebuildExport',
    invoke: (api) => api.rebuildExport(),
    request: { method: 'POST', path: '/api/export/rebuild' },
  },

  // ══ TIER 3 — read transform (request + adapter-shape + schema where routed) ══
  // Fixture filename defaults to `<name>.json`. `openapi` is present only where
  // the worker route declares a response_model (settings / media-approval /
  // scheduling — verified live); memoryStats/pipelineEvents have none, so they
  // carry shape only. Shapes assert with typeof/Array.isArray (cross-realm rule).
  {
    name: 'listSettings',
    invoke: (api) => api.listSettings(),
    request: {
      method: 'GET',
      path: '/api/settings',
      query: { limit: 100, offset: 0 },
    },
    shape: (out) => {
      assert.ok(Array.isArray(out.settings), 'settings is an array');
      assert.ok(Array.isArray(out.categories), 'categories is an array');
      assert.equal(typeof out.total, 'number', 'total is a number');
    },
    openapi: { path: '/api/settings', method: 'get' },
  },
  {
    name: 'mediaQueue',
    invoke: (api) => api.mediaQueue(),
    request: { method: 'GET', path: '/api/media-approval/pending' },
    shape: (out) => {
      assert.ok(Array.isArray(out.queue), 'queue is an array');
      assert.equal(
        typeof out.gate2Pending,
        'number',
        'gate2Pending is a number'
      );
      out.queue.forEach((r) => assert.equal(typeof r.post_id, 'string'));
    },
    openapi: { path: '/api/media-approval/pending', method: 'get' },
  },
  {
    name: 'schedule',
    invoke: (api) => api.schedule(),
    request: { method: 'GET', path: '/api/scheduling' },
    shape: (out) => {
      assert.ok(Array.isArray(out.rows), 'rows is an array');
      assert.equal(typeof out.count, 'number', 'count is a number');
    },
    openapi: { path: '/api/scheduling', method: 'get' },
  },
  {
    name: 'memoryStats',
    invoke: (api) => api.memoryStats(),
    request: { method: 'GET', path: '/api/memory/stats' },
    shape: (out) => {
      assert.equal(
        typeof out.totalEmbeddings,
        'number',
        'totalEmbeddings is a number'
      );
      assert.ok(Array.isArray(out.bySource), 'bySource is an array');
      assert.ok(Array.isArray(out.byWriter), 'byWriter is an array');
    },
  },
  {
    name: 'pipelineEvents',
    invoke: (api) => api.pipelineEvents(),
    request: {
      method: 'GET',
      path: '/api/pipeline/events',
      query: { limit: 50, since_minutes: 120 },
    },
    shape: (out) => {
      assert.ok(
        Array.isArray(out),
        'pipelineEvents returns an array of feed lines'
      );
      out.forEach((line) => {
        assert.ok(
          Array.isArray(line.tag),
          'each feed line has a [tone,label] tag'
        );
        assert.equal(typeof line.html, 'string', 'each feed line has html');
      });
    },
  },
  {
    name: 'voiceJoinUrl',
    invoke: (api) => api.voiceJoinUrl(),
    request: {
      method: 'GET',
      path: '/api/settings',
      query: { search: 'voice_agent_public_join_url', limit: 10 },
    },
    shape: (out) =>
      assert.equal(typeof out, 'string', 'voiceJoinUrl returns a string'),
    openapi: { path: '/api/settings', method: 'get' },
  },
  {
    name: 'electricityRateKwh',
    invoke: (api) => api.electricityRateKwh(),
    request: {
      method: 'GET',
      path: '/api/settings',
      query: { search: 'electricity_rate_kwh', limit: 10 },
    },
    shape: (out) =>
      assert.ok(
        out === null || (typeof out === 'number' && out > 0),
        'electricityRateKwh returns a positive number or null'
      ),
    openapi: { path: '/api/settings', method: 'get' },
  },

  // ══ TIER 3 — Prometheus (request PromQL + vector/scalar shape; no OpenAPI) ══
  {
    name: 'promScalar',
    invoke: (api) => api.promScalar('up'),
    request: { host: 'prometheus', query: 'up' },
    shape: (out) =>
      assert.ok(
        out === null || typeof out === 'number',
        'scalar is number|null'
      ),
  },
  {
    name: 'promVector',
    invoke: (api) => api.promVector('up'),
    request: { host: 'prometheus', query: 'up' },
    shape: (out) => {
      assert.ok(Array.isArray(out), 'vector is an array');
      out.forEach((s) => {
        assert.ok('labels' in s, 'each series has labels');
        assert.ok('value' in s, 'each series has a value');
      });
    },
  },

  // ── C: time-series trends (Prometheus range) — request-only, no OpenAPI ──
  {
    name: 'httpRateSeries',
    invoke: (api) => api.httpRateSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'sum(rate(poindexter_http_requests_total[60s]))',
    },
  },
  {
    name: 'httpErrorSeries',
    invoke: (api) => api.httpErrorSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query:
        'sum(rate(poindexter_http_requests_total{status=~"5.."}[60s])) ' +
        '/ sum(rate(poindexter_http_requests_total[60s])) * 100',
    },
  },
  {
    name: 'httpLatencySeries',
    invoke: (api) => api.httpLatencySeries('1h'),
    request: [
      {
        host: 'prometheus',
        rangeQuery: true,
        query:
          'histogram_quantile(0.95, sum(rate(poindexter_http_request_duration_seconds_bucket[60s])) by (le))',
      },
      {
        host: 'prometheus',
        rangeQuery: true,
        query:
          'histogram_quantile(0.99, sum(rate(poindexter_http_request_duration_seconds_bucket[60s])) by (le))',
      },
    ],
  },
  {
    name: 'throughputSeries',
    invoke: (api) => api.throughputSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'poindexter_posts_total{status="published"}',
    },
  },
  {
    name: 'costSeries',
    invoke: (api) => api.costSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'poindexter_daily_spend_usd',
    },
  },

  // ── D: GPU / hardware / power trends (Prometheus range) — request-only ──
  {
    name: 'gpuUtilSeries',
    invoke: (api) => api.gpuUtilSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_utilization_percent)',
    },
  },
  {
    name: 'gpuTempSeries',
    invoke: (api) => api.gpuTempSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_temperature_celsius)',
    },
  },
  {
    name: 'vramUsedSeries',
    invoke: (api) => api.vramUsedSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_memory_used_mib) / 1024',
    },
  },
  {
    name: 'gpuPowerSeries',
    invoke: (api) => api.gpuPowerSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_power_draw_watts)',
    },
  },
  {
    name: 'systemPowerSeries',
    invoke: (api) => api.systemPowerSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'psu_total_power_watts or system_total_power_estimate_watts',
    },
  },

  // ── E: Postgres internals (Prometheus range) — request-only ──
  {
    name: 'dbConnStateSeries',
    invoke: (api) => api.dbConnStateSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query:
        'sum by (state) (pg_stat_activity_count{state=~"active|idle|idle in transaction"})',
    },
  },
  {
    name: 'dbCacheHitSeries',
    invoke: (api) => api.dbCacheHitSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query:
        'sum(pg_stat_database_blks_hit) / (sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100',
    },
  },
  {
    name: 'dbSizeSeries',
    invoke: (api) => api.dbSizeSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query:
        'pg_database_size_bytes{datname=~"poindexter|poindexter_brain"} / 1073741824',
    },
  },
  {
    name: 'dbDeadTuplesSeries',
    invoke: (api) => api.dbDeadTuplesSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'sum(pg_stat_user_tables_n_dead_tup)',
    },
  },
  {
    name: 'dbConnectionsSeries',
    invoke: (api) => api.dbConnectionsSeries('1h'),
    request: [
      {
        host: 'prometheus',
        rangeQuery: true,
        query: 'sum(pg_stat_database_numbackends)',
      },
      {
        host: 'prometheus',
        rangeQuery: true,
        query: 'pg_settings_max_connections',
      },
    ],
  },
  {
    name: 'dbTxnRateSeries',
    invoke: (api) => api.dbTxnRateSeries('1h'),
    request: [
      {
        host: 'prometheus',
        rangeQuery: true,
        query: 'sum(rate(pg_stat_database_xact_commit[60s]))',
      },
      {
        host: 'prometheus',
        rangeQuery: true,
        query: 'sum(rate(pg_stat_database_xact_rollback[60s]))',
      },
    ],
  },

  // ── C: time-series trends (worker / audit_log) — request-only, no OpenAPI ──
  {
    name: 'qaTrend',
    invoke: (api) => api.qaTrend('1h'),
    request: {
      method: 'GET',
      path: '/api/qa/trend',
      query: { range_seconds: 3600, step_seconds: 15 },
    },
  },
  {
    name: 'findingsTrend',
    invoke: (api) => api.findingsTrend('1h'),
    request: {
      method: 'GET',
      path: '/api/findings/trend',
      query: { range_seconds: 3600, step_seconds: 15 },
    },
  },
];

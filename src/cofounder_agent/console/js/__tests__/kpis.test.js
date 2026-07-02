'use strict';

// Contract tests for the operator console's live KPI mapper
// (src/cofounder_agent/console/js/kpis.js -> PX.kpisFromLive). They run on Node's
// built-in test runner with no build step and no new dependencies, alongside the
// other console-unit tests in this directory — run `npm run test:console`, or
// standalone `node --test src/cofounder_agent/console/js/__tests__/kpis.test.js`.
//
// Unlike api.token.test.js (which vm-evaluates the api.js IIFE against a browser
// shim because api.js touches `window` at load), kpis.js is a PURE, dual-mode
// module (browser global + module.exports), so this requires it directly.
//
// WHY these tests exist: the KPI honest-empty mapping — which KPIs get a real
// value vs. render '—' — is the one branch-heavy bit behind the live KPI strip,
// and feedback_no_dummy_data makes its correctness load-bearing (the strip must
// never carry a mock number into a live view, contradicting the panels beside it).

const test = require('node:test');
const assert = require('node:assert/strict');
const { kpisFromLive } = require('../kpis.js');

const DAY = 86400000;
const NOW = Date.parse('2026-06-21T12:00:00Z');

// Stand-in for the mock PX.kpis base — the ids/units/actions the mapper keys on
// (mirrors data.js). The mapper must preserve id order and project live values
// onto these, never carry the mock value into a live view.
const BASE = [
  {
    id: 'published',
    label: 'Published (30d)',
    value: 79,
    unit: '',
    tone: '',
    delta: 6,
    deltaLabel: '+6 vs prev',
    spark: [1, 2, 3],
  },
  {
    id: 'approval',
    label: 'Awaiting Approval',
    value: 4,
    unit: '',
    tone: 'amber',
    action: 'approvals',
    delta: 4,
    deltaLabel: 'needs you',
    spark: [1, 2],
  },
  {
    id: 'failed',
    label: 'Failed Tasks (24h)',
    value: 3,
    unit: '',
    tone: 'alert',
    action: 'tasks',
    delta: 3,
    deltaLabel: 'investigate',
    spark: [0, 1],
  },
  {
    id: 'quality',
    label: 'Avg Quality (7d)',
    value: 82.4,
    unit: '',
    tone: 'mint',
    delta: 1.7,
    deltaLabel: 'target ≥ 80',
    spark: [80, 82],
  },
  {
    id: 'traffic',
    label: 'Page Views (24h)',
    value: 1284,
    unit: '',
    tone: '',
    delta: -8,
    deltaLabel: '-8% d/d',
    spark: [40, 50],
  },
  {
    id: 'spend',
    label: 'Cloud Spend (mo)',
    value: 11.42,
    unit: '$',
    tone: '',
    delta: -2.1,
    deltaLabel: '$50 budget',
    spark: [1, 2],
  },
];

const byId = (arr, id) => arr.find((k) => k.id === id);

// posts() response stand-in: one published post per `daysAgo` entry.
function postsAgo(daysAgoList) {
  return {
    posts: daysAgoList.map((d, i) => ({
      id: 'p' + i,
      published_at: new Date(NOW - d * DAY).toISOString(),
    })),
    total: daysAgoList.length,
  };
}

test('preserves KPI id order and count', () => {
  const out = kpisFromLive(
    BASE,
    { cost: null, pendingApproval: null, posts: null, views: null },
    NOW
  );
  assert.deepEqual(
    out.map((k) => k.id),
    BASE.map((k) => k.id)
  );
  assert.equal(out.length, BASE.length);
});

test('SPEND maps from the budget-loaded cost state (same source as the Cost panel)', () => {
  const out = kpisFromLive(
    BASE,
    {
      cost: {
        monthToDate: 35.54,
        budget: 150,
        percentUsed: 23.7,
        status: 'healthy',
      },
    },
    NOW
  );
  const spend = byId(out, 'spend');
  assert.equal(spend.value, 35.54);
  assert.equal(spend.unit, '$');
  assert.equal(spend.deltaLabel, '$150 budget');
  assert.equal(spend.tone, ''); // healthy + under 80% used
  assert.deepEqual(spend.spark, []); // budget() has no by-day series → honest blank
});

test('SPEND goes amber at ≥80% of budget or status=warning', () => {
  const hot = byId(
    kpisFromLive(
      BASE,
      {
        cost: {
          monthToDate: 130,
          budget: 150,
          percentUsed: 86.6,
          status: 'healthy',
        },
      },
      NOW
    ),
    'spend'
  );
  assert.equal(hot.tone, 'amber');
  const warn = byId(
    kpisFromLive(
      BASE,
      {
        cost: {
          monthToDate: 40,
          budget: 150,
          percentUsed: 26,
          status: 'warning',
        },
      },
      NOW
    ),
    'spend'
  );
  assert.equal(warn.tone, 'amber');
});

test('APPROVAL reflects the live pending-approval count (inbox-derived)', () => {
  const zero = byId(
    kpisFromLive(BASE, { pendingApproval: 0 }, NOW),
    'approval'
  );
  assert.equal(zero.value, 0);
  assert.equal(zero.tone, '');
  assert.equal(zero.deltaLabel, 'inbox zero');
  const four = byId(
    kpisFromLive(BASE, { pendingApproval: 4 }, NOW),
    'approval'
  );
  assert.equal(four.value, 4);
  assert.equal(four.tone, 'amber');
  assert.equal(four.deltaLabel, 'needs you');
});

test('PUBLISHED counts only posts published within the trailing 30 days', () => {
  // 3 inside the window (today, 10d, 29d) + 2 outside (31d, 200d)
  const pub = byId(
    kpisFromLive(BASE, { posts: postsAgo([0, 10, 29, 31, 200]) }, NOW),
    'published'
  );
  assert.equal(pub.value, 3);
  assert.equal(pub.spark.length, 30);
  assert.equal(
    pub.spark.reduce((a, b) => a + b, 0),
    3
  ); // histogram sums to the count
  assert.equal(pub.spark[29], 1); // today's bucket holds the 0-day-old post
  assert.equal(pub.spark[0], 1); // oldest bucket holds the 29-day-old post
  assert.equal(pub.deltaLabel, 'last 30d');
});

test('TRAFFIC sums the last-24h page-view buckets', () => {
  const views = {
    daily: [
      { day: '2026-06-20', views: 18 },
      { day: '2026-06-21', views: 24 },
    ],
  };
  const t = byId(kpisFromLive(BASE, { views }, NOW), 'traffic');
  assert.equal(t.value, 42);
  assert.deepEqual(t.spark, []);
  assert.equal(t.deltaLabel, 'last 24h');
});

test('QUALITY averages quality_score over the trailing-30d window only', () => {
  const posts = {
    posts: [
      // in-window, scored → counted
      {
        id: 'a',
        published_at: new Date(NOW - 1 * DAY).toISOString(),
        quality_score: 90,
      },
      {
        id: 'b',
        published_at: new Date(NOW - 10 * DAY).toISOString(),
        quality_score: 70,
      },
      // in-window, unscored (pre-seam post) → excluded, not treated as 0
      {
        id: 'c',
        published_at: new Date(NOW - 2 * DAY).toISOString(),
        quality_score: null,
      },
      // out-of-window, scored → excluded
      {
        id: 'd',
        published_at: new Date(NOW - 40 * DAY).toISOString(),
        quality_score: 10,
      },
    ],
    total: 4,
  };
  const q = byId(kpisFromLive(BASE, { posts }, NOW), 'quality');
  assert.equal(q.value, 80); // (90 + 70) / 2
  assert.equal(q.label, 'Avg Quality (30d)'); // live window ≠ the mock's 7d
  assert.equal(q.deltaLabel, '2 scored');
  assert.equal(q.tone, 'mint'); // ≥ 80
});

test('QUALITY goes amber below 80 and honest-empty with zero scored posts', () => {
  const low = {
    posts: [
      {
        id: 'a',
        published_at: new Date(NOW - 1 * DAY).toISOString(),
        quality_score: 60,
      },
    ],
    total: 1,
  };
  assert.equal(
    byId(kpisFromLive(BASE, { posts: low }, NOW), 'quality').tone,
    'amber'
  );

  const unscored = byId(
    kpisFromLive(BASE, { posts: postsAgo([0, 1, 2]) }, NOW),
    'quality'
  );
  assert.equal(unscored.value, '—'); // no quality_score anywhere → honest-empty
  assert.deepEqual(unscored.spark, []);
});

test('FAILED counts only failures with a timestamp in the trailing 24h', () => {
  const failedTasks = {
    items: [
      // 2h ago ✓
      { id: 't1', completed_at: new Date(NOW - 2 * 3600 * 1000).toISOString() },
      // 20h ago ✓ (completed_at absent → falls back to updated_at)
      { id: 't2', updated_at: new Date(NOW - 20 * 3600 * 1000).toISOString() },
      // 30h ago ✗
      {
        id: 't3',
        completed_at: new Date(NOW - 30 * 3600 * 1000).toISOString(),
      },
      // no timestamp at all ✗ (never guessed into the window)
      { id: 't4' },
    ],
    total: 4,
  };
  const f = byId(kpisFromLive(BASE, { failedTasks }, NOW), 'failed');
  assert.equal(f.value, 2);
  assert.equal(f.tone, 'alert');
  assert.equal(f.deltaLabel, 'investigate');
});

test('FAILED shows a real 0 (clean day) but honest-empty when the read failed', () => {
  const clean = byId(
    kpisFromLive(BASE, { failedTasks: { items: [], total: 0 } }, NOW),
    'failed'
  );
  assert.equal(clean.value, 0); // a clean 24h is REAL data, not empty
  assert.equal(clean.deltaLabel, 'clean 24h');
  assert.equal(clean.tone, '');

  const missing = byId(kpisFromLive(BASE, {}, NOW), 'failed');
  assert.equal(missing.value, '—'); // read failed/absent → honest-empty
  assert.deepEqual(missing.spark, []);
});

test('honest-empty when a read is missing — never the carried-over mock value', () => {
  const out = kpisFromLive(
    BASE,
    { cost: null, pendingApproval: null, posts: null, views: null },
    NOW
  );
  for (const id of ['published', 'traffic', 'spend']) {
    const k = byId(out, id);
    assert.equal(k.value, '—', `${id} should be '—' when its read is null`);
    assert.deepEqual(k.spark, []);
  }
  // honest-empty drops the '$' unit so we never render a bare "$—"
  assert.equal(byId(out, 'spend').unit, '');
});

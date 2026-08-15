'use strict';

// Contract tests for the console live-activity mapper
// (src/cofounder_agent/console/js/activity.js -> PX.mapActivity). Pure dual-mode
// module, required directly like kpis.test.js. Run `npm run test:console` or
// `node --test src/cofounder_agent/console/js/__tests__/activity.test.js`.
//
// WHY: the kind→column split and elapsed math is the one branchy bit behind the
// NOW RUNNING band; feedback_no_dummy_data makes "idle maps to empty" load-bearing.

const test = require('node:test');
const assert = require('node:assert/strict');
const { mapActivity, mapPulseFreshness } = require('../activity.js');

const NOW = Date.parse('2026-07-10T12:00:00Z');
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

test('splits content/media into production, jobs+brain into background', () => {
  const raw = {
    running: [
      {
        kind: 'content',
        title: 'Post',
        step: 'qa.critic',
        progress_pct: 62,
        started_at: iso(180000),
      },
      {
        kind: 'job',
        ref_id: 'dispatch_media_pipeline',
        title: 'Media dispatch',
        started_at: iso(60000),
      },
      { kind: 'brain', title: 'Brain monitor cycle', started_at: iso(5000) },
    ],
    recent: [],
    summary: { running_by_kind: { content: 1, job: 1, brain: 1 } },
  };
  const m = mapActivity(raw, NOW);
  assert.equal(m.inProduction.length, 1);
  assert.equal(m.background.length, 2);
  assert.equal(m.inProduction[0].elapsedS, 180); // seconds since started_at
});

test('honest-empty when nothing running', () => {
  const m = mapActivity(
    { running: [], recent: [], summary: { running_by_kind: {} } },
    NOW
  );
  assert.deepEqual(m.inProduction, []);
  assert.deepEqual(m.background, []);
});

test('trail carries duration_ms as durationMs', () => {
  const m = mapActivity(
    {
      running: [],
      recent: [{ kind: 'job', title: 'x', status: 'ok', duration_ms: 8200 }],
    },
    NOW
  );
  assert.equal(m.trail[0].durationMs, 8200);
});

// ── mapPulseFreshness — the band's stale dressing ──────────────────────────
// WHY: the polled resource retains last-good data on error (deliberate), and
// elapsedS above recomputes ages from started_at every render — so a retained
// row keeps aging on screen. Observed 2026-08-15: a finished run_taps job sat
// in BACKGROUND for hours, aging, while /api/activity served empty to live
// pollers, because app.jsx dropped the resource's stale flag at the band
// boundary. These pin the presentation contract that closes the gap.

test('mapPulseFreshness: stale resource flags the band and keeps the badge', () => {
  const f = mapPulseFreshness({ stale: true, lastUpdatedAt: NOW - 42000 });
  assert.equal(f.stale, true);
  assert.equal(f.bandClass, 'nowrun nowrun--stale');
  assert.equal(f.showBadge, true);
  assert.equal(f.lastUpdatedAt, NOW - 42000);
});

test('mapPulseFreshness: fresh resource renders the plain band', () => {
  const f = mapPulseFreshness({ stale: false, lastUpdatedAt: NOW });
  assert.equal(f.stale, false);
  assert.equal(f.bandClass, 'nowrun');
  assert.equal(f.showBadge, true);
});

test('mapPulseFreshness: null-safe when no resource is passed (no badge, not stale)', () => {
  for (const fresh of [undefined, null]) {
    const f = mapPulseFreshness(fresh);
    assert.equal(f.stale, false);
    assert.equal(f.bandClass, 'nowrun');
    assert.equal(f.showBadge, false);
    assert.equal(f.lastUpdatedAt, null);
  }
});

test('mapPulseFreshness: never-loaded resource is stale with a null timestamp', () => {
  const f = mapPulseFreshness({ stale: true, lastUpdatedAt: null });
  assert.equal(f.stale, true);
  assert.equal(f.lastUpdatedAt, null); // Freshness renders "stale —"
});

test('staleness is presentation-only: retained rows still map, never swapped for idle', () => {
  // The incident payload: one retained background job, hours old.
  const retained = {
    running: [
      {
        kind: 'job',
        ref_id: 'run_taps',
        title: 'run_taps',
        started_at: iso(3 * 3600 * 1000),
      },
    ],
    recent: [],
    summary: { running_by_kind: { job: 1 } },
  };
  const m = mapActivity(retained, NOW);
  const f = mapPulseFreshness({ stale: true, lastUpdatedAt: NOW - 600000 });
  // Rows survive (honest retained data, feedback_no_dummy_data)…
  assert.equal(m.background.length, 1);
  assert.equal(m.background[0].elapsedS, 3 * 3600);
  // …but the band is dressed stale so they can't read as live work.
  assert.equal(f.stale, true);
  assert.match(f.bandClass, /nowrun--stale/);
});

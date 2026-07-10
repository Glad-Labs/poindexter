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
const { mapActivity } = require('../activity.js');

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

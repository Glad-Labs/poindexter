'use strict';

// Contract tests for the publish-slot picker helpers
// (src/cofounder_agent/console/js/schedule-helpers.js) — the local↔UTC
// boundary, slot validation, and the preset list behind the approve
// drawer's Schedule action. Same vm harness as the chat/api tests: the
// REAL file evaluated in a Node vm context, no DOM, no build.
//
// Why these are pinned: the console's Schedule button was a three-line
// stub until 2026-08-08 that toasted "Scheduled for 09:00 tomorrow" and
// called nothing. The replacement's whole job is to turn an operator's
// wall-clock pick into the UTC instant scheduled_publisher polls for, so
// the conversion and the "is this slot usable" predicate are the two
// places a regression would silently un-schedule posts again.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// vm-realm objects carry foreign prototypes, so `instanceof Date` and
// strict deepEqual against host values both fail. Normalize through JSON
// for structural comparison (same trick as chat-helpers.test.js), and
// duck-type dates via getTime().
const j = (x) => JSON.parse(JSON.stringify(x));
const isDate = (x) =>
  !!x && typeof x.getTime === 'function' && isFinite(x.getTime());

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'schedule-helpers.js'),
  'utf8'
);

function load() {
  const sandbox = { console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'schedule-helpers.js' });
  return sandbox.PXSchedule;
}

const H = load();

// ── toLocalInput ────────────────────────────────────────────────
// The regression this guards: seeding the input via toISOString() would
// hand back UTC, shifting the displayed hour by the operator's offset.

test('toLocalInput emits datetime-local format in LOCAL time', () => {
  const d = new Date(2026, 7, 9, 9, 0); // 2026-08-09 09:00 local
  assert.equal(H.toLocalInput(d), '2026-08-09T09:00');
});

test('toLocalInput zero-pads month, day, hour and minute', () => {
  const d = new Date(2026, 0, 3, 7, 5); // 2026-01-03 07:05 local
  assert.equal(H.toLocalInput(d), '2026-01-03T07:05');
});

test('toLocalInput round-trips through the local-time Date parse', () => {
  const d = new Date(2026, 10, 1, 14, 30);
  // `new Date('YYYY-MM-DDTHH:mm')` (no zone) parses as LOCAL, so the
  // wall-clock time must survive the round trip in any TZ the CI box uses.
  const back = new Date(H.toLocalInput(d));
  assert.equal(back.getHours(), 14);
  assert.equal(back.getMinutes(), 30);
  assert.equal(back.getDate(), 1);
});

// ── toIso ───────────────────────────────────────────────────────

test('toIso converts a local picker value to a UTC instant', () => {
  const local = H.toLocalInput(new Date(2026, 7, 9, 9, 0));
  const iso = H.toIso(local);
  // Same instant, expressed as UTC — compare instants, not strings, so
  // the assertion holds regardless of the runner's timezone.
  assert.equal(new Date(iso).getTime(), new Date(2026, 7, 9, 9, 0).getTime());
  assert.ok(iso.endsWith('Z'), 'ISO output is UTC-suffixed');
});

test('toIso returns null for an unparseable value rather than Invalid Date', () => {
  assert.equal(H.toIso('not-a-time'), null);
  assert.equal(H.toIso(''), null);
});

// ── slotState ───────────────────────────────────────────────────

test('slotState accepts a future slot and reports lead time', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  const s = H.slotState('2026-08-09T09:00', now);
  assert.equal(s.valid, true);
  assert.equal(s.reason, null);
  assert.equal(s.lead, new Date(2026, 7, 9, 9, 0).getTime() - now.getTime());
});

test('slotState rejects a past slot with reason "past"', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  const s = H.slotState('2026-08-07T09:00', now);
  assert.equal(s.valid, false);
  assert.equal(s.reason, 'past');
  // The date still comes back so the UI can say WHICH slot was wrong.
  assert.ok(isDate(s.date));
});

test('slotState treats empty and unparseable as distinct not-yet-valid states', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  assert.deepEqual(
    { valid: H.slotState('', now).valid, reason: H.slotState('', now).reason },
    { valid: false, reason: 'empty' }
  );
  const bad = H.slotState('2026-13-99T99:99', now);
  assert.equal(bad.valid, false);
  assert.equal(bad.reason, 'unparseable');
});

test('slotState rejects a slot exactly at now (not strictly future)', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  const s = H.slotState(H.toLocalInput(now), now);
  assert.equal(s.valid, false, 'now is not a future slot');
  assert.equal(s.reason, 'past');
});

// ── slotPresets ─────────────────────────────────────────────────

test('slotPresets always offers the two calendar presets', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  const p = H.slotPresets({ rows: [] }, 4, now);
  assert.deepEqual(j(p.map(([label]) => label)), [
    'Tomorrow 09:00',
    'Tomorrow 14:00',
  ]);
  assert.equal(H.toLocalInput(p[0][1]), '2026-08-09T09:00');
  assert.equal(H.toLocalInput(p[1][1]), '2026-08-09T14:00');
});

test('slotPresets adds "After queue" one spacing interval past the last future slot', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  const last = new Date(2026, 7, 10, 9, 0);
  const p = H.slotPresets(
    {
      rows: [
        { published_at: new Date(2026, 7, 9, 9, 0).toISOString() },
        { published_at: last.toISOString() },
      ],
    },
    4,
    now
  );
  assert.equal(p.length, 3);
  assert.equal(p[2][0], 'After queue (+4h)');
  assert.equal(p[2][1].getTime(), last.getTime() + 4 * 3600000);
});

test('slotPresets ignores past queue rows when finding the last slot', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  const future = new Date(2026, 7, 9, 9, 0);
  const p = H.slotPresets(
    {
      rows: [
        { published_at: new Date(2026, 7, 1, 9, 0).toISOString() }, // past
        { published_at: future.toISOString() },
      ],
    },
    4,
    now
  );
  assert.equal(p[2][1].getTime(), future.getTime() + 4 * 3600000);
});

test('slotPresets omits "After queue" when the queue is empty', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  assert.equal(H.slotPresets({ rows: [] }, 4, now).length, 2);
  assert.equal(H.slotPresets(null, 4, now).length, 2);
});

test('slotPresets omits "After queue" rather than guessing an interval', () => {
  // publishSpacingHours() returns null when app_settings is unreadable.
  // Offering a slot computed from a made-up cadence would be worse than
  // offering one fewer button (feedback_no_dummy_data).
  const now = new Date(2026, 7, 8, 12, 0);
  const rows = [{ published_at: new Date(2026, 7, 9, 9, 0).toISOString() }];
  assert.equal(H.slotPresets({ rows }, null, now).length, 2);
  assert.equal(H.slotPresets({ rows }, 0, now).length, 2);
});

test('slotPresets survives malformed published_at values', () => {
  const now = new Date(2026, 7, 8, 12, 0);
  const p = H.slotPresets({ rows: [{ published_at: 'garbage' }] }, 4, now);
  assert.equal(p.length, 2, 'unparseable rows drop out, no NaN slot');
});

// ── leadLabel ───────────────────────────────────────────────────

test('leadLabel picks minute / hour / day units', () => {
  assert.equal(H.leadLabel(30 * 60000), '30m');
  assert.equal(H.leadLabel(5 * 3600000), '5h');
  assert.equal(H.leadLabel(3 * 86400000), '3d');
});

test('leadLabel never renders a bare 0m', () => {
  assert.equal(H.leadLabel(1000), '1m');
});

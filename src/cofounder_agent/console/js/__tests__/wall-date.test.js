'use strict';

// Contract tests for the Wall display's date line
// (src/cofounder_agent/console/js/data.js → PX.wallDate) plus wiring
// guards on modes.jsx. Same vm harness as the schedule/chat tests for
// the pure half: the REAL data.js evaluated in a Node vm context, no
// DOM, no build.
//
// Why these are pinned: WallDisplay's date was the hardcoded literal
// 'MON 08 JUN 2026' — the mock epoch (PX.now = 2026-06-08) rendered by
// hand — sitting under a clock prop that IS live-wired. So the live
// wall showed the right time over a date frozen in June, observed
// 2026-08-31 as "14:05:42 / MON 08 JUN 2026" (both dates were Mondays,
// which kept the weekday looking plausible). The date is now derived
// from PX.wallDate at render time: live gets `new Date()`, mock keeps
// the simulated PX.now. The formatter tests pin the format; the source
// guards pin the derivation, which a pure test cannot see (modes.jsx is
// JSX — like version.test.js / nowrunning.staleness.test.js, the seam
// is checked by reading the source).
//
// Runs with the other console-unit tests: `npm run test:console`, or
// standalone:
//   node --test src/cofounder_agent/console/js/__tests__/wall-date.test.js

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = (f) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');

function load() {
  const sandbox = { console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read('data.js'), sandbox, { filename: 'data.js' });
  return sandbox.PX;
}

const PX = load();

// ── the formatter ───────────────────────────────────────────────

test('wallDate renders the reported regression date correctly', () => {
  // The instant from the bug report: the live wall on 2026-08-31
  // 14:05:42 must say AUG 31, not the mock epoch's June date.
  assert.equal(
    PX.wallDate(new Date(2026, 7, 31, 14, 5, 42)),
    'MON 31 AUG 2026'
  );
});

test('wallDate on the mock epoch matches the old hardcoded string', () => {
  // Mock mode feeds PX.now, so the zero-backend demo renders exactly
  // what the hardcoded scaffold showed — the fix changes live only.
  assert.equal(PX.wallDate(PX.now), 'MON 08 JUN 2026');
});

test('wallDate zero-pads single-digit days', () => {
  assert.equal(PX.wallDate(new Date(2026, 8, 3)), 'THU 03 SEP 2026');
});

test('wallDate walks a full week of weekday tokens', () => {
  // 2026-08-30 was a Sunday; day overflow past Aug 31 rolls into
  // September per Date semantics, covering a month boundary too.
  const week = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
  week.forEach((tok, i) => {
    assert.equal(PX.wallDate(new Date(2026, 7, 30 + i)).slice(0, 3), tok);
  });
});

test('wallDate names all twelve months', () => {
  const months = [
    'JAN',
    'FEB',
    'MAR',
    'APR',
    'MAY',
    'JUN',
    'JUL',
    'AUG',
    'SEP',
    'OCT',
    'NOV',
    'DEC',
  ];
  months.forEach((tok, m) => {
    assert.equal(PX.wallDate(new Date(2026, m, 15)).split(' ')[2], tok);
  });
});

test('wallDate at the year boundaries', () => {
  assert.equal(PX.wallDate(new Date(2026, 0, 1)), 'THU 01 JAN 2026');
  assert.equal(PX.wallDate(new Date(2026, 11, 31)), 'THU 31 DEC 2026');
});

// ── the wiring (source guards — modes.jsx is JSX, not vm-loadable) ──

test('modes.jsx derives the wall date from PX.wallDate, live vs mock', () => {
  const src = read('modes.jsx');
  assert.match(
    src,
    /const date = window\.PX\.wallDate\(\s*window\.PX\.api\.isLive\(\) \? new Date\(\) : window\.PX\.now\s*\)/,
    'WallDisplay must compute date via PX.wallDate — live from new Date(), ' +
      'mock from PX.now — a literal here re-freezes the live wall on one day'
  );
});

test('modes.jsx carries no hardcoded wall-date literal', () => {
  const src = read('modes.jsx');
  assert.doesNotMatch(
    src,
    /'[A-Z]{3} \d{2} [A-Z]{3} \d{4}'/,
    'a hardcoded DDD dd MMM yyyy literal in modes.jsx is the exact bug ' +
      'this suite pins: the wall rendered MON 08 JUN 2026 forever'
  );
});

'use strict';

// Contract tests for the Wall display's TIME line — wall-date.test.js's
// sibling (that suite pins the date under this clock). Same split: the
// pure half runs the REAL data.js in a Node vm; the wiring half reads
// app.jsx source, because JSX is not vm-loadable and the seam here is
// which effect drives setClock.
//
// Why these are pinned: in live mode the only setClock call site sat
// inside the pipeline-events poll, gated on UNSEEN rows — so the topbar
// and WALL clock advanced only when a new event arrived. On a quiet
// system the time froze at the last event's arrival, and a freshly
// opened page showed the hardcoded '14:32:00' scaffold (the formatted
// mock epoch) until the first event landed. Observed 2026-08-31 while
// fixing the sibling wall-date freeze (#3510). The clock is now seeded
// from the formatter at first paint and ticked by a dedicated 1s
// interval in live mode; mock stays simulator-driven (the feed timer
// advances the simulated PX.now via PX.nextTs).
//
// Runs with the other console-unit tests: `npm run test:console`, or
// standalone:
//   node --test src/cofounder_agent/console/js/__tests__/wall-clock.test.js

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

test('hhmmss renders a local wall-clock instant as HH:MM:SS', () => {
  // Local-time constructor on purpose: hhmmss reads toTimeString, so the
  // assertion holds in any zone the suite runs in.
  assert.equal(PX.hhmmss(new Date(2026, 7, 31, 14, 5, 42)), '14:05:42');
  assert.equal(PX.hhmmss(new Date(2026, 0, 1, 0, 0, 0)), '00:00:00');
});

test('hhmmss of the mock epoch is the retired scaffold literal', () => {
  // The deleted '14:32:00' initializer was exactly PX.hhmmss(PX.now) — so
  // seeding mock mode from the formatter changes nothing the demo shows.
  assert.equal(PX.hhmmss(PX.now), '14:32:00');
});

test('nextTs advances the simulated epoch hhmmss reads', () => {
  // The mock clock's driver: the feed simulator calls nextTs() (mutates
  // PX.now forward 30–120s) then setClock(PX.hhmmss(PX.now)).
  const fresh = load();
  const before = fresh.now.getTime();
  const ts = fresh.nextTs();
  assert.equal(ts, fresh.hhmmss(fresh.now));
  const stepMs = fresh.now.getTime() - before;
  assert.ok(
    stepMs >= 30_000 && stepMs <= 120_000,
    `nextTs must step the simulated clock forward 30–120s (got ${stepMs}ms)`
  );
});

// ── the wiring (source guards — app.jsx is JSX, not vm-loadable) ──

test('app.jsx seeds the clock from the formatter, live vs mock', () => {
  const src = read('app.jsx');
  assert.match(
    src,
    /const \[clock, setClock\] = useS\(\(\) =>\s*PX\.hhmmss\(PX\.api\.isLive\(\) \? new Date\(\) : PX\.now\)\s*\)/,
    'the clock state must seed via PX.hhmmss — live from new Date(), mock ' +
      'from PX.now — so first paint never shows a stale scaffold time'
  );
  assert.doesNotMatch(
    src,
    /useS\(\s*'\d{2}:\d{2}:\d{2}'\s*\)/,
    'a hardcoded HH:MM:SS initializer is the exact bug this suite pins: a ' +
      'fresh live page showed the mock epoch until the first event landed'
  );
});

test('app.jsx ticks the live clock on a dedicated interval', () => {
  const src = read('app.jsx');
  assert.match(
    src,
    /if \(!PX\.api\.isLive\(\)\) return undefined;\s*const id = setInterval\(\(\) => setClock\(PX\.hhmmss\(new Date\(\)\)\), 1000\);\s*return \(\) => clearInterval\(id\);/,
    'live mode must drive setClock from its own 1s interval (with cleanup), ' +
      'not from data-poll arrivals — event-coupled ticking is what froze the ' +
      'wall time on a quiet system'
  );
});

test('app.jsx has exactly two setClock drivers: mock simulator + live interval', () => {
  const src = read('app.jsx');
  const calls = src.match(/setClock\(/g) || [];
  assert.equal(
    calls.length,
    2,
    'expected exactly two setClock call sites — the mock feed simulator and ' +
      'the dedicated live interval. A third re-couples the clock to a data ' +
      'poll (the frozen-wall bug) or races the interval'
  );
  assert.match(
    src,
    /setClock\(PX\.hhmmss\(PX\.now\)\)/,
    'the mock path must stay simulator-driven: the feed timer advances the ' +
      'simulated PX.now (nextTs) and sets the clock from it, keeping the ' +
      'zero-backend demo coherent with its feed timestamps'
  );
});

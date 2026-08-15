'use strict';

// Wiring drift guards for the SYSTEM PULSE band's stale dressing.
//
// The reliability core retains last-good /api/activity data on a failed poll
// (deliberate — panels hold their last values) and computes a `stale` flag,
// but app.jsx once passed only `activity=` into <NowRunningBand> — the flag
// was dropped at the band boundary. Because the band recomputes elapsed ages
// from started_at on every render, the retained rows kept aging on screen:
// observed 2026-08-15 as a finished run_taps job sitting in BACKGROUND for
// hours, indistinguishable from live work, while the server was idle.
//
// The presentation logic itself is pure and unit-tested in activity.test.js
// (PX.mapPulseFreshness). What a pure test CANNOT catch is the prop being
// dropped again at either end of the wiring, so — like version.test.js —
// these read the source and pin the seams: app.jsx must pass the resource,
// nowrunning.jsx must consume it, console.css must give the class teeth.
//
// Runs with the other console-unit tests: `npm run test:console`, or
// standalone `node --test src/cofounder_agent/console/js/__tests__/nowrunning.staleness.test.js`.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = (f) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');

test('app.jsx passes the activity resource into NowRunningBand as fresh=', () => {
  const src = read('app.jsx');
  const tag = src.match(/<NowRunningBand\b[\s\S]*?\/>/);
  assert.ok(tag, 'app.jsx must render <NowRunningBand … />');
  assert.match(
    tag[0],
    /fresh=\{activityR\}/,
    'NowRunningBand must receive fresh={activityR} — dropping it re-opens the ' +
      'stale-rows-age-as-live gap (retained payload renders with no staleness cue)'
  );
});

test('nowrunning.jsx consumes fresh: mapPulseFreshness → band class + Freshness badge', () => {
  const src = read('nowrunning.jsx');
  assert.match(
    src,
    /function NowRunningBand\(\{[^}]*\bfresh\b[^}]*\}\)/,
    'NowRunningBand must accept the fresh prop'
  );
  assert.match(
    src,
    /mapPulseFreshness\(fresh\)/,
    'the band must derive its stale dressing from PX.mapPulseFreshness(fresh)'
  );
  assert.match(
    src,
    /<section className=\{f\.bandClass\}/,
    'the section must take its className from the freshness mapping ' +
      '(nowrun--stale is what freezes/dims the band)'
  );
  assert.match(
    src,
    /<Freshness lastUpdatedAt=\{f\.lastUpdatedAt\} stale=\{f\.stale\}/,
    'the header must carry the <Freshness> badge (the colorblind-safe ' +
      '"stale Ns" text label — never animation/color alone)'
  );
});

test('console.css gives nowrun--stale teeth (frozen choreography + dimmed grid)', () => {
  const css = read(path.join('..', 'css', 'console.css'));
  assert.match(
    css,
    /\.nowrun--stale \.nowrun__grid \{\s*opacity:/,
    'stale band must dim its columns'
  );
  // The liveness cues the stale dressing must freeze — the same set the
  // prefers-reduced-motion override targets.
  for (const cue of ['nowrun__scan > i', 'nowrun__eq i', 'nowrun__dot']) {
    assert.ok(
      new RegExp(
        `\\.nowrun--stale \\.${cue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`
      ).test(css),
      `stale band must stop the ${cue} liveness animation`
    );
  }
});

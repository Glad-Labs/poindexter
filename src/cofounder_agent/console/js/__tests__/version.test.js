'use strict';

// Drift guard for the console's topbar version eyebrow.
//
// app.jsx's POINDEXTER_VERSION literal is stamped by release-please via the
// `generic` extra-files entry in release-please-config.json (the
// `// x-release-please-version` annotation), in the same release commit that
// bumps the root package.json — so the two must always agree. They didn't for
// ~100 releases: the release-please workflow passed `release-type` as an
// ACTION INPUT, which flips the v4+/v5 action into bootstrap mode and silently
// ignores the config file, so the header sat at V0.74.0 while the repo reached
// 0.108.x. This test turns any recurrence of that silent stall into a red
// console-unit run instead of a stale topbar.
//
// Runs with the other console-unit tests: `npm run test:console`, or
// standalone `node --test src/cofounder_agent/console/js/__tests__/version.test.js`.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const APP_JSX = path.join(__dirname, '..', 'app.jsx');
// __tests__ → js → console → cofounder_agent → src → repo root
const ROOT_PKG = path.join(
  __dirname,
  '..',
  '..',
  '..',
  '..',
  '..',
  'package.json'
);

test('POINDEXTER_VERSION keeps its release-please annotation', () => {
  const src = fs.readFileSync(APP_JSX, 'utf8');
  const m = src.match(
    /const POINDEXTER_VERSION = '(\d+\.\d+\.\d+)'; \/\/ x-release-please-version/
  );
  assert.ok(
    m,
    "app.jsx must declare `const POINDEXTER_VERSION = 'x.y.z'; // x-release-please-version` " +
      '— the annotation is what lets release-please stamp the literal'
  );
});

test('POINDEXTER_VERSION matches the repo version (root package.json)', () => {
  const src = fs.readFileSync(APP_JSX, 'utf8');
  const m = src.match(/const POINDEXTER_VERSION = '(\d+\.\d+\.\d+)';/);
  assert.ok(m, 'POINDEXTER_VERSION literal not found in app.jsx');
  const pkg = JSON.parse(fs.readFileSync(ROOT_PKG, 'utf8'));
  assert.equal(
    m[1],
    pkg.version,
    `console topbar (${m[1]}) drifted from the repo version (${pkg.version}); ` +
      'release-please bumps both in the same release commit — if this fails, ' +
      'the extra-files marker stopped firing (check release-please-config.json ' +
      'and that the workflow passes NO release-type input)'
  );
});

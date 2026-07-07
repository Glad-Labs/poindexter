'use strict';
// Generic contract runner. One node:test per manifest surface. Auto-discovered
// by `npm run test:console` (the __tests__/**/*.test.js glob) — no CI change.
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {
  loadApiWithRecorder,
  assertRequest,
  requestMatches,
  validateAgainstSchema,
} = require('./contract-runtime.js');
const manifest = require('./contracts.manifest.js');

const FIX_DIR = path.join(__dirname, 'fixtures');
const SNAPSHOT = (() => {
  const p = path.join(__dirname, 'openapi.snapshot.json');
  return fs.existsSync(p)
    ? JSON.parse(fs.readFileSync(p, 'utf8'))
    : { paths: {}, components: { schemas: {} } };
})();

function loadFixture(file) {
  const p = path.join(FIX_DIR, file);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null;
}

for (const row of manifest) {
  test(`contract: ${row.name}`, async () => {
    const fixtureFile = row.fixture || `${row.name}.json`;
    const fixture = loadFixture(fixtureFile);
    // Replay the recorded fixture (or {} when none) as the 200 response for
    // every non-/token call this surface makes.
    const { api, calls } = loadApiWithRecorder(
      () => ({
        status: 200,
        payload: fixture == null ? {} : fixture,
      }),
      {},
      // api.js's trend methods reach window.PX.ts at invoke time — preload it.
      { preload: ['timeseries.js'] }
    );

    const out = await row.invoke(api);

    // ── request contract ──────────────────────────────────────────────────
    // The first non-/token call must match row.request. Paging/retry surfaces
    // (listSettings pages through ~1090 settings) legitimately issue follow-up
    // calls; those are ignored. A composite row (request is an array) matches
    // each expected against some captured call, order-independent
    // (serviceHealth-style fan-out).
    const nonToken = calls.filter((c) => !c.url.endsWith('/token'));
    if (Array.isArray(row.request)) {
      row.request.forEach((exp) => {
        assert.ok(
          nonToken.some((c) => requestMatches(c, exp)),
          `[${row.name}] no captured call matched ${JSON.stringify(exp)}; saw ${JSON.stringify(nonToken)}`
        );
      });
    } else {
      assert.ok(
        nonToken.length >= 1,
        `[${row.name}] expected at least one request, saw none`
      );
      assertRequest(nonToken[0], row.request, row.name);
    }

    // ── adapter-output shape (transform surfaces) ──
    if (row.shape) {
      assert.ok(
        fixture != null,
        `[${row.name}] shape assertion needs fixtures/${fixtureFile} — run record-fixtures.mjs`
      );
      row.shape(out);
    }

    // ── schema conformance (where a response_model exists) ──
    if (row.openapi && fixture != null) {
      validateAgainstSchema(fixture, SNAPSHOT, row.openapi, row.name);
    }
  });
}

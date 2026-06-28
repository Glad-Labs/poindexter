'use strict';
// Contract tests for the console's pure Grafana-embed URL builder
// (js/telemetry.js -> PX.telemetry.grafanaPanelUrl). Runs on node:test with no
// build step (run `npm run test:console`).
const test = require('node:test');
const assert = require('node:assert/strict');
const { grafanaPanelUrl } = require('../telemetry.js');

test('builds a d-solo URL with theme + kiosk defaults', () => {
  const u = grafanaPanelUrl('http://localhost:3000', 'database', 7);
  assert.equal(
    u,
    'http://localhost:3000/d-solo/database?panelId=7&theme=dark&kiosk'
  );
});

test('strips a trailing slash on the base', () => {
  const u = grafanaPanelUrl('http://localhost:3000/', 'hardware-power', 2);
  assert.equal(u.startsWith('http://localhost:3000/d-solo/'), true);
});

test('honest-empty when base or uid missing', () => {
  assert.equal(grafanaPanelUrl('', 'x', 1), '');
  assert.equal(grafanaPanelUrl('http://x', '', 1), '');
});

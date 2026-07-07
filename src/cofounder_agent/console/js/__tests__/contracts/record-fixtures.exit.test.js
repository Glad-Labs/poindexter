'use strict';
// Regression guard for the recorder's EXIT-CODE CONTRACT, which the nightly
// workflow branches on:
//   0 = no drift, 1 = schema drift, 2 = operational (unreachable / bad creds).
//
// The bug this pins: the first nightly smoke test hit a self-hosted CI runner
// (a container) that couldn't reach the worker at localhost:8002. The recorder
// let the `fetch failed` rejection bubble to Node's default exit 1 — byte-
// identical to the "drift" code — so the workflow misread a down worker as
// drift and tried to open an empty PR. A connection failure MUST be exit 2.
//
// These cases run fully offline: they point the recorder at a closed local port
// (instant ECONNREFUSED) or omit creds, so no worker/network is required.
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const net = require('node:net');
const path = require('node:path');

const RECORDER = path.join(__dirname, 'record-fixtures.mjs');

function runRecorder(args, env) {
  return spawnSync(process.execPath, [RECORDER, ...args], {
    env: { ...process.env, ...env },
    encoding: 'utf8',
    timeout: 30000,
  });
}

// Bind an ephemeral port, capture it, then close — the OS-assigned high port is
// now free, so a connection to it is refused (ECONNREFUSED). A guaranteed-closed
// VALID port; a low port like :1 is on the Fetch bad-ports blocklist and throws
// "bad port" (URL validation) before any connection is attempted.
function freeClosedPort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

test('unreachable worker → exit 2 (operational), NOT 1 (drift)', async () => {
  const port = await freeClosedPort();
  const base = `http://127.0.0.1:${port}`;
  // With creds present, the recorder gets past the creds gate and fails at the
  // first fetch (mintToken) with ECONNREFUSED — exercising the connection path.
  const r = runRecorder(['--base', base, '--prometheus', base], {
    CONSOLE_CONTRACT_CLIENT_ID: 'dummy-id',
    CONSOLE_CONTRACT_CLIENT_SECRET: 'dummy-secret',
  });
  assert.equal(
    r.status,
    2,
    `expected exit 2 for an unreachable worker, got ${r.status}. ` +
      `stderr:\n${r.stderr}`
  );
  assert.notEqual(r.status, 1, 'a down worker must never read as schema drift');
  assert.match(
    r.stderr,
    /unreachable/i,
    'stderr should name the unreachable worker'
  );
});

test('missing OAuth creds → exit 2 (operational), NOT 1 (drift)', () => {
  const r = runRecorder(['--base', 'http://127.0.0.1:8002'], {
    CONSOLE_CONTRACT_CLIENT_ID: '',
    CONSOLE_CONTRACT_CLIENT_SECRET: '',
  });
  assert.equal(
    r.status,
    2,
    `expected exit 2 when creds are missing, got ${r.status}. stderr:\n${r.stderr}`
  );
});

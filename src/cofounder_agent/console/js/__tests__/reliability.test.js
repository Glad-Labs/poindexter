'use strict';

// Unit tests for the console reliability core (js/reliability.js) — pure logic
// only (computeStale / resourceReducer / connectionReducer / ConnectionState).
// The React hook usePolledResource is browser-verified, not tested here.
const test = require('node:test');
const assert = require('node:assert/strict');
const { computeStale } = require('../reliability.js');

test('computeStale: fresh data within 2x interval is not stale', () => {
  const now = 100_000;
  assert.equal(computeStale(now - 5_000, 5_000, null, now), false);
});

test('computeStale: data older than 2x interval is stale', () => {
  const now = 100_000;
  assert.equal(computeStale(now - 11_000, 5_000, null, now), true);
});

test('computeStale: an error forces stale regardless of age', () => {
  const now = 100_000;
  assert.equal(computeStale(now, 5_000, new Error('boom'), now), true);
});

test('computeStale: never-loaded (null lastUpdatedAt) is stale', () => {
  assert.equal(computeStale(null, 5_000, null, 100_000), true);
});

const { resourceReducer, RESOURCE_INIT } = require('../reliability.js');

test('resourceReducer: success sets data + timestamp, clears error', () => {
  const s = resourceReducer(RESOURCE_INIT, {
    type: 'success',
    data: [1, 2],
    at: 42,
  });
  assert.deepEqual(s, { data: [1, 2], lastUpdatedAt: 42, error: null });
});

test('resourceReducer: error retains previous data, records error', () => {
  const prev = { data: [1, 2], lastUpdatedAt: 42, error: null };
  const s = resourceReducer(prev, { type: 'error', error: new Error('down') });
  assert.deepEqual(s.data, [1, 2]); // retained
  assert.equal(s.lastUpdatedAt, 42); // unchanged — data is still from t=42
  assert.equal(s.error.message, 'down');
});

test('resourceReducer: success after error clears the error', () => {
  const errored = { data: [1], lastUpdatedAt: 42, error: new Error('x') };
  const s = resourceReducer(errored, { type: 'success', data: [9], at: 99 });
  assert.deepEqual(s, { data: [9], lastUpdatedAt: 99, error: null });
});

const {
  connectionReducer,
  CONNECTION_INIT,
  isDisconnected,
  ConnectionState,
} = require('../reliability.js');

test('connectionReducer: 3 consecutive failures → disconnected', () => {
  let s = CONNECTION_INIT;
  s = connectionReducer(s, { type: 'health-fail' });
  s = connectionReducer(s, { type: 'health-fail' });
  assert.equal(isDisconnected(s), false); // only 2
  s = connectionReducer(s, { type: 'health-fail' });
  assert.equal(isDisconnected(s), true); // 3
});

test('connectionReducer: a health-ok resets the failure counter and stamps lastSeen', () => {
  let s = { consecutiveHealthFailures: 5, lastSeenAt: 1 };
  s = connectionReducer(s, { type: 'health-ok', at: 777 });
  assert.equal(s.consecutiveHealthFailures, 0);
  assert.equal(s.lastSeenAt, 777);
  assert.equal(isDisconnected(s), false);
});

test('ConnectionState store: reportHealth drives getState + notifies subscribers', () => {
  const seen = [];
  const unsub = ConnectionState.subscribe((st) =>
    seen.push(isDisconnected(st))
  );
  ConnectionState.reportHealth(false);
  ConnectionState.reportHealth(false);
  ConnectionState.reportHealth(false);
  assert.equal(isDisconnected(ConnectionState.getState()), true);
  ConnectionState.reportHealth(true, 123);
  assert.equal(isDisconnected(ConnectionState.getState()), false);
  assert.equal(seen[seen.length - 1], false);
  unsub();
  ConnectionState.reportHealth(false); // no throw after unsubscribe
});

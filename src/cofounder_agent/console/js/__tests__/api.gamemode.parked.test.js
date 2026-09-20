'use strict';

// Game-mode parked sidecars must not render as faults.
//
// Game mode (`services/game_mode.py`) stops the GPU sidecars so the operator
// can use the machine. Nothing outside the CLI and the brain could see that
// state, so the console rendered five deliberately-stopped containers as five
// faults — a red "5 SERVICE DOWN" banner, a rail badge, and five Restart
// suggestions, for a mode the operator switched on themselves. That is the
// same defect class as the Map's hardcoded red edge: the surface asserting a
// fault where the system is working as designed, which trains an operator to
// stop reading red.
//
// The line these tests hold: parked is NEUTRAL, never healthy, and the
// suppression is structurally unable to outlive the mode or reach a container
// that is not on its list.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadApiWithRecorder } = require('./contracts/contract-runtime.js');

const OPTS = { preload: ['data.js'] };
const PARKED = [
  'poindexter-speaches',
  'poindexter-chatterbox',
  'poindexter-stable-audio',
  'poindexter-image-gen-server',
  'poindexter-wan-server',
];

// cAdvisor reports only `present`; everything else looks absent.
function responder(gameMode, present = []) {
  return ({ url }) => {
    if (url.includes('/api/game-mode/status')) {
      if (gameMode === 'fail') return { status: 500 };
      return { payload: gameMode };
    }
    if (url.includes('/api/services/host-health')) {
      return { payload: { services: {}, staleness_seconds: 900 } };
    }
    if (url.includes('/api/health')) return { payload: { ok: true } };
    const q = new URL(url, 'http://local').searchParams.get('query') || '';
    if (!q.startsWith('time() - container_last_seen')) {
      return { payload: { data: { result: [] } } };
    }
    return {
      payload: {
        status: 'success',
        data: {
          result: present.map((n) => ({
            metric: { name: n, image: 'img/' + n },
            value: [0, '5'],
          })),
        },
      },
    };
  };
}

const ACTIVE = {
  active: true,
  until: '2099-01-01T00:00:00+00:00',
  parked_services: ['speaches', 'chatterbox'],
  parked_containers: PARKED,
  seconds_remaining: 3600,
};
const INACTIVE = {
  active: false,
  until: null,
  parked_services: [],
  parked_containers: [],
  seconds_remaining: 0,
};

async function rows(gameMode, present) {
  const { api } = loadApiWithRecorder(responder(gameMode, present), {}, OPTS);
  return api.serviceHealth();
}
const by = (rs, c) => rs.find((r) => r.container === c);

test('a parked, stopped sidecar reads neutral instead of down', async () => {
  const rs = await rows(ACTIVE);
  const speaches = by(rs, 'poindexter-speaches');
  assert.equal(speaches.status, 'off', 'not a fault');
  assert.equal(speaches.metric, 'parked · game mode', 'and it says why');
});

test('parked is never healthy — the container really is stopped', async () => {
  const rs = await rows(ACTIVE);
  for (const c of PARKED) {
    assert.notEqual(by(rs, c).status, 'ok', c + ' must not read as running');
  }
});

test('parked sidecars drop out of the SERVICE DOWN count', async () => {
  // The banner, rail badge and restart suggestions all key off status==='err'.
  const active = await rows(ACTIVE);
  const off = await rows(INACTIVE);
  const errs = (rs) => rs.filter((r) => r.status === 'err').length;
  assert.equal(
    errs(off) - errs(active),
    PARKED.length,
    'exactly the parked set leaves the fault count, nothing else'
  );
});

test('game mode OFF leaves a stopped sidecar reading down', async () => {
  const rs = await rows(INACTIVE);
  assert.equal(by(rs, 'poindexter-speaches').status, 'err');
  assert.equal(by(rs, 'poindexter-speaches').metric, 'down');
});

test('an expired mode cannot suppress — the server empties the list', async () => {
  // active:false with a stale list is the shape a naive client would mishandle.
  const rs = await rows({ ...INACTIVE, parked_containers: PARKED });
  assert.equal(by(rs, 'poindexter-speaches').status, 'err');
});

test('a container NOT on the park list is still down during game mode', async () => {
  const rs = await rows(ACTIVE);
  assert.equal(by(rs, 'poindexter-loki').status, 'err');
  assert.equal(by(rs, 'poindexter-loki').metric, 'down');
});

test('a parked service that is RUNNING reads normally, not parked', async () => {
  // The list says "may be parked", not "is parked" — a live series wins.
  const rs = await rows(ACTIVE, ['poindexter-speaches']);
  const speaches = by(rs, 'poindexter-speaches');
  assert.equal(speaches.status, 'ok');
  assert.match(speaches.metric, /^up /);
});

test('an unreachable game-mode endpoint fails SAFE, not quiet', async () => {
  // Excusing a real outage because we could not ask is worse than a red row.
  const rs = await rows('fail');
  assert.equal(by(rs, 'poindexter-speaches').status, 'err');
  assert.equal(by(rs, 'poindexter-speaches').metric, 'down');
});

test('parked rows are labelled as parked, not as absent', async () => {
  const rs = await rows(ACTIVE);
  assert.equal(by(rs, 'poindexter-speaches').probe, 'parked ⏸');
  assert.equal(by(rs, 'poindexter-loki').probe, 'absent ✕');
});

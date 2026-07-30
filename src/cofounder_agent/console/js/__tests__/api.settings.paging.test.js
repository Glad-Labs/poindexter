'use strict';
// Regression guard for loadAllSettings' paging accumulator.
//
// Two defects lived on the same two lines and together turned console-unit red
// on main (RangeError: Maximum call stack size exceeded):
//
//   1. `const items = (first && first.items) || []` ALIASED page 1's array
//      instead of copying it, so the accumulator mutated the caller's payload.
//      When a later page resolved to that same object the loop pushed the array
//      into itself, doubling per page: 100 -> 200 -> 400 -> ... -> 204,800.
//   2. `items.push(...page)` spread every element as a separate argument, so a
//      large page blew the argument limit even without the aliasing.
//
// Both are reachable from real responses, not just fixtures — any transport
// that returns a cached/shared object hits #1, and a single big page hits #2.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadApiWithRecorder } = require('./contracts/contract-runtime.js');

const PAGE = 100;

function page(n) {
  return Array.from({ length: n }, (_, i) => ({
    key: 'k' + i,
    value: 'v',
    category: 'pipeline',
  }));
}

test('loadAllSettings does not alias or mutate the response payload', async () => {
  // ONE shared object replayed for every call — the exact shape that exploded.
  const shared = { items: page(PAGE), total: 1257, limit: PAGE, offset: 0 };
  const before = shared.items.length;

  const { api } = loadApiWithRecorder(
    () => ({ status: 200, payload: shared }),
    {}
  );
  const out = await api.listSettings();

  assert.equal(
    shared.items.length,
    before,
    'loadAllSettings mutated the response payload — it must copy page 1, not alias it'
  );
  assert.ok(Array.isArray(out.settings), 'settings is an array');
  // 1257 total => page 1 + 12 more pages of 100. Linear, not doubled.
  assert.ok(
    out.settings.length <= 1300,
    `settings grew to ${out.settings.length}; a self-referential push is doubling per page`
  );
});

test('loadAllSettings survives a page far larger than the argument limit', async () => {
  // Guards defect #2 independently of #1: a fresh (non-aliased) object each
  // call, but one page big enough that `push(...page)` would RangeError.
  const BIG = 200000;
  const { api } = loadApiWithRecorder(
    () => ({
      status: 200,
      payload: { items: page(BIG), total: BIG, limit: PAGE, offset: 0 },
    }),
    {}
  );

  const out = await api.listSettings();
  assert.ok(
    Array.isArray(out.settings) && out.settings.length >= BIG,
    'a page larger than the spread argument limit must still accumulate'
  );
});

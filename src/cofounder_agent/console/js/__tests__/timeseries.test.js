'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const T = require('../timeseries.js');

test('deriveStep targets ~240 points and clamps to 15s floor', () => {
  assert.equal(T.deriveStep(3600), 15); // 3600/240=15
  assert.equal(T.deriveStep(604800), Math.round(604800 / 240)); // 2520
  assert.equal(T.deriveStep(600), 15); // 600/240=2.5 -> clamp 15
});

test('seriesBounds ignores nulls', () => {
  const s = [
    {
      label: 'a',
      points: [
        [1000, 5],
        [2000, null],
        [3000, 9],
      ],
    },
  ];
  const b = T.seriesBounds(s);
  assert.equal(b.tMin, 1000);
  assert.equal(b.tMax, 3000);
  assert.equal(b.vMin, 5);
  assert.equal(b.vMax, 9);
});

test('isEmptySeries true when all points null or no series', () => {
  assert.equal(T.isEmptySeries([]), true);
  assert.equal(T.isEmptySeries([{ label: 'a', points: [[1, null]] }]), true);
  assert.equal(T.isEmptySeries([{ label: 'a', points: [[1, 2]] }]), false);
});

test('buildPath breaks the path at a null (gap), not zero-fill', () => {
  const box = { x: 0, y: 0, w: 100, h: 50 };
  const bounds = { tMin: 0, tMax: 3, vMin: 0, vMax: 10 };
  const d = T.buildPath(
    [
      [0, 5],
      [1, null],
      [2, 5],
      [3, 5],
    ],
    bounds,
    box
  );
  // exactly two M commands: one before the gap, one after.
  assert.equal((d.match(/M/g) || []).length, 2);
});

test('dashFor cycles distinctly; index 0 is solid', () => {
  assert.equal(T.dashFor(0), '');
  assert.notEqual(T.dashFor(1), T.dashFor(0));
  assert.notEqual(T.dashFor(2), T.dashFor(1));
});

test('matrixToSeries maps a Prometheus matrix to canonical series (sec->ms)', () => {
  const matrix = [
    {
      metric: { quantile: '0.95' },
      values: [
        [1000, '0.2'],
        [1060, '0.3'],
      ],
    },
  ];
  const out = T.matrixToSeries(matrix, 'p95');
  assert.equal(out.length, 1);
  assert.equal(out[0].points[0][0], 1000 * 1000); // seconds -> ms
  assert.equal(out[0].points[0][1], 0.2);
});

test('matrixToSeries labels each series from labelBy with a prefix', () => {
  const matrix = [
    {
      metric: { gpu: '0', instance: 'x', job: 'nvidia-smi' },
      values: [[1000, '2']],
    },
    {
      metric: { gpu: '1', instance: 'x', job: 'nvidia-smi' },
      values: [[1000, '0']],
    },
  ];
  const out = T.matrixToSeries(matrix, undefined, 'gpu', 'GPU ');
  const labels = out.map((s) => s.label);
  assert.equal(out.length, 2);
  assert.ok(labels.includes('GPU 0') && labels.includes('GPU 1'));
  assert.equal(out[0].points[0][0], 1000 * 1000); // still seconds -> ms
});

test('matrixToSeries without labelBy keeps the joined-label behavior', () => {
  const out = T.matrixToSeries([
    { metric: { quantile: '0.95' }, values: [[1000, '0.2']] },
  ]);
  assert.equal(out[0].label, 'quantile=0.95');
});

test('latestPoint returns the last finite-valued point, skipping trailing nulls', () => {
  assert.deepEqual(
    T.latestPoint([
      [1000, 5],
      [2000, 9],
      [3000, null],
    ]),
    [2000, 9]
  );
  assert.deepEqual(T.latestPoint([[1000, 0]]), [1000, 0]); // zero is a real value
});

test('latestPoint is null for empty, missing, or all-null series', () => {
  assert.equal(T.latestPoint([]), null);
  assert.equal(T.latestPoint(undefined), null);
  assert.equal(
    T.latestPoint([
      [1000, null],
      [2000, NaN],
    ]),
    null
  );
});

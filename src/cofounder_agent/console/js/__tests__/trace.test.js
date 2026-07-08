'use strict';

// Pure trace-view logic (trace-helpers.js) — the derivation layer that turns
// live atom_runs rows into the prototype's enriched node shape. Kept separate
// from trace.jsx so it's testable without a React/JSX/jsdom harness. Every
// assertion here pins a translation the live data depends on (group bucketing,
// QA-score parse, rescue-loop detection, honest-empty on missing data).
const test = require('node:test');
const assert = require('node:assert/strict');
const TraceH = require('../trace-helpers.js');

test('groupFor buckets a node into its pipeline phase', () => {
  assert.equal(
    TraceH.groupFor('content.generate_draft', 'content.generate_draft'),
    'Writer'
  );
  assert.equal(TraceH.groupFor('qa.critic', 'qa.critic'), 'QA rails');
  assert.equal(TraceH.groupFor('qa.rewrite', 'qa.rewrite'), 'QA rescue');
  assert.equal(
    TraceH.groupFor('content.generate_images', 'content.generate_images'),
    'Image'
  );
  assert.equal(
    TraceH.groupFor('seo.generate_all_metadata', 'seo.generate_all_metadata'),
    'SEO + media'
  );
  assert.equal(
    TraceH.groupFor('content.persist_task', 'content.persist_task'),
    'Finalize'
  );
  // draft_gate and preview_gate share the atoms.approval_gate atom — the node_id
  // disambiguates which phase they belong to.
  assert.equal(TraceH.groupFor('atoms.approval_gate', 'draft_gate'), 'Writer');
  assert.equal(
    TraceH.groupFor('atoms.approval_gate', 'preview_gate'),
    'Finalize'
  );
  assert.equal(TraceH.groupFor('weird.unknown', 'weird.unknown'), 'Pipeline');
});

test('glyphOf maps live statuses to colorblind glyph + tone class', () => {
  assert.equal(TraceH.glyphOf('halted').char, '✕');
  assert.equal(TraceH.glyphOf('error').char, '✕');
  assert.equal(TraceH.glyphOf('ok').char, '✓');
  assert.equal(TraceH.glyphOf('skipped').char, '○');
  assert.equal(TraceH.glyphOf('running').char, '◐');
  assert.equal(TraceH.glyphOf('ok').cls, 'c-mint');
  assert.equal(TraceH.glyphOf('halted').cls, 'c-red');
});

test('normalizeNodes is honest-empty on no input', () => {
  assert.deepEqual(TraceH.normalizeNodes([]), []);
  assert.deepEqual(TraceH.normalizeNodes(null), []);
});

test('normalizeNodes derives group, QA score/verdict, and halt status', () => {
  const nodes = TraceH.normalizeNodes([
    {
      seq: 2,
      atom: 'content.generate_draft',
      node_id: 'content.generate_draft',
      status: 'ok',
      tier: 'premium',
      model: 'claude-sonnet-5',
      latency_ms: 48000,
      cost: 0.17,
      output_keys: ['draft'],
      output_preview: '{"content":"x"}',
    },
    {
      seq: 18,
      atom: 'qa.critic',
      node_id: 'qa.critic',
      status: 'ok',
      tier: 'standard',
      model: 'glm-4-6',
      latency_ms: 9800,
      cost: 0,
      output_keys: ['qa_rail'],
      output_preview:
        '{"approved":false,"score":58,"veto":"unbenchmarked claim"}',
    },
    {
      seq: 28,
      atom: 'qa.aggregate',
      node_id: 'qa.aggregate',
      status: 'halted',
      tier: 'free',
      model: 'rule-based',
      latency_ms: 40,
      cost: 0,
      output_keys: ['qa_decision'],
      output_preview: '{"approved":false}',
    },
  ]);
  assert.equal(nodes[0].group, 'Writer');
  assert.equal(nodes[0].outKeys.length, 1);
  assert.equal(nodes[1].group, 'QA rails');
  assert.equal(nodes[1].score, 58);
  assert.equal(nodes[1].verdict, 'veto');
  assert.equal(nodes[2].status, 'halt');
});

test('normalizeNodes flags a rescue-loop re-run of the same atom', () => {
  const nodes = TraceH.normalizeNodes([
    {
      seq: 18,
      atom: 'qa.critic',
      node_id: 'qa.critic',
      status: 'ok',
      latency_ms: 9000,
      output_preview: '{"approved":true,"score":72}',
    },
    {
      seq: 31,
      atom: 'qa.critic',
      node_id: 'qa.critic',
      status: 'ok',
      latency_ms: 9000,
      output_preview: '{"approved":true,"score":84}',
    },
  ]);
  assert.equal(nodes[0].loop, false);
  assert.equal(nodes[1].loop, true);
});

test('parsePreview returns null on truncated / non-JSON previews', () => {
  assert.equal(TraceH.parsePreview('{"a":1}').a, 1);
  assert.equal(TraceH.parsePreview('{"a":1'), null); // truncated by the byte cap
  assert.equal(TraceH.parsePreview('plain text'), null);
  assert.equal(TraceH.parsePreview(''), null);
});

test('barPct is proportional and floored/guarded', () => {
  assert.equal(TraceH.barPct(50, 100), 50);
  assert.ok(TraceH.barPct(0, 100) >= 2); // floor so a 0ms node still shows a tick
  assert.ok(TraceH.barPct(1, 0) >= 2); // maxMs=0 never divides by zero
});

test('defaultRunId prefers the content run, else first, else null', () => {
  assert.equal(
    TraceH.defaultRunId([
      { run_id: 'R2', kind: 'content' },
      { run_id: 'R1', kind: 'retry' },
    ]),
    'R2'
  );
  assert.equal(TraceH.defaultRunId([{ run_id: 'M', kind: 'media' }]), 'M');
  assert.equal(TraceH.defaultRunId([]), null);
  assert.equal(TraceH.defaultRunId(null), null);
});

test('deriveHealth shows — for null KPIs (honest-empty, never a fake 0%)', () => {
  const h = TraceH.deriveHealth({
    tasks: 0,
    pass_rate: null,
    avg_quality: null,
    avg_cost_usd: null,
  });
  const byK = Object.fromEntries(h.map((x) => [x.k, x.v]));
  assert.equal(byK['pass rate'], '—');
  assert.equal(byK['avg quality'], '—');
  assert.equal(byK['avg cost'], '—');
});

test('mapRecentRun maps rejected→halt and computes wall time', () => {
  const r = TraceH.mapRecentRun({
    task_id: 'T',
    topic: 'x',
    status: 'rejected',
    started_at: '2026-06-08T14:00:00Z',
    completed_at: '2026-06-08T14:03:04Z',
  });
  assert.equal(r.status, 'halt');
  assert.equal(r.wall, '3m 04s');
  const ok = TraceH.mapRecentRun({
    task_id: 'T2',
    topic: 'y',
    status: 'published',
    started_at: '2026-06-08T14:00:00Z',
    completed_at: '2026-06-08T14:05:00Z',
  });
  assert.equal(ok.status, 'done');
});

test('parseCorpus splits name → url bullets, url-less lines survive', () => {
  const rows = TraceH.parseCorpus(
    '- Ollama docs → https://github.com/ollama/ollama\n- an internal note with no link'
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].u, 'https://github.com/ollama/ollama');
  assert.ok(rows[0].n.includes('Ollama docs'));
  assert.equal(rows[1].u, '');
});

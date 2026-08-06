'use strict';

// Contract tests for the Cofounder chat pure helpers
// (src/cofounder_agent/console/js/chat-helpers.js) — the NDJSON splitter and
// the event→view fold the thread renders from. Same vm harness as the api
// tests: the REAL file evaluated in a Node vm context, no DOM, no build.
//
// The fold contract matters doubly: replaying a turn's stream events must
// produce the same `parts` the server persists into chat_messages.parts
// (services/chat_agent.py), so the in-flight render and the after-reload
// render are identical. These tests pin that mapping event-by-event.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// vm-realm objects have foreign prototypes; strict deepEqual rejects them.
// Normalize through JSON before structural comparison.
const j = (x) => JSON.parse(JSON.stringify(x));

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'chat-helpers.js'),
  'utf8'
);

function load() {
  const sandbox = { console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'chat-helpers.js' });
  return sandbox.PXChat;
}

// ── splitNdjson ─────────────────────────────────────────────

test('splitNdjson: whole lines parse, trailing partial carries', () => {
  const PXChat = load();
  const r = PXChat.splitNdjson(
    '',
    '{"event":"turn_started","message_id":"m1"}\n{"event":"text","te'
  );
  assert.equal(r.events.length, 1);
  assert.equal(r.events[0].event, 'turn_started');
  assert.equal(r.rest, '{"event":"text","te');
});

test('splitNdjson: carry + next chunk completes the line', () => {
  const PXChat = load();
  const first = PXChat.splitNdjson('', '{"event":"text","te');
  const second = PXChat.splitNdjson(first.rest, 'xt":"hi"}\n');
  assert.equal(second.events.length, 1);
  assert.deepEqual(j(second.events[0]), { event: 'text', text: 'hi' });
  assert.equal(second.rest, '');
});

test('splitNdjson: CRLF and blank lines are tolerated', () => {
  const PXChat = load();
  const r = PXChat.splitNdjson(
    '',
    '{"event":"done"}\r\n\r\n{"event":"text","text":"x"}\n'
  );
  assert.equal(r.events.length, 2);
  assert.equal(r.events[0].event, 'done');
  assert.equal(r.events[1].text, 'x');
});

test('splitNdjson: a malformed line becomes _parse_error, not a throw', () => {
  const PXChat = load();
  const r = PXChat.splitNdjson('', '{not json}\n{"event":"done"}\n');
  assert.equal(r.events[0].event, '_parse_error');
  assert.equal(r.events[1].event, 'done');
});

test('splitNdjson: multiple events in one chunk', () => {
  const PXChat = load();
  const r = PXChat.splitNdjson(
    '',
    '{"event":"a"}\n{"event":"b"}\n{"event":"c"}\n'
  );
  assert.deepEqual(j(r.events.map((e) => e.event)), ['a', 'b', 'c']);
});

// ── foldEvent ───────────────────────────────────────────────

function foldAll(PXChat, events) {
  return events.reduce((v, e) => PXChat.foldEvent(v, e), PXChat.newTurnView());
}

test('fold: full happy turn produces server-shaped parts', () => {
  const PXChat = load();
  const v = foldAll(PXChat, [
    { event: 'turn_started', message_id: 'm9' },
    { event: 'tool_start', name: 'list_tasks', args_digest: '{"limit":3}' },
    {
      event: 'tool_result',
      name: 'list_tasks',
      ok: true,
      ms: 412,
      digest: '3 task(s)',
    },
    { event: 'task_linked', task_id: 't-1' },
    { event: 'text', text: 'done!' },
    {
      event: 'done',
      turn_status: 'complete',
      prompt_tokens: 10,
      completion_tokens: 5,
      cost_usd: 0.01,
    },
  ]);
  assert.equal(v.messageId, 'm9');
  assert.equal(v.status, 'complete');
  assert.equal(v.done, true);
  assert.equal(v.liveTool, null);
  assert.deepEqual(j(v.parts.map((p) => p.type)), [
    'tool_call',
    'card',
    'markdown',
  ]);
  assert.equal(v.parts[0].ok, true);
  assert.equal(v.parts[1].card.task_id, 't-1');
  assert.deepEqual(j(v.taskIds), ['t-1']);
  assert.equal(v.stats.toolCalls, 1);
  assert.equal(v.stats.toolErrors, 0);
  assert.equal(v.stats.promptTokens, 10);
  assert.equal(v.stats.costUsd, 0.01);
});

test('fold: tool_start sets liveTool; tool_result clears it', () => {
  const PXChat = load();
  const mid = foldAll(PXChat, [
    { event: 'turn_started', message_id: 'm' },
    { event: 'tool_start', name: 'get_budget', args_digest: '{}' },
  ]);
  assert.deepEqual(j(mid.liveTool), { name: 'get_budget', args_digest: '{}' });
  const after = PXChat.foldEvent(mid, {
    event: 'tool_result',
    name: 'get_budget',
    ok: false,
    ms: 20,
    digest: 'err',
  });
  assert.equal(after.liveTool, null);
  assert.equal(after.stats.toolErrors, 1);
});

test('fold: error + failed done → failed view with error rows', () => {
  const PXChat = load();
  const v = foldAll(PXChat, [
    { event: 'turn_started', message_id: 'm' },
    {
      event: 'error',
      reason: 'provider_no_tools',
      detail: 'route via litellm',
    },
    {
      event: 'done',
      turn_status: 'failed',
      prompt_tokens: 0,
      completion_tokens: 0,
      cost_usd: 0,
    },
  ]);
  assert.equal(v.status, 'failed');
  assert.equal(v.errors.length, 1);
  assert.match(v.errors[0].detail, /litellm/);
});

test('fold: interrupted done keeps its status', () => {
  const PXChat = load();
  const v = foldAll(PXChat, [
    { event: 'turn_started', message_id: 'm' },
    { event: 'error', reason: 'turn_timeout', detail: '120s deadline' },
    {
      event: 'done',
      turn_status: 'interrupted',
      prompt_tokens: 3,
      completion_tokens: 0,
      cost_usd: 0,
    },
  ]);
  assert.equal(v.status, 'interrupted');
});

test('fold: unknown event kinds are a forward-compatible no-op', () => {
  const PXChat = load();
  const base = foldAll(PXChat, [{ event: 'turn_started', message_id: 'm' }]);
  const v = PXChat.foldEvent(base, { event: 'plan_proposed', plan: 'x' });
  assert.deepEqual(j(v.parts), j(base.parts));
  assert.equal(v.status, 'streaming');
});

test('fold: approval_required renders the pending card (P3)', () => {
  const PXChat = load();
  const v = foldAll(PXChat, [
    { event: 'turn_started', message_id: 'm' },
    {
      event: 'approval_required',
      approval_id: 'appr-1',
      tool: 'set_setting',
      summary: 'set_setting {"key":"k"}',
    },
    { event: 'text', text: 'Awaiting your sign-off.' },
    { event: 'done', turn_status: 'complete' },
  ]);
  const card = v.parts.find((p) => p.type === 'card').card;
  assert.equal(card.kind, 'approval');
  assert.equal(card.approval_id, 'appr-1');
  assert.equal(card.state, 'pending');
  assert.equal(v.status, 'complete');
});

test('watchProgress: pct from expected nodes; indeterminate when unknown', () => {
  const PXChat = load();
  const p = PXChat.watchProgress({
    task_id: 't1',
    status: 'in_progress',
    terminal: false,
    topic: 'T',
    nodes_done: 12,
    expected_nodes: 31,
    nodes: [
      { node_id: 'a', status: 'success', duration_ms: 5 },
      { node_id: 'b', status: 'running', duration_ms: null },
    ],
  });
  assert.equal(p.pct, 39);
  assert.equal(p.current.node_id, 'b');
  assert.equal(p.tail.length, 2);
  const unknown = PXChat.watchProgress({
    task_id: 't2',
    status: 'in_progress',
    nodes_done: 3,
    expected_nodes: null,
    nodes: [],
  });
  assert.equal(unknown.pct, null);
  assert.equal(PXChat.watchProgress(null), null);
});

test('fold: does not mutate the previous view (state-safe for React)', () => {
  const PXChat = load();
  const a = foldAll(PXChat, [{ event: 'turn_started', message_id: 'm' }]);
  const before = JSON.stringify(a);
  PXChat.foldEvent(a, { event: 'text', text: 'hi' });
  assert.equal(JSON.stringify(a), before);
});

// ── sessionStats / statusMeta / suggestions ─────────────────

test('sessionStats: totals over assistant rows only', () => {
  const PXChat = load();
  const s = PXChat.sessionStats([
    { role: 'user', parts: [{ type: 'markdown', text: 'q' }] },
    {
      role: 'assistant',
      model: 'qwen2.5:7b',
      prompt_tokens: 100,
      completion_tokens: 20,
      cost_usd: 0.02,
      parts: [
        { type: 'tool_call', ok: true },
        { type: 'tool_call', ok: false },
        { type: 'markdown', text: 'a' },
      ],
    },
    {
      role: 'assistant',
      model: 'qwen2.5:7b',
      prompt_tokens: 50,
      completion_tokens: 10,
      cost_usd: 0,
      parts: [],
    },
  ]);
  assert.equal(s.turns, 2);
  assert.equal(s.toolCalls, 2);
  assert.equal(s.toolErrors, 1);
  assert.equal(s.promptTokens, 150);
  assert.equal(s.completionTokens, 30);
  assert.ok(Math.abs(s.costUsd - 0.02) < 1e-9);
  assert.equal(s.model, 'qwen2.5:7b');
});

test('statusMeta: terminal + running labels', () => {
  const PXChat = load();
  assert.deepEqual(j(PXChat.statusMeta('complete')), ['', '']);
  assert.deepEqual(j(PXChat.statusMeta('failed')), ['FAILED', 'err']);
  assert.deepEqual(j(PXChat.statusMeta('interrupted')), [
    'INTERRUPTED',
    'warn',
  ]);
  assert.deepEqual(j(PXChat.statusMeta('streaming')), ['RUNNING', 'run']);
});

test('suggestedPrompts: derived from the catalog, capped at 4', () => {
  const PXChat = load();
  const all = PXChat.suggestedPrompts([
    { name: 'list_tasks' },
    { name: 'get_budget' },
    { name: 'find_similar_posts' },
    { name: 'create_post' },
  ]);
  assert.equal(all.length, 4);
  const slim = PXChat.suggestedPrompts([{ name: 'list_tasks' }]);
  assert.equal(slim.length, 1);
  assert.match(slim[0], /pipeline/i);
  assert.deepEqual(j(PXChat.suggestedPrompts([])), []);
});

test('slashMatches: prefix filter, empty for non-slash input', () => {
  const PXChat = load();
  assert.equal(PXChat.slashMatches('hello').length, 0);
  assert.ok(PXChat.slashMatches('/').length >= 3);
  const m = PXChat.slashMatches('/cr');
  assert.equal(m.length, 1);
  assert.equal(m[0].cmd, '/create-post');
});

test('threadFingerprint: moves on new messages, status flips, and card stamps', () => {
  const PXChat = load();
  const base = {
    messages: [
      {
        id: 'm1',
        turn_status: 'complete',
        parts: [{ type: 'markdown', text: 'q' }],
      },
      {
        id: 'm2',
        turn_status: 'complete',
        parts: [{ type: 'card', card: { kind: 'approval', state: 'pending' } }],
      },
    ],
    task_links: [],
  };
  const f0 = PXChat.threadFingerprint(base);
  assert.equal(
    f0,
    PXChat.threadFingerprint(JSON.parse(JSON.stringify(base))),
    'identical threads must fingerprint identically'
  );
  const withSystem = JSON.parse(JSON.stringify(base));
  withSystem.messages.push({
    id: 'm3',
    turn_status: 'complete',
    parts: [{ type: 'markdown', text: 'Draft ready' }],
  });
  assert.notEqual(PXChat.threadFingerprint(withSystem), f0);
  const stamped = JSON.parse(JSON.stringify(base));
  stamped.messages[1].parts[0].card.state = 'approved';
  assert.notEqual(PXChat.threadFingerprint(stamped), f0);
  const repaired = JSON.parse(JSON.stringify(base));
  repaired.messages[1].turn_status = 'interrupted';
  assert.notEqual(PXChat.threadFingerprint(repaired), f0);
  assert.equal(PXChat.threadFingerprint(null), '');
});

test('expandSlash: self-contained commands send; argument-takers insert', () => {
  const PXChat = load();
  const brief = PXChat.expandSlash('/brief');
  assert.ok(brief.send && brief.send.includes('status briefing'));
  const bare = PXChat.expandSlash('/create-post');
  assert.ok(bare.insert && bare.insert.startsWith('Write a post about'));
  const withArgs = PXChat.expandSlash('/create-post edge caching patterns');
  assert.equal(withArgs.send, 'Write a post about edge caching patterns');
  assert.equal(PXChat.expandSlash('hello /brief'), null);
  assert.equal(PXChat.expandSlash('/unknown'), null);
  assert.equal(PXChat.expandSlash(''), null);
});

test('fold: generic card event renders the part (P4 plan cards)', () => {
  const PXChat = load();
  const v = foldAll(PXChat, [
    { event: 'turn_started', message_id: 'm' },
    {
      event: 'card',
      card: { kind: 'plan', plan_id: 'p1', state: 'draft', nodes: ['a'] },
    },
    { event: 'done', turn_status: 'complete' },
  ]);
  const card = v.parts.find((p) => p.type === 'card').card;
  assert.equal(card.kind, 'plan');
  assert.equal(card.plan_id, 'p1');
});

test('planBlocks: groups node ids into plain-language blocks, in order', () => {
  const PXChat = load();
  const blocks = PXChat.planBlocks([
    'verify_task',
    'content.generate_draft',
    'writer_self_review',
    'qa.web_factcheck',
    'qa.web_factcheck_2',
    'qa.aggregate',
    'seo.generate_all_metadata',
    'content.persist_task',
    'totally_novel_node',
  ]);
  const labels = j(blocks.map((b) => b.label));
  assert.deepEqual(labels, [
    'Verify',
    'Write & polish',
    'Quality checks',
    'SEO',
    'Finalize',
    'Other steps',
  ]);
  assert.equal(blocks.find((b) => b.label === 'Quality checks').count, 3);
  assert.deepEqual(j(PXChat.planBlocks([])), []);
});

// ── readGate — monotonic thread-read serialization ─────────────────
// Both semantics earned by live incidents: stale reads must not regress
// the pane, AND slow reads must still make forward progress when the
// worker is busy (the Run-button freeze).

test('readGate: out-of-order stale response is discarded', () => {
  const PXChat = load();
  const g = PXChat.readGate();
  const older = g.begin(false);
  const newer = g.begin(false);
  assert.equal(g.settle(newer), true); // newer read applies first
  assert.equal(g.settle(older), false); // stale arrival discarded
});

test('readGate: slow reads still apply when nothing newer applied (livelock fix)', () => {
  const PXChat = load();
  const g = PXChat.readGate();
  // Simulates reads slower than the poll cadence: each begins before the
  // previous settles. Under latest-started-wins every settle would be
  // discarded and the pane would freeze; monotonic apply keeps progress.
  const t1 = g.begin(false);
  const t2 = g.begin(false);
  assert.equal(g.settle(t1), true); // slow but nothing newer applied yet
  assert.equal(g.settle(t2), true); // and the newer one still applies after
});

test('readGate: background ticks are skipped while a read is in flight', () => {
  const PXChat = load();
  const g = PXChat.readGate();
  const t1 = g.begin(true);
  assert.ok(t1 > 0);
  assert.equal(g.begin(true), 0); // tick dropped — no convoy on a busy worker
  g.settle(t1);
  assert.ok(g.begin(true) > 0); // next tick fetches again
});

test('readGate: action reads are never skipped', () => {
  const PXChat = load();
  const g = PXChat.readGate();
  const poll = g.begin(true);
  const action = g.begin(false); // Run/approve refresh during an in-flight poll
  assert.ok(action > 0);
  assert.equal(g.settle(action), true);
  assert.equal(g.settle(poll), false); // the older poll read is now stale
});

test('readGate: failed reads release the in-flight slot without applying', () => {
  const PXChat = load();
  const g = PXChat.readGate();
  const t1 = g.begin(true);
  g.fail(t1);
  const t2 = g.begin(true); // not skipped — the failed read released it
  assert.ok(t2 > 0);
  assert.equal(g.settle(t2), true);
});

test('readGate: reset marks all in-flight reads stale (conversation switch)', () => {
  const PXChat = load();
  const g = PXChat.readGate();
  const oldConv = g.begin(false);
  g.reset();
  const newConv = g.begin(false);
  assert.equal(g.settle(oldConv), false); // old conversation's read discarded
  assert.equal(g.settle(newConv), true);
});

// ── planWarnings (absence chips, poindexter#950 follow-up) ──────────

test('planWarnings: bare writer plan flags qa / fact-check / images', () => {
  const PXChat = load();
  assert.deepEqual(j(PXChat.planWarnings(['gen_draft', 'compile_meta'])), [
    'no quality checks',
    'no fact-check',
    'no images',
  ]);
});

test('planWarnings: full canonical-style plan warns nothing', () => {
  const PXChat = load();
  assert.deepEqual(
    j(
      PXChat.planWarnings([
        'gen_draft',
        'qa_web_factcheck',
        'generate_images',
        'persist_task',
      ])
    ),
    []
  );
});

test('planWarnings: qa presence clears quality but not fact-check', () => {
  const PXChat = load();
  assert.deepEqual(
    j(PXChat.planWarnings(['draft', 'qa_critic', 'hero_image'])),
    ['no fact-check']
  );
});

test('planWarnings: auto-added terminal shows as reassurance', () => {
  const PXChat = load();
  assert.deepEqual(
    j(
      PXChat.planWarnings([
        'draft',
        'qa_rails',
        'fact_check',
        'inject_images',
        'ensure_terminal_status',
      ])
    ),
    ['auto-added: lands in your approval queue']
  );
});

test('planWarnings: empty node list flags everything absent', () => {
  const PXChat = load();
  assert.deepEqual(j(PXChat.planWarnings([])), [
    'no quality checks',
    'no fact-check',
    'no images',
  ]);
});

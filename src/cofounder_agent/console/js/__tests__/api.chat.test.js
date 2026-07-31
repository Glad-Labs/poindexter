'use strict';

// Contract tests for PX.api's Cofounder chat surface (poindexter#948):
// the NDJSON-streaming live send, and the scripted mock send that must stay
// behaviourally identical (same event kinds through the same onEvent path,
// exchange persisted into the mock thread). Same vm harness as
// api.token.test.js — the REAL api.js + chat-helpers.js evaluated together,
// no DOM, no build step.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// vm-realm objects have foreign prototypes; strict deepEqual rejects them.
// Normalize through JSON before structural comparison.
const j = (x) => JSON.parse(JSON.stringify(x));

const API_SRC = fs.readFileSync(path.join(__dirname, '..', 'api.js'), 'utf8');
const HELPERS_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'chat-helpers.js'),
  'utf8'
);

function res(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'ERR',
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  };
}

// A streamable OK response whose body reader yields `chunks` (strings) in
// order — the shape httpStream() consumes.
function streamRes(chunks) {
  let i = 0;
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => chunks.join(''),
    body: {
      getReader() {
        return {
          read: async () =>
            i < chunks.length
              ? { value: chunks[i++], done: false }
              : { value: undefined, done: true },
          cancel() {},
        };
      },
    },
  };
}

function makeLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  };
}

// Minimal chatMock mirroring data.js's shape (2 tools, 1 conversation).
function makeChatMock() {
  return {
    persona: 'Poindexter',
    tools: [
      { name: 'list_tasks', description: 'd', tier: 'read' },
      { name: 'create_post', description: 'd', tier: 'write' },
    ],
    conversations: [
      {
        id: 'c1',
        title: '',
        brain: 'local',
        status: 'active',
        message_count: 0,
        created_at: '2026-07-31T00:00:00Z',
        last_message_at: '2026-07-31T00:00:00Z',
      },
    ],
    threads: { c1: [] },
    scriptFor() {
      return [
        [0, { event: 'turn_started', message_id: 'mock-m1' }],
        [0, { event: 'tool_start', name: 'list_tasks', args_digest: '{}' }],
        [
          0,
          {
            event: 'tool_result',
            name: 'list_tasks',
            ok: true,
            ms: 5,
            digest: '1 task',
          },
        ],
        [0, { event: 'text', text: 'one task in flight' }],
        [
          0,
          {
            event: 'done',
            turn_status: 'complete',
            prompt_tokens: 9,
            completion_tokens: 4,
            cost_usd: 0,
          },
        ],
      ];
    },
  };
}

function makeAdapter({ live, apiHandler } = {}) {
  const calls = { token: 0, api: 0, urls: [], opts: [] };
  const fetchStub = (url, opts) => {
    const u = String(url);
    if (u.endsWith('/token')) {
      calls.token += 1;
      return Promise.resolve(res({ access_token: 'jwt-1', expires_in: 3600 }));
    }
    calls.api += 1;
    calls.urls.push(u);
    calls.opts.push(opts);
    return Promise.resolve(apiHandler ? apiHandler(u, opts) : res({}));
  };
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController,
    performance,
    Date,
    fetch: fetchStub,
    PX_API_LIVE: !!live,
    PX: { chatMock: makeChatMock() },
  };
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage();
  vm.createContext(sandbox);
  vm.runInContext(HELPERS_SRC, sandbox, { filename: 'chat-helpers.js' });
  vm.runInContext(API_SRC, sandbox, { filename: 'api.js' });
  const api = sandbox.PX.api;
  if (live) {
    api.setClient('cid', 'secret');
    api.setLive(true);
  } else {
    api.setLive(false);
    api.setSim('normal');
  }
  return { api, calls, sandbox };
}

// ── mock branch ─────────────────────────────────────────────

test('mock: chatSend streams the scripted events through onEvent in order', async () => {
  const { api } = makeAdapter({ live: false });
  const kinds = [];
  await api.chatSend('c1', 'status?', (ev) => kinds.push(ev.event));
  assert.deepEqual(j(kinds), [
    'turn_started',
    'tool_start',
    'tool_result',
    'text',
    'done',
  ]);
});

test('mock: chatSend persists the exchange with server-shaped parts', async () => {
  const { api } = makeAdapter({ live: false });
  await api.chatSend('c1', 'status?', () => {});
  const t = await api.chatGet('c1');
  assert.equal(t.messages.length, 2);
  const [user, agent] = t.messages;
  assert.equal(user.role, 'user');
  assert.deepEqual(j(user.parts), [{ type: 'markdown', text: 'status?' }]);
  assert.equal(agent.role, 'assistant');
  assert.equal(agent.turn_status, 'complete');
  assert.deepEqual(j(agent.parts.map((p) => p.type)), [
    'tool_call',
    'markdown',
  ]);
  assert.equal(agent.prompt_tokens, 9);
  // Conversation metadata follows the turn (title-if-empty + counts).
  const list = await api.chatList();
  assert.equal(list.conversations[0].message_count, 2);
  assert.equal(list.conversations[0].title, 'status?');
});

test('mock: sim=error fails the turn honestly (failed status, error event)', async () => {
  const { api } = makeAdapter({ live: false });
  api.setSim('error');
  const events = [];
  await api.chatSend('c1', 'x', (ev) => events.push(ev));
  const done = events.find((e) => e.event === 'done');
  assert.equal(done.turn_status, 'failed');
  assert.ok(events.some((e) => e.event === 'error'));
  // chatGet also rides the sim switch; reset so the readback itself works.
  api.setSim('normal');
  const t = await api.chatGet('c1');
  assert.equal(t.messages[1].turn_status, 'failed');
});

test('mock: chatCreate + chatArchive maintain the list', async () => {
  const { api } = makeAdapter({ live: false });
  const conv = await api.chatCreate('My thread');
  assert.equal(conv.title, 'My thread');
  let list = await api.chatList();
  assert.equal(list.count, 2);
  await api.chatArchive(conv.id);
  list = await api.chatList();
  assert.equal(list.count, 1);
  const archived = await api.chatList('archived');
  assert.equal(archived.count, 1);
});

test('mock: chatTools returns persona + catalog', async () => {
  const { api } = makeAdapter({ live: false });
  const c = await api.chatTools();
  assert.equal(c.persona, 'Poindexter');
  assert.equal(c.tools.length, 2);
});

// ── live branch ─────────────────────────────────────────────

test('live: chatSend reads the NDJSON stream, reassembling split lines', async () => {
  const { api, calls } = makeAdapter({
    live: true,
    apiHandler: (u) => {
      if (u.includes('/messages'))
        return streamRes([
          '{"event":"turn_started","message_id":"m1"}\n{"event":"te',
          'xt","text":"hello"}\n{"event":"done","turn_status":"comp',
          'lete","prompt_tokens":1,"completion_tokens":2,"cost_usd":0}\n',
        ]);
      return res({});
    },
  });
  const events = [];
  await api.chatSend('conv-9', 'hi', (ev) => events.push(ev));
  assert.deepEqual(j(events.map((e) => e.event)), [
    'turn_started',
    'text',
    'done',
  ]);
  assert.equal(events[1].text, 'hello');
  const post = calls.opts[calls.urls.findIndex((u) => u.includes('/messages'))];
  assert.equal(post.method, 'POST');
  assert.match(post.headers.Authorization, /^Bearer jwt-/);
  assert.deepEqual(j(JSON.parse(post.body)), { text: 'hi' });
});

test('live: a final line without trailing newline still emits', async () => {
  const { api } = makeAdapter({
    live: true,
    apiHandler: () =>
      streamRes([
        '{"event":"turn_started","message_id":"m"}\n{"event":"done","turn_status":"complete"}',
      ]),
  });
  const events = [];
  await api.chatSend('c', 'x', (ev) => events.push(ev));
  assert.deepEqual(j(events.map((e) => e.event)), ['turn_started', 'done']);
});

test('live: a 403 (surface disabled) rejects with the server detail', async () => {
  const { api } = makeAdapter({
    live: true,
    apiHandler: () =>
      res(
        {
          detail:
            'The Cofounder chat surface is disabled. Enable it with `poindexter settings set console_chat_enabled true`',
        },
        403
      ),
  });
  await assert.rejects(
    () => api.chatSend('c', 'x', () => {}),
    /console_chat_enabled/
  );
});

test('live: chatList/chatGet hit the expected endpoints', async () => {
  const { api, calls } = makeAdapter({
    live: true,
    apiHandler: (u) => {
      if (u.includes('/api/chat/conversations?status=active'))
        return res({ conversations: [], count: 0 });
      if (u.match(/\/api\/chat\/conversations\/abc$/))
        return res({
          conversation: { id: 'abc' },
          messages: [],
          task_links: [],
        });
      return res({});
    },
  });
  await api.chatList();
  await api.chatGet('abc');
  assert.ok(
    calls.urls.some((u) => u.includes('/api/chat/conversations?status=active'))
  );
  assert.ok(calls.urls.some((u) => u.endsWith('/api/chat/conversations/abc')));
});

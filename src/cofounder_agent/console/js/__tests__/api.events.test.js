'use strict';

// Wire + mapping tests for PX.api.pipelineEvents() — the EVENT STREAM feed.
//
// The live branch GETs /api/pipeline/events and maps each flattened
// audit_log row through eventToFeedLine. Every MODERN graph_def event type
// (post atom-cutover #355: qa_pass_completed, qa_rescue_scheduled,
// template_completed, auto_publish_gate, …) must get a typed feed line
// (tone + label + html), not fall through to the generic EVENT branch —
// the feed starved from 2026-06-01 until these types were wired because
// only the legacy pre-#355 names were mapped. Same vm harness as
// api.http.test.js (node:test + vm realm, no jsdom).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'api.js'), 'utf8');

function makeLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  };
}

// Build a live-mode api whose data GETs return {count, events, server_time}.
// Returns {api, calls} — calls records every non-token URL fetched.
function makeApi(events) {
  const calls = [];
  const fetchStub = (url, _opts) => {
    if (String(url).endsWith('/token')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ access_token: 'jwt', expires_in: 3600 }),
      });
    }
    calls.push(String(url));
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        count: events.length,
        events,
        server_time: '2026-07-25T14:00:00+00:00',
      }),
    });
  };

  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController,
    performance,
    fetch: fetchStub,
    PX_API_LIVE: true,
  };
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage();
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);

  const api = sandbox.PX.api;
  api.setClient('cid', 'secret');
  api.setLive(true);
  return { api, calls };
}

const TS = '2026-07-25T14:03:22+00:00';
const TASK = 'ece2f516-1111-2222-3333-444455556666';

function ev(id, event_type, details, severity) {
  return {
    id,
    timestamp: TS,
    event_type,
    source: 'test',
    task_id: TASK,
    severity: severity || 'info',
    details,
  };
}

test('pipelineEvents() maps every modern graph_def type to a typed feed line', async () => {
  const events = [
    ev(1, 'qa_pass_completed', {
      approved: true,
      final_score: 86.4,
      approval_threshold: 80,
      reviewer_count: 9,
      reviews: [
        { reviewer: 'programmatic_validator', approved: true, score: 100 },
        { reviewer: 'ollama_critic', approved: true, score: 82 },
      ],
      rescued: true,
    }),
    ev(2, 'qa_pass_completed', {
      approved: false,
      final_score: 61,
      reviewer_count: 9,
      reviews: [],
    }),
    ev(3, 'qa_rescue_scheduled', {
      attempt: 2,
      max_attempts: 2,
      threshold: 80.0,
      vetoed_by: ['programmatic_validator', 'ollama_critic'],
      final_score: 65.0,
    }),
    ev(
      4,
      'qa_flagged_surfaced',
      {
        attempts: 2,
        threshold: 80.0,
        vetoed_by: ['ollama_critic'],
        final_score: 68.12,
      },
      'warning'
    ),
    ev(5, 'writer_self_review_pass', {
      enabled: true,
      revised: false,
      contradictions_found: 0,
    }),
    ev(6, 'writer_self_review_pass', {
      enabled: true,
      revised: true,
      contradictions_found: 2,
    }),
    ev(7, 'ragas_score', {
      score: 0.6492,
      faithfulness: 0.7727,
      answer_relevancy: 0.675,
      context_precision: 0.5,
    }),
    ev(8, 'image_style_picked', {
      style: 'dark glassmorphism UI design',
      topic: 'The startup Postgres survival guide',
    }),
    ev(
      9,
      'image_ocr_gate_result',
      {
        model: 'z_image_turbo',
        passed: false,
        attempts: 3,
        threshold: 6,
        text_chars: 10,
      },
      'warning'
    ),
    ev(10, 'video_shot_rendered', {
      rung: 'primary',
      source: 'image_kenburns',
      success: true,
      qa_score: 98.0,
      shot_idx: 7,
      duration_s: 5.0,
      qa_outcome: 'accepted',
    }),
    ev(11, 'video_shot_rendered', {
      rung: 'primary',
      source: 'image_gen',
      success: false,
      shot_idx: 2,
      error: 'render timeout',
    }),
    ev(12, 'template_completed', {
      ok: true,
      records: ['stage.verify_task', 'content.generate_draft', 'qa.aggregate'],
    }),
    ev(13, 'auto_publish_gate', {
      would_fire: false,
      gate_state: 'block_qa_flagged',
      dry_run: true,
      quality_score: 75.0,
    }),
    ev(14, 'auto_publish_gate', {
      would_fire: true,
      gate_state: 'fired',
      dry_run: false,
    }),
    ev(15, 'approval_gate_paused', {
      gate_name: 'seo_refresh_gate',
      paused_at: TS,
    }),
    ev(16, 'approval_gate_approved', {
      gate_name: 'seo_refresh_gate',
      previous_status: 'awaiting_gate',
    }),
    ev(17, 'task_started', {
      topic: 'The startup Postgres survival guide',
      template_slug: 'canonical_blog',
    }),
    ev(18, 'some_future_event_type', { anything: 1 }, 'warning'),
  ];

  const { api, calls } = makeApi(events);
  const lines = await api.pipelineEvents();

  assert.equal(calls.length, 1, 'one data GET');
  assert.match(
    calls[0],
    /\/api\/pipeline\/events\?limit=50&since_minutes=120$/,
    'polls the pipeline events endpoint with the documented window'
  );

  assert.equal(lines.length, events.length, 'one feed line per event');
  const by = new Map(lines.map((l) => [l.id, l]));
  // vm-realm arrays have a foreign Array prototype, so deepEqual rejects
  // them; compare the [tone,label] pair as a joined string instead.
  const tagOf = (l) => Array.from(l.tag).join('|');

  // Every line carries the feed-line contract: id (dedup), ts, [tone,label], html.
  for (const l of lines) {
    assert.ok(
      Array.isArray(l.tag) && l.tag.length === 2,
      'tag is [tone,label]'
    );
    assert.equal(typeof l.html, 'string');
    assert.match(l.ts, /^\d\d:\d\d:\d\d$/);
  }

  // qa_pass_completed — approve vs reject tones, score, reviewer count, rescue.
  assert.equal(tagOf(by.get(1)), 'mint|QA');
  assert.match(by.get(1).html, /APPROVED/);
  assert.match(by.get(1).html, /86<\/b>\/100/);
  assert.match(by.get(1).html, /2 reviewers/); // reviews[] length wins
  assert.match(by.get(1).html, /rescued/);
  assert.equal(tagOf(by.get(2)), 'red|QA');
  assert.match(by.get(2).html, /REJECTED/);
  assert.match(by.get(2).html, /9 reviewers/); // falls back to reviewer_count

  // qa_rescue_scheduled — amber rewrite line with veto detail.
  assert.equal(tagOf(by.get(3)), 'amber|REWRITE');
  assert.match(by.get(3).html, /rescue attempt <b>2<\/b>\/2/);
  assert.match(
    by.get(3).html,
    /vetoed by programmatic_validator, ollama_critic/
  );

  // qa_flagged_surfaced — flagged post riding through to operator review.
  assert.equal(tagOf(by.get(4)), 'amber|QA');
  assert.match(by.get(4).html, /flagged for operator review/);

  // writer_self_review_pass — clean vs revised.
  assert.equal(tagOf(by.get(5)), 'cyan|WRITER');
  assert.match(by.get(5).html, /clean/);
  assert.equal(tagOf(by.get(6)), 'amber|WRITER');
  assert.match(by.get(6).html, /revised/);
  assert.match(by.get(6).html, /2 contradictions/);

  // ragas_score — 2dp rounding.
  assert.equal(tagOf(by.get(7)), 'cyan|QA');
  assert.match(by.get(7).html, /ragas <b>0\.65<\/b>/);
  assert.match(by.get(7).html, /faith 0\.77/);

  // image_style_picked / image_ocr_gate_result.
  assert.equal(tagOf(by.get(8)), 'cyan|IMAGE');
  assert.match(by.get(8).html, /dark glassmorphism UI design/);
  assert.equal(tagOf(by.get(9)), 'amber|IMAGE');
  assert.match(by.get(9).html, /OCR gate/);
  assert.match(by.get(9).html, /FAIL/);
  assert.match(by.get(9).html, /3 attempt/);

  // video_shot_rendered — success vs failure.
  assert.equal(tagOf(by.get(10)), 'mint|VIDEO');
  assert.match(by.get(10).html, /shot <b>7<\/b>/);
  assert.match(by.get(10).html, /accepted/);
  assert.match(by.get(10).html, /image_kenburns/);
  assert.equal(tagOf(by.get(11)), 'red|VIDEO');
  assert.match(by.get(11).html, /failed/);

  // template_completed — node count.
  assert.equal(tagOf(by.get(12)), 'mint|PIPELINE');
  assert.match(by.get(12).html, /complete/);
  assert.match(by.get(12).html, /3 nodes/);

  // auto_publish_gate — holds vs fires, dry-run marker.
  assert.equal(tagOf(by.get(13)), 'cyan|PUBLISH');
  assert.match(by.get(13).html, /holds/);
  assert.match(by.get(13).html, /block_qa_flagged/);
  assert.match(by.get(13).html, /dry-run/);
  assert.equal(tagOf(by.get(14)), 'mint|PUBLISH');
  assert.match(by.get(14).html, /fires/);

  // approval gates — the HITL pause/resume lifecycle.
  assert.equal(tagOf(by.get(15)), 'amber|GATE');
  assert.match(by.get(15).html, /seo_refresh_gate/);
  assert.match(by.get(15).html, /needs you/);
  assert.equal(tagOf(by.get(16)), 'mint|GATE');
  assert.match(by.get(16).html, /approved/);

  // Legacy task lifecycle still maps (not regressed by the modern cases).
  assert.equal(tagOf(by.get(17)), 'cyan|TASK');
  assert.match(by.get(17).html, /started/);

  // Unknown types fall through to the generic EVENT line, toned by severity —
  // this is what makes a pipeline_event_stream_types CSV addition render
  // without a console deploy.
  assert.equal(tagOf(by.get(18)), 'amber|EVENT');
  assert.match(by.get(18).html, /some_future_event_type/);
});

test('pipelineEvents() escapes LLM-derived detail text (XSS)', async () => {
  const probe = '<img src=x onerror=alert(1)>';
  const events = [
    ev(1, 'image_style_picked', { style: probe }),
    ev(2, 'qa_rescue_scheduled', {
      attempt: 1,
      max_attempts: 2,
      vetoed_by: [probe],
      final_score: 10,
    }),
    ev(3, 'approval_gate_paused', { gate_name: probe }),
  ];
  const { api } = makeApi(events);
  const lines = await api.pipelineEvents();
  for (const l of lines) {
    assert.ok(!l.html.includes('<img'), 'raw markup must not survive');
    assert.ok(l.html.includes('&lt;img'), 'markup arrives escaped');
  }
});

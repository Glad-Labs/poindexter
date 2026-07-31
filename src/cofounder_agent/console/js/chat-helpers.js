/* ══════════════════════════════════════════════════════════════
   Cofounder chat — pure helpers (window.PXChat)
   ──────────────────────────────────────────────────────────────
   Plain-JS, no React: the NDJSON stream splitter, the event→view
   fold, and the small read-model mappers cofounder.jsx renders.
   Kept out of the JSX (kpis.js / trace-helpers.js pattern) so the
   node:test + vm suite exercises the REAL logic with no DOM.

   The fold mirrors what services/chat_agent.py persists: replaying
   a turn's stream events client-side must produce the same `parts`
   the server finalizes into chat_messages.parts — that is what lets
   the in-flight render and the after-reload render look identical.
   ══════════════════════════════════════════════════════════════ */
(function () {
  // ── NDJSON incremental splitter ─────────────────────────────
  // Feed chunks as they arrive; returns parsed events + the trailing
  // partial line to carry into the next call. Tolerates CRLF and blank
  // lines. A malformed line becomes {event:'_parse_error', raw} instead
  // of throwing — one bad line must not kill the stream.
  function splitNdjson(carry, chunk) {
    const buf = (carry || '') + (chunk || '');
    const lines = buf.split('\n');
    const rest = lines.pop(); // '' when chunk ended in \n
    const events = [];
    for (const line of lines) {
      const t = line.replace(/\r$/, '').trim();
      if (!t) continue;
      try {
        events.push(JSON.parse(t));
      } catch (e) {
        events.push({ event: '_parse_error', raw: t.slice(0, 200) });
      }
    }
    return { events, rest };
  }

  // ── Stream fold: events → in-flight assistant view ──────────
  // State shape matches what the thread renders for a persisted row:
  //   { parts:[…chat_messages.parts shapes…], status, liveTool,
  //     stats:{toolCalls, toolErrors, promptTokens, completionTokens,
  //            costUsd}, errors:[…], done }
  function newTurnView() {
    return {
      parts: [],
      status: 'streaming',
      liveTool: null,
      taskIds: [],
      errors: [],
      stats: {
        toolCalls: 0,
        toolErrors: 0,
        promptTokens: 0,
        completionTokens: 0,
        costUsd: 0,
      },
      done: false,
      messageId: null,
    };
  }

  function foldEvent(view, ev) {
    const v = {
      ...view,
      parts: view.parts.slice(),
      taskIds: view.taskIds.slice(),
      errors: view.errors.slice(),
      stats: { ...view.stats },
    };
    switch (ev.event) {
      case 'turn_started':
        v.messageId = ev.message_id || null;
        v.status = 'streaming';
        break;
      case 'tool_start':
        v.liveTool = { name: ev.name, args_digest: ev.args_digest || '' };
        break;
      case 'tool_result':
        v.liveTool = null;
        v.stats.toolCalls += 1;
        if (!ev.ok) v.stats.toolErrors += 1;
        // Same shape the server persists (args_digest arrives on the
        // tool_start; the chip shows name/ok/ms/digest either way).
        v.parts.push({
          type: 'tool_call',
          name: ev.name,
          ok: !!ev.ok,
          ms: ev.ms || 0,
          result_digest: ev.digest || '',
        });
        break;
      case 'task_linked':
        v.taskIds.push(ev.task_id);
        v.parts.push({
          type: 'card',
          card: { kind: 'task_link', task_id: ev.task_id },
        });
        break;
      case 'text':
        v.parts.push({ type: 'markdown', text: ev.text || '' });
        break;
      case 'error':
        v.errors.push({
          reason: ev.reason || 'error',
          detail: ev.detail || '',
        });
        break;
      case 'done':
        v.done = true;
        v.liveTool = null;
        v.status = ev.turn_status || 'complete';
        v.stats.promptTokens = ev.prompt_tokens || 0;
        v.stats.completionTokens = ev.completion_tokens || 0;
        v.stats.costUsd = Number(ev.cost_usd || 0);
        break;
      case '_parse_error':
        v.errors.push({ reason: 'parse', detail: ev.raw || '' });
        break;
      default:
        // Unknown event type: forward-compatible no-op (a P3+ server may
        // emit new kinds; old consoles must not break).
        break;
    }
    return v;
  }

  // ── Session totals for the rail (over persisted messages) ───
  function sessionStats(messages) {
    const s = {
      turns: 0,
      toolCalls: 0,
      toolErrors: 0,
      promptTokens: 0,
      completionTokens: 0,
      costUsd: 0,
      model: '',
    };
    for (const m of messages || []) {
      if (m.role !== 'assistant') continue;
      s.turns += 1;
      s.promptTokens += m.prompt_tokens || 0;
      s.completionTokens += m.completion_tokens || 0;
      s.costUsd += Number(m.cost_usd || 0);
      if (m.model) s.model = m.model;
      for (const p of m.parts || []) {
        if (p.type === 'tool_call') {
          s.toolCalls += 1;
          if (!p.ok) s.toolErrors += 1;
        }
      }
    }
    return s;
  }

  // Turn-status → chip label/class. 'streaming' rows older than the
  // repair threshold are flipped server-side on read; a fresh one here
  // means a turn is genuinely running elsewhere (another tab).
  const STATUS_META = {
    streaming: ['RUNNING', 'run'],
    pending: ['QUEUED', 'run'],
    complete: ['', ''],
    failed: ['FAILED', 'err'],
    interrupted: ['INTERRUPTED', 'warn'],
  };
  function statusMeta(turnStatus) {
    return STATUS_META[turnStatus] || ['', ''];
  }

  // Suggested starter prompts for the empty state. Derived from which
  // tools the catalog actually reports, so a slimmed-down install never
  // suggests something its agent can't do.
  function suggestedPrompts(tools) {
    const names = new Set((tools || []).map((t) => t.name));
    const out = [];
    if (names.has('list_tasks')) out.push("What's in the pipeline right now?");
    if (names.has('get_budget')) out.push('How much have we spent this month?');
    if (names.has('find_similar_posts'))
      out.push('Have we written about local-first analytics?');
    if (names.has('create_post'))
      out.push('Write a post about self-hosted observability');
    return out.slice(0, 4);
  }

  // Slash shortcuts the composer expands inline (P2: canned intents; the
  // architect's /plan lands with P4 and simply maps to prose until then).
  const SLASH = [
    {
      cmd: '/brief',
      text: 'Give me a quick status briefing: pipeline, budget, anything failing.',
    },
    { cmd: '/create-post', text: 'Write a post about ' },
    { cmd: '/coverage', text: 'Have we already written about ' },
  ];
  function slashMatches(input) {
    if (!input || input[0] !== '/') return [];
    const q = input.toLowerCase();
    return SLASH.filter((s) => s.cmd.startsWith(q));
  }

  window.PXChat = {
    splitNdjson,
    newTurnView,
    foldEvent,
    sessionStats,
    statusMeta,
    suggestedPrompts,
    slashMatches,
    SLASH,
  };
})();

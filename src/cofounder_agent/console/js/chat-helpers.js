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
      case 'card':
        // Generic rich card from a tool (P4: the architect's plan card).
        // Mirrors the part the server persists on the turn.
        if (ev.card) v.parts.push({ type: 'card', card: ev.card });
        break;
      case 'approval_required':
        // Mirrors the pending card part the server persists on the turn
        // (P3 poindexter#949) so the live render matches the reload.
        v.parts.push({
          type: 'card',
          card: {
            kind: 'approval',
            approval_id: ev.approval_id,
            tool: ev.tool,
            summary: ev.summary || '',
            state: 'pending',
          },
        });
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

  // Enter-time slash expansion. The first live session sent '/brief' as a
  // LITERAL message (the menu only expanded on click). Rules:
  //   '/brief'                → {send: <template>}       (self-contained)
  //   '/create-post'          → {insert: <template>}     (needs an argument
  //                             — trailing-space templates must not send bare)
  //   '/create-post edge x'   → {send: <template + args>}
  //   anything else           → null (send literally, as typed)
  function expandSlash(input) {
    const t = (input || '').trim();
    if (!t || t[0] !== '/') return null;
    for (const s of SLASH) {
      if (t.toLowerCase() === s.cmd) {
        return s.text.endsWith(' ')
          ? { insert: s.text }
          : { send: s.text.trim() };
      }
      if (t.toLowerCase().startsWith(s.cmd + ' ')) {
        const args = t.slice(s.cmd.length + 1).trim();
        const joiner = s.text.endsWith(' ') ? '' : ' ';
        return { send: (s.text + joiner + args).trim() };
      }
    }
    return null;
  }

  // Cheap change-detection fingerprint for a loaded thread. The open
  // thread refresh-polls (watcher completion messages land server-side
  // with no push channel), and re-setting identical state every poll
  // would yank the reader's scroll to the bottom — so the poll only
  // applies a fetch whose fingerprint moved. Approval-card state rides
  // message parts, hence the per-message status+parts-length walk.
  function threadFingerprint(thread) {
    if (!thread) return '';
    const msgs = thread.messages || [];
    const per = msgs
      .map(
        (m) =>
          `${m.id}:${m.turn_status}:${(m.parts || []).length}:${(m.parts || [])
            .map((p) => (p.card && p.card.state) || '')
            .join('')}`
      )
      .join('|');
    return `${msgs.length}#${per}#${(thread.task_links || []).length}`;
  }

  // Plan-card node ids → plain-language step blocks (P4). Grouped by
  // recognizable prefixes so a non-technical reader sees "Write ·
  // Quality checks ×13 · SEO" while the technical toggle shows raw node
  // ids. Unknown prefixes fall into "Other steps" rather than vanishing.
  const _BLOCK_RULES = [
    [/^verify/, 'Verify'],
    [/research|topic/, 'Research'],
    [
      /generate_draft|writer|two_pass|narrate|self_review|reconcile|normalize|title|affiliate|link/,
      'Write & polish',
    ],
    [/image|caption/, 'Images'],
    [/^qa[._]|quality|url_validation/, 'Quality checks'],
    [/^seo/, 'SEO'],
    [/media|video|audio|podcast|shot_list|capture_training/, 'Media'],
    [/social/, 'Social'],
    [/approval_gate|gate/, 'Your approval gate'],
    [
      /persist|compile|finalize|record_pipeline|evaluate_auto_publish/,
      'Finalize',
    ],
  ];
  function planBlocks(nodes) {
    const blocks = [];
    const byLabel = {};
    for (const raw of nodes || []) {
      const id = String(raw).toLowerCase();
      let label = 'Other steps';
      for (const [re, l] of _BLOCK_RULES) {
        if (re.test(id)) {
          label = l;
          break;
        }
      }
      if (!byLabel[label]) {
        byLabel[label] = { label, count: 0 };
        blocks.push(byLabel[label]);
      }
      byLabel[label].count += 1;
    }
    return blocks;
  }

  // Absence warnings for a plan card, derived from node ids the same way
  // planBlocks derives presence. The first live batch shipped a plan whose
  // closing text CLAIMED fact-checking while the graph had no such node —
  // the card must make what's MISSING visible before the operator presses
  // Run. Honest over clever: a podcast plan will show "no images", and
  // that is fine — these are informative amber chips, not blockers.
  const _WARN_RULES = [
    [/qa|quality|critic|review/, 'no quality checks'],
    [/fact/, 'no fact-check'],
    [/image|hero|visual/, 'no images'],
  ];

  function planWarnings(nodes) {
    const ids = (nodes || []).map((n) => String(n).toLowerCase());
    const warnings = [];
    for (const [re, warn] of _WARN_RULES) {
      if (!ids.some((id) => re.test(id))) warnings.push(warn);
    }
    // The auto-added terminal is a reassurance, not a warning: the plan
    // had no landing state of its own and the system supplied one.
    if (ids.some((id) => id === 'ensure_terminal_status'))
      warnings.push('auto-added: lands in your approval queue');
    return warnings;
  }

  // Watched-run snapshot (GET /api/chat/watch/{id}) → rail progress view.
  // pct is null when the graph's expected node count is unknown (dev_diary /
  // capture disabled) — the bar renders indeterminate, never fabricated.
  function watchProgress(snapshot) {
    if (!snapshot) return null;
    const done = snapshot.nodes_done || 0;
    const expected = snapshot.expected_nodes || null;
    const pct =
      expected && expected > 0
        ? Math.min(100, Math.round((done / expected) * 100))
        : null;
    const nodes = snapshot.nodes || [];
    const running = nodes.filter((n) => n.status === 'running');
    return {
      taskId: snapshot.task_id,
      status: snapshot.status,
      terminal: !!snapshot.terminal,
      topic: snapshot.topic || '',
      done,
      expected,
      pct,
      current: running.length ? running[running.length - 1] : null,
      tail: nodes.slice(-4),
    };
  }

  // Serializes concurrent thread reads with MONOTONIC APPLY semantics.
  // Two failure modes bracketed by live incidents, and the gate must hold
  // both at once:
  //  - Regression (first live session): a slow STALE read landing after a
  //    fresher one applied would clobber the pane backwards. A response
  //    whose read began at-or-before the last APPLIED read's start is
  //    discarded (settle → false).
  //  - Livelock (Run-button incident): "latest-started wins" discarded
  //    every response once read latency exceeded the poll cadence — the
  //    worker gets busy the moment a plan task starts, each poll tick
  //    superseded the previous still-in-flight read, and the pane froze
  //    on 'draft' until the pipeline finished. A slow response now still
  //    applies (forward progress) as long as nothing newer applied first,
  //    and background ticks are SKIPPED while a background read is in
  //    flight so reads never convoy on a busy worker.
  // begin(background) → token (0 = skip this tick); settle(token) → true
  // when the caller should apply; fail(token) on fetch error; reset() on
  // conversation switch (marks every in-flight read stale).
  function readGate() {
    let seq = 0;
    let applied = 0;
    let inFlight = 0;
    return {
      begin(background) {
        if (background && inFlight > 0) return 0;
        inFlight += 1;
        seq += 1;
        return seq;
      },
      settle(token) {
        inFlight = Math.max(0, inFlight - 1);
        if (!token || token <= applied) return false;
        applied = token;
        return true;
      },
      fail() {
        inFlight = Math.max(0, inFlight - 1);
      },
      reset() {
        applied = seq;
      },
    };
  }

  window.PXChat = {
    splitNdjson,
    newTurnView,
    foldEvent,
    sessionStats,
    statusMeta,
    suggestedPrompts,
    slashMatches,
    expandSlash,
    planBlocks,
    planWarnings,
    threadFingerprint,
    watchProgress,
    readGate,
    SLASH,
  };
})();

/* ══════════════════════════════════════════════════════════════
   Cofounder mode — conversation surface (poindexter#948).
   Thread + activity rail + composer over the /api/chat P1 backend
   (PX.api.chat*). All stream/fold logic lives in chat-helpers.js
   (window.PXChat) so it stays unit-testable; this file is render
   + wiring only.
   ══════════════════════════════════════════════════════════════ */
/* global PXChat */

const CF = {}; // module-local namespace (mirrors trace.jsx's style)

// ── Small pieces ──────────────────────────────────────────────

function CfStatusChip({ status }) {
  const [label, cls] = PXChat.statusMeta(status);
  if (!label) return null;
  return <span className={`cf-status cf-status--${cls}`}>{label}</span>;
}

// Collapsed tool chip: ✓/✕ name · ms. Expands to args + result digests.
function CfToolChip({ part }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className={`cf-tool ${part.ok ? '' : 'cf-tool--err'}`}>
      <button className="cf-tool__head" onClick={() => setOpen(!open)}>
        <span className="cf-tool__glyph">{part.ok ? '✓' : '✕'}</span>
        <span className="cf-tool__name">{part.name}</span>
        {part.ms != null && <span className="cf-tool__ms">{part.ms}ms</span>}
        <Icon name="chevron" size={10} className={open ? 'is-open' : ''} />
      </button>
      {open && (
        <div className="cf-tool__body">
          {part.args_digest ? (
            <pre className="cf-tool__pre">
              <b>args</b> {part.args_digest}
            </pre>
          ) : null}
          <pre className="cf-tool__pre">
            <b>result</b> {part.result_digest || '(empty)'}
          </pre>
        </div>
      )}
    </div>
  );
}

// Approval card (P3 poindexter#949): pending → one-shot Approve/Deny;
// resolved → stamped outcome. Buttons disable while resolving and after.
function CfApprovalCard({ card, onResolved, pushToast }) {
  const [busy, setBusy] = React.useState(false);
  const pending = card.state === 'pending';
  const resolve = async (approve) => {
    if (busy || !pending) return;
    setBusy(true);
    try {
      await (approve
        ? PX.api.chatApprove(card.approval_id)
        : PX.api.chatDeny(card.approval_id));
      if (onResolved) await onResolved();
    } catch (e) {
      pushToast && pushToast(`Approval failed — ${e.message}`, 'red', '✕');
    } finally {
      // The reload normally swaps this card to its resolved state; if it
      // didn't (already_resolved race), give the buttons back rather than
      // stranding WORKING… forever.
      setBusy(false);
    }
  };
  return (
    <div
      className={`cf-approval ${pending ? '' : `cf-approval--${card.state}`}`}
    >
      <div className="cf-approval__head">
        <Icon
          name={pending ? 'alert' : card.state === 'approved' ? 'check' : 'x'}
          size={13}
        />
        <span className="cf-approval__tool">{card.tool}</span>
        <span className="cf-approval__state">
          {pending ? 'NEEDS YOUR APPROVAL' : card.state.toUpperCase()}
        </span>
      </div>
      <div className="cf-approval__summary">{card.summary}</div>
      {pending && (
        <div className="cf-approval__actions">
          <button
            className="mbtn mbtn--amber"
            disabled={busy}
            onClick={() => resolve(true)}
          >
            <Icon name="check" size={12} />
            {busy ? 'WORKING…' : 'Approve once'}
          </button>
          <button
            className="mbtn mbtn--ghost"
            disabled={busy}
            onClick={() => resolve(false)}
          >
            <Icon name="x" size={12} />
            Deny
          </button>
        </div>
      )}
      {!pending && card.state === 'approved' && (
        <div className="cf-approval__result">
          {card.executed_ok ? '✓' : '✕'} {card.result_digest || '(no output)'}
        </div>
      )}
    </div>
  );
}

// Completion card the watcher job appends when a linked run goes terminal.
// Post-lifecycle buttons reuse the console's EXISTING task endpoints —
// the same calls the approvals inbox makes.
function CfTaskResultCard({ card, onOpenTask, onActed, pushToast }) {
  const [busy, setBusy] = React.useState(false);
  const act = async (label, fn) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      pushToast && pushToast(`${label} — ok`, 'mint', '✓');
      if (onActed) await onActed();
    } catch (e) {
      pushToast && pushToast(`${label} failed — ${e.message}`, 'red', '✕');
    } finally {
      setBusy(false);
    }
  };
  const awaiting = card.status === 'awaiting_approval';
  const approved = card.status === 'approved';
  return (
    <div className="cf-card cf-card--result">
      <Icon name="pipeline" size={13} />
      <span>
        <b className="tnum">{String(card.task_id).slice(0, 8)}</b> {card.status}
        {card.quality_score != null
          ? ` · Q${Math.round(card.quality_score)}`
          : ''}
        {card.topic ? ` — ${card.topic}` : ''}
      </span>
      <span className="cf-card__actions">
        {awaiting && (
          <button
            className="mbtn mbtn--primary"
            disabled={busy}
            onClick={() => act('Approve', () => PX.api.approve(card.task_id))}
            title="Stage for publishing (approve ≠ publish)"
          >
            <Icon name="check" size={12} />
            Approve
          </button>
        )}
        {awaiting && (
          <button
            className="mbtn mbtn--ghost"
            disabled={busy}
            onClick={() => act('Reject', () => PX.api.reject(card.task_id))}
          >
            <Icon name="x" size={12} />
          </button>
        )}
        {approved && (
          <button
            className="mbtn mbtn--amber"
            disabled={busy}
            onClick={() =>
              act('Publish', () => PX.api.publishTask(card.task_id))
            }
            title="Ship it live"
          >
            <Icon name="bolt" size={12} />
            Publish
          </button>
        )}
        <button
          className="mbtn mbtn--ghost"
          onClick={() => onOpenTask && onOpenTask(card.task_id)}
          title="Open the task trace"
        >
          <Icon name="link" size={12} />
        </button>
      </span>
    </div>
  );
}

// Architect plan card (P4 poindexter#950): plain-language step blocks +
// technical toggle; one-shot Run creates the pipeline task; Adjust drops
// a feedback prefill into the composer (the model recomposes — the
// conversation IS the adjust loop).
function CfPlanCard({ card, onOpenTask, onResolved, onAdjust, pushToast }) {
  const [busy, setBusy] = React.useState(false);
  const [detail, setDetail] = React.useState(false);
  const draft = card.state === 'draft';
  const blocks = PXChat.planBlocks(card.nodes || []);
  const run = async () => {
    if (busy || !draft) return;
    setBusy(true);
    try {
      await PX.api.chatPlanRun(card.plan_id);
      if (onResolved) await onResolved();
    } catch (e) {
      pushToast && pushToast(`Run failed — ${e.message}`, 'red', '✕');
    } finally {
      // The reload normally swaps this card to 'ran'; if it didn't
      // (already_resolved race), give the button back rather than
      // stranding it busy forever.
      setBusy(false);
    }
  };
  return (
    <div className={`cf-plan ${draft ? '' : 'cf-plan--ran'}`}>
      <div className="cf-plan__head">
        <Icon name="pipeline" size={13} />
        <span className="cf-plan__title">
          Plan · {card.node_count} steps
          {card.topic ? ` — ${card.topic}` : ''}
        </span>
        <span className="cf-plan__slug tnum">{card.slug}</span>
      </div>
      <div className="cf-plan__blocks">
        {blocks.map((b) => (
          <span key={b.label} className="cf-plan__block">
            {b.label}
            {b.count > 1 ? ` ×${b.count}` : ''}
          </span>
        ))}
      </div>
      {detail && (
        <pre className="cf-tool__pre cf-plan__nodes">
          {(card.nodes || []).join('\n')}
        </pre>
      )}
      <div className="cf-plan__actions">
        {draft ? (
          <>
            <button
              className="mbtn mbtn--primary"
              disabled={busy}
              onClick={run}
              title="Create the pipeline task from this plan"
            >
              <Icon name="bolt" size={12} />
              {busy ? 'STARTING…' : 'Run pipeline'}
            </button>
            <button
              className="mbtn"
              disabled={busy}
              onClick={() => onAdjust && onAdjust('Adjust the plan: ')}
              title="Tell the architect what to change"
            >
              <Icon name="retry" size={12} />
              Adjust
            </button>
          </>
        ) : (
          <span className="cf-plan__ranchip">
            {/* No live-state claim here — the card doesn't track the task
                (a static "running" label outlived cancelled tasks and read
                as an infinite run). The watch rail + completion card are
                the live-status surfaces; this chip just links the task. */}
            <Icon name="check" size={12} /> started
            {card.task_id ? (
              <button
                className="mbtn mbtn--ghost"
                onClick={() => onOpenTask && onOpenTask(card.task_id)}
                title="Open the task trace"
              >
                <Icon name="link" size={12} />
                {String(card.task_id).slice(0, 8)}
              </button>
            ) : null}
          </span>
        )}
        <button
          className="mbtn mbtn--ghost cf-plan__detail"
          onClick={() => setDetail(!detail)}
        >
          {detail ? 'hide' : 'show'} technical detail
        </button>
      </div>
    </div>
  );
}

function CfCard({ card, onOpenTask, onResolved, onAdjust, pushToast }) {
  if (card.kind === 'plan')
    return (
      <CfPlanCard
        card={card}
        onOpenTask={onOpenTask}
        onResolved={onResolved}
        onAdjust={onAdjust}
        pushToast={pushToast}
      />
    );
  if (card.kind === 'approval')
    return (
      <CfApprovalCard
        card={card}
        onResolved={onResolved}
        pushToast={pushToast}
      />
    );
  if (card.kind === 'task_result')
    return (
      <CfTaskResultCard
        card={card}
        onOpenTask={onOpenTask}
        onActed={onResolved}
        pushToast={pushToast}
      />
    );
  if (card.kind === 'task_link') {
    return (
      <div className="cf-card">
        <Icon name="pipeline" size={13} />
        <span>
          task <b className="tnum">{String(card.task_id).slice(0, 8)}</b> queued
          — runs the pipeline, then waits in your approval inbox
        </span>
        <button
          className="mbtn mbtn--ghost"
          onClick={() => onOpenTask && onOpenTask(card.task_id)}
          title="Open the task trace"
        >
          <Icon name="link" size={12} />
          Trace
        </button>
      </div>
    );
  }
  // Forward-compatible: unknown card kinds (P3/P4 servers) render their
  // raw payload instead of vanishing.
  return (
    <div className="cf-card">
      <Icon name="doc" size={13} />
      <pre className="cf-tool__pre">{JSON.stringify(card)}</pre>
    </div>
  );
}

function CfParts({ parts, onOpenTask, onResolved, onAdjust, pushToast }) {
  return (
    <>
      {(parts || []).map((p, i) => {
        if (p.type === 'markdown')
          return (
            <div key={i} className="cf-text">
              {p.text}
            </div>
          );
        if (p.type === 'tool_call') return <CfToolChip key={i} part={p} />;
        if (p.type === 'card')
          return (
            <CfCard
              key={i}
              card={p.card || {}}
              onOpenTask={onOpenTask}
              onResolved={onResolved}
              onAdjust={onAdjust}
              pushToast={pushToast}
            />
          );
        return null;
      })}
    </>
  );
}

function CfMessage({
  msg,
  onOpenTask,
  onRetry,
  onResolved,
  onAdjust,
  pushToast,
}) {
  const isUser = msg.role === 'user';
  const isSystem = msg.role === 'system';
  const retryable =
    !isUser &&
    !isSystem &&
    (msg.turn_status === 'interrupted' || msg.turn_status === 'failed');
  const variant = isUser ? 'user' : isSystem ? 'system' : 'agent';
  return (
    <div className={`cf-msg cf-msg--${variant}`}>
      <div className="cf-msg__body">
        <CfParts
          parts={msg.parts}
          onOpenTask={onOpenTask}
          onResolved={onResolved}
          onAdjust={onAdjust}
          pushToast={pushToast}
        />
        <div className="cf-msg__meta">
          <CfStatusChip status={isSystem ? 'complete' : msg.turn_status} />
          {retryable && onRetry && (
            <button className="mbtn mbtn--ghost" onClick={onRetry}>
              <Icon name="retry" size={11} />
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// The in-flight assistant message, rendered from the live fold.
function CfLiveTurn({ view, onOpenTask, onAdjust, pushToast }) {
  return (
    <div className="cf-msg cf-msg--agent cf-msg--live">
      <div className="cf-msg__body">
        <CfParts
          parts={view.parts}
          onOpenTask={onOpenTask}
          onAdjust={onAdjust}
          pushToast={pushToast}
        />
        {view.liveTool && (
          <div className="cf-tool cf-tool--running">
            <span className="cf-tool__head">
              <span className="cf-spin" />
              <span className="cf-tool__name">{view.liveTool.name}</span>
              <span className="cf-tool__ms">running…</span>
            </span>
          </div>
        )}
        {!view.liveTool && !view.done && (
          <div className="cf-thinking">
            <span className="cf-spin" /> thinking…
          </div>
        )}
        {view.errors.map((e, i) => (
          <div key={i} className="cf-error">
            <Icon name="alert" size={12} /> {e.reason}
            {e.detail ? ` — ${e.detail}` : ''}
          </div>
        ))}
      </div>
    </div>
  );
}

// Empty state: persona intro + capability list from the LIVE catalog +
// suggested starter prompts.
function CfEmpty({ catalog, onPick }) {
  const prompts = PXChat.suggestedPrompts(catalog ? catalog.tools : []);
  return (
    <div className="cf-empty">
      <div className="cf-empty__logo">
        <Icon name="brain" size={26} />
      </div>
      <h2>{catalog ? catalog.persona : 'Cofounder'}</h2>
      <p className="cf-empty__sub">
        Ask in plain language — I act through{' '}
        {catalog ? catalog.tools.length : 0} tools and never publish without
        your sign-off.
      </p>
      {catalog && (
        <div className="cf-empty__tools">
          {catalog.tools.map((t) => (
            <span
              key={t.name}
              className={`cf-cap ${t.tier === 'write' ? 'cf-cap--write' : ''}`}
              title={t.description}
            >
              {t.name}
            </span>
          ))}
        </div>
      )}
      <div className="cf-empty__prompts">
        {prompts.map((p) => (
          <button key={p} className="mbtn" onClick={() => onPick(p)}>
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

function CfDisabled({ detail }) {
  return (
    <div className="cf-empty">
      <div className="cf-empty__logo cf-empty__logo--warn">
        <Icon name="alert" size={24} />
      </div>
      <h2>Cofounder chat is disabled</h2>
      <p className="cf-empty__sub">
        {detail ||
          'Enable it with `poindexter settings set console_chat_enabled true` (and route the LLM tier through a tool-capable provider — see docs/architecture/cofounder-chat.md).'}
      </p>
    </div>
  );
}

// Watched pipeline runs: live per-node progress from GET /api/chat/watch.
function CfWatchBlock({ watches, onOpenTask }) {
  const rows = Object.values(watches || {});
  if (!rows.length) return null;
  return (
    <div className="cf-rail__block">
      <div className="cf-rail__title">WATCHING RUNS</div>
      {rows.map((w) => {
        const p = PXChat.watchProgress(w);
        if (!p) return null;
        return (
          <div key={p.taskId} className="cf-watch">
            <button
              className="cf-watch__head"
              onClick={() => onOpenTask && onOpenTask(p.taskId)}
              title="Open the task trace"
            >
              <span className="tnum">{String(p.taskId).slice(0, 8)}</span>
              <span className="cf-watch__status">{p.status}</span>
              <span className="cf-rail__ms">
                {p.done}
                {p.expected ? `/${p.expected}` : ''} steps
              </span>
            </button>
            <div className="cf-watch__bar">
              <div
                className={`cf-watch__fill ${p.pct == null ? 'cf-watch__fill--indet' : ''}`}
                style={p.pct != null ? { width: p.pct + '%' } : {}}
              />
            </div>
            {p.current && (
              <div className="cf-rail__row">
                <span className="cf-spin" />{' '}
                {p.current.atom || p.current.node_id}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// Activity rail: current-turn live feed + watched runs + session totals.
function CfRail({ live, messages, brain, model, watches, onOpenTask }) {
  const stats = PXChat.sessionStats(messages || []);
  const liveStats = live ? live.stats : null;
  const feed = live
    ? live.parts.filter((p) => p.type === 'tool_call').slice(-8)
    : [];
  return (
    <aside className="cf-rail">
      <CfWatchBlock watches={watches} onOpenTask={onOpenTask} />
      <div className="cf-rail__block">
        <div className="cf-rail__title">NOW DOING</div>
        {!live && <div className="cf-rail__idle">idle — send a message</div>}
        {live && (
          <>
            {feed.map((p, i) => (
              <div key={i} className="cf-rail__row">
                <span className={p.ok ? 'c-mint' : 'c-red'}>
                  {p.ok ? '✓' : '✕'}
                </span>{' '}
                {p.name} <span className="cf-rail__ms">{p.ms}ms</span>
              </div>
            ))}
            {live.liveTool && (
              <div className="cf-rail__row">
                <span className="cf-spin" /> {live.liveTool.name}…
              </div>
            )}
            {!live.liveTool && !live.done && (
              <div className="cf-rail__row">
                <span className="cf-spin" /> thinking…
              </div>
            )}
          </>
        )}
      </div>
      <div className="cf-rail__block">
        <div className="cf-rail__title">SESSION</div>
        <div className="cf-rail__kv">
          <span>brain</span>
          <b>
            {brain || 'local'} · {model || stats.model || '—'}
          </b>
        </div>
        <div className="cf-rail__kv">
          <span>turns</span>
          <b>{stats.turns + (live && live.done ? 1 : 0)}</b>
        </div>
        <div className="cf-rail__kv">
          <span>tools</span>
          <b>
            {stats.toolCalls + (liveStats ? liveStats.toolCalls : 0)} calls
            {stats.toolErrors + (liveStats ? liveStats.toolErrors : 0) > 0
              ? ` · ${stats.toolErrors + (liveStats ? liveStats.toolErrors : 0)} err`
              : ''}
          </b>
        </div>
        <div className="cf-rail__kv">
          <span>tokens</span>
          <b className="tnum">
            {(
              stats.promptTokens +
              stats.completionTokens +
              (liveStats
                ? liveStats.promptTokens + liveStats.completionTokens
                : 0)
            ).toLocaleString()}
          </b>
        </div>
        <div className="cf-rail__kv">
          <span>cost</span>
          <b className="tnum">
            ${(stats.costUsd + (liveStats ? liveStats.costUsd : 0)).toFixed(2)}
          </b>
        </div>
      </div>
    </aside>
  );
}

function CfComposer({ value, setValue, onSend, disabled, onStop }) {
  const slash = PXChat.slashMatches(value);
  const taRef = React.useRef(null);
  const send = () => {
    let text = (value || '').trim();
    if (!text || disabled) return;
    // Enter expands slash commands instead of sending them literally
    // ('/brief' went out as-is in the first live session). An
    // argument-taking command with no argument drops its template into
    // the composer for completion rather than sending a bare stub.
    const expanded = PXChat.expandSlash(text);
    if (expanded && expanded.insert) {
      setValue(expanded.insert);
      if (taRef.current) taRef.current.focus();
      return;
    }
    if (expanded && expanded.send) text = expanded.send;
    onSend(text);
  };
  return (
    <div className="cf-composer">
      {slash.length > 0 && (
        <div className="cf-slash">
          {slash.map((s) => (
            <button
              key={s.cmd}
              className="cf-slash__item"
              onClick={() => {
                setValue(s.text);
                if (taRef.current) taRef.current.focus();
              }}
            >
              <b>{s.cmd}</b> <span>{s.text}</span>
            </button>
          ))}
        </div>
      )}
      <div className="cf-composer__row">
        <textarea
          ref={taRef}
          className="cf-composer__input"
          placeholder="Ask for anything — a post, a status check, a search… (/ for shortcuts)"
          value={value}
          rows={1}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        {disabled && onStop ? (
          <button
            className="mbtn mbtn--danger cf-composer__send"
            onClick={onStop}
            title="Stop this turn (finalizes as interrupted)"
          >
            <Icon name="kill" size={13} />
            STOP
          </button>
        ) : (
          <button
            className="mbtn mbtn--primary cf-composer__send"
            onClick={send}
            disabled={disabled || !(value || '').trim()}
            title={disabled ? 'Turn in progress…' : 'Send (Enter)'}
          >
            <Icon name="bolt" size={13} />
            {disabled ? 'RUNNING' : 'SEND'}
          </button>
        )}
      </div>
    </div>
  );
}

// ── The mode ─────────────────────────────────────────────────

function CofounderMode({ onOpenTask, pushToast }) {
  const api = window.PX.api;
  const [catalog, setCatalog] = React.useState(null);
  const [disabledDetail, setDisabledDetail] = React.useState(null);
  const [convs, setConvs] = React.useState(null); // null = loading
  const [selectedId, setSelectedId] = React.useState(null);
  const [thread, setThread] = React.useState(null);
  const [live, setLive] = React.useState(null); // PXChat turn view while streaming
  const [sending, setSending] = React.useState(false);
  const [pendingUserText, setPendingUserText] = React.useState(null);
  const [input, setInput] = React.useState('');
  const [listOpen, setListOpen] = React.useState(false); // mobile drawer
  const [watches, setWatches] = React.useState({}); // taskId → watch snapshot
  const scrollRef = React.useRef(null);
  const abortRef = React.useRef(null); // Stop button (P3)
  const readGateRef = React.useRef(PXChat.readGate()); // serializes thread reads
  const threadRef = React.useRef(null);
  React.useEffect(() => {
    threadRef.current = thread;
  }, [thread]);

  const disabled403 = (e) => {
    const m = String((e && e.message) || '');
    if (m.includes('403')) {
      setDisabledDetail(m.split('—').slice(1).join('—').trim() || null);
      return true;
    }
    return false;
  };

  React.useEffect(() => {
    api
      .chatTools()
      .then(setCatalog)
      .catch((e) => {
        if (!disabled403(e))
          pushToast(`Chat catalog failed — ${e.message}`, 'red', '✕');
      });
    api
      .chatList()
      .then((r) => setConvs(r.conversations || []))
      .catch((e) => {
        setConvs([]);
        if (!disabled403(e))
          pushToast(`Chat list failed — ${e.message}`, 'red', '✕');
      });
  }, []);

  // Every thread read flows through here, gated by PXChat.readGate with
  // MONOTONIC APPLY semantics (both directions earned by live incidents):
  // a stale response landing after a fresher one applied is discarded —
  // but a slow response with nothing newer applied still wins, so reads
  // keep making forward progress when the worker is busy and every fetch
  // is slower than the poll cadence (the Run-button freeze: the plan's
  // own pipeline task slowed the API the instant it started, and
  // latest-started-wins discarded every response until the task
  // finished). Background ticks are skipped while one is in flight.
  // Returns false only on a fetch failure — and a failed refresh never
  // blanks rows already on screen; the next poll repairs instead.
  // quiet: swallow fetch errors (background refreshes must never toast or
  // blank). skippable: poll ticks only — a tick may be dropped while a
  // read is in flight, but action-triggered refreshes (Run/approve/post-
  // turn) always fetch so their update is never left to the next tick.
  const syncThread = async (id, { quiet = false, skippable = false } = {}) => {
    const gate = readGateRef.current;
    const token = gate.begin(skippable);
    if (!token) return true; // a read is already in flight; tick dropped
    try {
      const fresh = await api.chatGet(id);
      if (!gate.settle(token)) return true; // a newer read already applied
      if (
        PXChat.threadFingerprint(fresh) !==
        PXChat.threadFingerprint(threadRef.current)
      )
        setThread(fresh);
      return true;
    } catch (e) {
      gate.fail();
      if (!quiet && !threadRef.current) {
        setThread(null);
        if (!disabled403(e))
          pushToast(`Thread load failed — ${e.message}`, 'red', '✕');
      }
      return false;
    }
  };

  React.useEffect(() => {
    setThread(null);
    threadRef.current = null;
    setLive(null);
    setPendingUserText(null);
    setWatches({});
    readGateRef.current.reset(); // in-flight reads of the old thread are stale
    if (selectedId) syncThread(selectedId);
  }, [selectedId]);

  // ── Thread refresh poll ────────────────────────────────────
  // Watcher completion messages (and turns from other tabs) land
  // server-side with no push channel — without this, they silently pile
  // up and flush together on the next manual action (the exact symptom
  // from the first live session). Quiet poll, fingerprint-guarded so an
  // unchanged thread never re-renders (and never yanks the scroll).
  React.useEffect(() => {
    if (!selectedId || sending) return undefined;
    const ms =
      Math.max(8, ((catalog && catalog.watch_poll_seconds) || 5) * 2) * 1000;
    const timer = setInterval(() => {
      // Transient read failures are swallowed inside syncThread — errors
      // here must never toast-spam or blank an idle thread.
      syncThread(selectedId, { quiet: true, skippable: true });
    }, ms);
    return () => clearInterval(timer);
  }, [selectedId, sending, catalog]);

  // ── Watched-run polling (P3 poindexter#949) ────────────────
  // Every conversation-linked task that is not yet terminal polls the slim
  // watch read on the configured cadence. A task flipping terminal reloads
  // the thread once (the watcher job's completion message lands there) and
  // drops out of the poll set.
  const watchPollMs =
    Math.max(2, (catalog && catalog.watch_poll_seconds) || 5) * 1000;
  React.useEffect(() => {
    if (!selectedId) return undefined;
    const linkIds = (thread ? thread.task_links || [] : []).map(
      (l) => l.pipeline_task_id
    );
    const liveIds = live ? live.taskIds : [];
    const ids = [...new Set([...linkIds, ...liveIds])].filter((id) => {
      const snap = watches[id];
      return !(snap && snap.terminal);
    });
    if (!ids.length) return undefined;
    let alive = true;
    const tick = async () => {
      for (const id of ids) {
        try {
          const snap = await PX.api.chatWatch(id);
          if (!alive) return;
          setWatches((w) => ({ ...w, [id]: snap }));
          if (snap.terminal && !(watches[id] && watches[id].terminal)) {
            await syncThread(selectedId, { quiet: true });
          }
        } catch (e) {
          // Unknown task / transient read failure: drop silently; the rail
          // simply shows nothing rather than a fabricated bar.
          if (alive)
            setWatches((w) => ({
              ...w,
              [id]: { task_id: id, terminal: true, nodes: [], nodes_done: 0 },
            }));
        }
      }
    };
    tick();
    const timer = setInterval(tick, watchPollMs);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [selectedId, thread, live && live.taskIds.join(','), watchPollMs]);

  // Keep the thread pinned to the bottom while streaming / loading.
  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread, live, pendingUserText]);

  const refreshList = () =>
    api
      .chatList()
      .then((r) => setConvs(r.conversations || []))
      .catch(() => {});

  const newConversation = () =>
    api
      .chatCreate('')
      .then((conv) => {
        setConvs((c) => [conv, ...(c || [])]);
        setSelectedId(conv.id);
        setListOpen(false);
      })
      .catch((e) => {
        if (!disabled403(e))
          pushToast(`Create failed — ${e.message}`, 'red', '✕');
      });

  const archive = (id) =>
    api
      .chatArchive(id)
      .then(() => {
        setConvs((c) => (c || []).filter((x) => x.id !== id));
        if (selectedId === id) setSelectedId(null);
      })
      .catch((e) => pushToast(`Archive failed — ${e.message}`, 'red', '✕'));

  const send = async (text) => {
    if (sending) return;
    let convId = selectedId;
    try {
      if (!convId) {
        const conv = await api.chatCreate('');
        setConvs((c) => [conv, ...(c || [])]);
        setSelectedId(conv.id);
        convId = conv.id;
      }
      setSending(true);
      setInput('');
      setPendingUserText(text);
      setLive(PXChat.newTurnView());
      const controller =
        typeof AbortController !== 'undefined' ? new AbortController() : null;
      abortRef.current = controller;
      await api.chatSend(
        convId,
        text,
        (ev) => setLive((v) => PXChat.foldEvent(v || PXChat.newTurnView(), ev)),
        controller ? { signal: controller.signal } : {}
      );
    } catch (e) {
      if (e && e.name === 'AbortError') {
        // Operator hit Stop: the worker finalizes the turn 'interrupted';
        // the thread reload below renders the honest state.
      } else {
        if (!disabled403(e))
          pushToast(`Turn failed — ${e.message}`, 'red', '✕');
        setLive((v) => {
          const base = v || PXChat.newTurnView();
          return {
            ...base,
            done: true,
            status: 'failed',
            errors: base.errors.concat([
              { reason: 'request', detail: e.message },
            ]),
          };
        });
      }
    } finally {
      abortRef.current = null;
      // Swap the live fold for the canonical persisted rows. One quick
      // retry before giving up: a transient read failure right here would
      // otherwise vanish the exchange until the next poll repairs it.
      const ok = await syncThread(convId, { quiet: true });
      if (!ok) {
        await new Promise((r) => setTimeout(r, 1200));
        await syncThread(convId, { quiet: true });
      }
      refreshList();
      setLive(null);
      setPendingUserText(null);
      setSending(false);
    }
  };

  const messages = thread ? thread.messages : null;
  const lastUserText = (() => {
    const rows = (messages || []).filter((m) => m.role === 'user');
    const last = rows[rows.length - 1];
    const p = last && (last.parts || []).find((x) => x.type === 'markdown');
    return p ? p.text : '';
  })();

  if (disabledDetail !== null && !catalog)
    return (
      <div className="cf">
        <CfDisabled detail={disabledDetail} />
      </div>
    );

  return (
    <div className="cf">
      <aside className={`cf-list ${listOpen ? 'is-open' : ''}`}>
        <div className="cf-list__head">
          <span>CONVERSATIONS</span>
          <button className="mbtn mbtn--primary" onClick={newConversation}>
            <Icon name="bolt" size={11} />
            New
          </button>
        </div>
        {convs === null && <div className="cf-rail__idle">loading…</div>}
        {convs !== null && convs.length === 0 && (
          <div className="cf-rail__idle">none yet</div>
        )}
        {(convs || []).map((c) => (
          <div
            key={c.id}
            className={`cf-list__item ${selectedId === c.id ? 'is-active' : ''}`}
            onClick={() => {
              setSelectedId(c.id);
              setListOpen(false);
            }}
          >
            <div className="cf-list__title">{c.title || '(untitled)'}</div>
            <div className="cf-list__meta">
              <span>{c.message_count || 0} msg</span>
              <button
                className="cf-list__x"
                title="Archive"
                onClick={(e) => {
                  e.stopPropagation();
                  archive(c.id);
                }}
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </aside>

      <section className="cf-main">
        <div className="cf-main__head">
          <button
            className="mbtn mbtn--ghost cf-main__listtoggle"
            onClick={() => setListOpen(!listOpen)}
          >
            <Icon name="audit" size={12} />
          </button>
          <Icon name="brain" size={15} />
          <span className="cf-main__title">
            {(thread && thread.conversation.title) ||
              (selectedId ? '(untitled)' : 'Cofounder')}
          </span>
          <span className="cf-main__brain">
            {thread ? thread.conversation.brain : 'local'}
          </span>
        </div>
        <div className="cf-thread" ref={scrollRef}>
          {!selectedId && <CfEmpty catalog={catalog} onPick={send} />}
          {selectedId && !thread && !pendingUserText && (
            <div className="cf-rail__idle">loading thread…</div>
          )}
          {selectedId &&
            thread &&
            messages.length === 0 &&
            !pendingUserText && <CfEmpty catalog={catalog} onPick={send} />}
          {(messages || []).map((m) => (
            <CfMessage
              key={m.id}
              msg={m}
              onOpenTask={onOpenTask}
              pushToast={pushToast}
              onAdjust={(t) => setInput(t)}
              onResolved={() => syncThread(selectedId, { quiet: true })}
              onRetry={
                m.role !== 'user' &&
                (m.turn_status === 'interrupted' || m.turn_status === 'failed')
                  ? () => setInput(lastUserText)
                  : null
              }
            />
          ))}
          {pendingUserText && (
            <div className="cf-msg cf-msg--user">
              <div className="cf-msg__body">
                <div className="cf-text">{pendingUserText}</div>
              </div>
            </div>
          )}
          {live && (
            <CfLiveTurn
              view={live}
              onOpenTask={onOpenTask}
              onAdjust={(t) => setInput(t)}
              pushToast={pushToast}
            />
          )}
        </div>
        <CfComposer
          value={input}
          setValue={setInput}
          onSend={send}
          disabled={sending}
          onStop={() => abortRef.current && abortRef.current.abort()}
        />
      </section>

      <CfRail
        live={live}
        messages={messages}
        brain={thread && thread.conversation.brain}
        model={null}
        watches={watches}
        onOpenTask={onOpenTask}
      />
    </div>
  );
}

window.CofounderMode = CofounderMode;
window.CF = CF;

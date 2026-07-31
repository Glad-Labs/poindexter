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

function CfCard({ card, onOpenTask }) {
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

function CfParts({ parts, onOpenTask }) {
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
          return <CfCard key={i} card={p.card || {}} onOpenTask={onOpenTask} />;
        return null;
      })}
    </>
  );
}

function CfMessage({ msg, onOpenTask, onRetry }) {
  const isUser = msg.role === 'user';
  const retryable =
    !isUser &&
    (msg.turn_status === 'interrupted' || msg.turn_status === 'failed');
  return (
    <div className={`cf-msg cf-msg--${isUser ? 'user' : 'agent'}`}>
      <div className="cf-msg__body">
        <CfParts parts={msg.parts} onOpenTask={onOpenTask} />
        <div className="cf-msg__meta">
          <CfStatusChip status={msg.turn_status} />
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
function CfLiveTurn({ view, onOpenTask }) {
  return (
    <div className="cf-msg cf-msg--agent cf-msg--live">
      <div className="cf-msg__body">
        <CfParts parts={view.parts} onOpenTask={onOpenTask} />
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

// Activity rail: current-turn live feed + session totals.
function CfRail({ live, messages, brain, model }) {
  const stats = PXChat.sessionStats(messages || []);
  const liveStats = live ? live.stats : null;
  const feed = live
    ? live.parts.filter((p) => p.type === 'tool_call').slice(-8)
    : [];
  return (
    <aside className="cf-rail">
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

function CfComposer({ value, setValue, onSend, disabled }) {
  const slash = PXChat.slashMatches(value);
  const taRef = React.useRef(null);
  const send = () => {
    const text = (value || '').trim();
    if (!text || disabled) return;
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
        <button
          className="mbtn mbtn--primary cf-composer__send"
          onClick={send}
          disabled={disabled || !(value || '').trim()}
          title={disabled ? 'Turn in progress…' : 'Send (Enter)'}
        >
          <Icon name="bolt" size={13} />
          {disabled ? 'RUNNING' : 'SEND'}
        </button>
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
  const scrollRef = React.useRef(null);

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

  const loadThread = (id) =>
    api
      .chatGet(id)
      .then((t) => setThread(t))
      .catch((e) => {
        setThread(null);
        if (!disabled403(e))
          pushToast(`Thread load failed — ${e.message}`, 'red', '✕');
      });

  React.useEffect(() => {
    setThread(null);
    setLive(null);
    setPendingUserText(null);
    if (selectedId) loadThread(selectedId);
  }, [selectedId]);

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
      await api.chatSend(convId, text, (ev) =>
        setLive((v) => PXChat.foldEvent(v || PXChat.newTurnView(), ev))
      );
    } catch (e) {
      if (!disabled403(e)) pushToast(`Turn failed — ${e.message}`, 'red', '✕');
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
    } finally {
      // Swap the live fold for the canonical persisted rows.
      await loadThread(convId);
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
          {live && <CfLiveTurn view={live} onOpenTask={onOpenTask} />}
        </div>
        <CfComposer
          value={input}
          setValue={setInput}
          onSend={send}
          disabled={sending}
        />
      </section>

      <CfRail
        live={live}
        messages={messages}
        brain={thread && thread.conversation.brain}
        model={null}
      />
    </div>
  );
}

window.CofounderMode = CofounderMode;
window.CF = CF;

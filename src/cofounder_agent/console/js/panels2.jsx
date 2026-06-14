/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — gap-closing panels.
   Revenue · Media (Stage 2) · QA Rails · Sub-tool Launcher.
   ────────────────────────────────────────────────────────────── */

/* ─── Findings — probe-routing triage (#461) ────────────────── */
// Read-only: findings are audit_log rows the brain's findings_alert_router
// delivers autonomously (watermark-based). There is no ack/route HTTP surface,
// so this panel surfaces "what fired, how it routed, what's still pending" —
// no fabricated mutation buttons (matches the self-heal-not-suppress model).
const SEV_TAG = {
  critical: 'red',
  warn: 'amber',
  warning: 'amber',
  info: 'cyan',
};
const SEV_LED = {
  critical: 'led-err',
  warn: 'led-warn',
  warning: 'led-warn',
  info: 'led-off',
};
const FINDING_STATUS_TAG = {
  PENDING: 'amber',
  routed: 'mint',
  'log-only': 'cyan',
};
function FindingsPanel({ findings, onOpen }) {
  const f = findings || {};
  const rows = f.findings || [];
  const emitted = (f.counts && f.counts.emitted) || 0;
  const pending = (f.counts && f.counts.pending) || 0;
  return (
    <Panel
      idx="F1"
      title="FINDINGS"
      meta={`${emitted} EMITTED · ${pending} PENDING`}
      flush
      action="Detail"
      onAction={onOpen}
    >
      <div
        style={{ padding: 12, borderBottom: '1px solid var(--gl-hairline)' }}
      >
        <div style={{ display: 'flex', gap: 20 }}>
          <div>
            <span className="kpi__value" style={{ fontSize: 26 }}>
              {emitted}
            </span>
            <div className="mono c-dim" style={{ fontSize: 10 }}>
              emitted · {f.hours || 168}h
            </div>
          </div>
          <div>
            <span
              className={`kpi__value ${pending ? 'is-amber' : ''}`}
              style={{ fontSize: 26 }}
            >
              {pending}
            </span>
            <div className="mono c-dim" style={{ fontSize: 10 }}>
              pending delivery
            </div>
          </div>
        </div>
        <div
          style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}
        >
          {(f.by_severity || []).map((s) => (
            <span
              key={s.severity}
              className={`tag tag--${SEV_TAG[s.severity] || 'cyan'}`}
            >
              {s.severity} · {s.count}
            </span>
          ))}
        </div>
      </div>
      <div>
        {rows.length === 0 ? (
          <div className="empty">
            <span className="glyph" aria-hidden="true">
              ✓
            </span>
            No findings in the last {f.hours || 168}h.
          </div>
        ) : (
          rows.slice(0, 8).map((row) => (
            <div
              key={row.id}
              className="svc"
              style={{
                gridTemplateColumns: 'auto 1fr auto',
                cursor: 'default',
              }}
            >
              <span
                className={`svc__led ${SEV_LED[row.severity] || 'led-off'}`}
                title={row.severity}
              />
              <span className="svc__name truncate">
                {row.kind}
                <small>{row.title || row.source}</small>
              </span>
              <span className="act-item__acts">
                <span
                  className={`tag tag--${FINDING_STATUS_TAG[row.status] || 'cyan'}`}
                >
                  {row.status}
                </span>
                <span className="tag" title="delivery policy">
                  {row.delivery}
                </span>
              </span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}

/* ─── Memory recall — semantic search over the pgvector corpus ───
   GET /api/memory/search?q=&source_table=&limit= . This is the recall surface
   the Brain drawer hosts — and doubles as "recall decision" (scope to
   memory/brain to pull decision-log embeddings). Read-only. `sources` is the
   live by_source_table list ([key,count]) so the scope select stays
   data-driven; the embedded mock returns canned hits offline. */
function MemorySearch({ sources }) {
  const [q, setQ] = React.useState('');
  const [scope, setScope] = React.useState('');
  const [s, setS] = React.useState({ status: 'idle', hits: [], err: '' });
  const keys = (sources || []).map(([k]) => k);
  const inputStyle = {
    background: 'var(--gl-surface-2)',
    color: 'var(--gl-text)',
    border: '1px solid var(--gl-hairline)',
    borderRadius: 4,
    padding: '6px 8px',
    fontSize: 12,
  };
  const run = async () => {
    const query = q.trim();
    if (!query) return;
    setS({ status: 'loading', hits: [], err: '' });
    try {
      const opts =
        '&limit=10' +
        (scope ? '&source_table=' + encodeURIComponent(scope) : '');
      const res = await PX.api.memorySearch(query, opts);
      const hits = (res && res.hits) || [];
      setS({ status: hits.length ? 'done' : 'empty', hits, err: '' });
    } catch (e) {
      setS({ status: 'error', hits: [], err: e.message });
    }
  };
  return (
    <>
      <div className="section-label">Recall — semantic search</div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <input
          type="text"
          style={{ ...inputStyle, flex: 1, minWidth: 0 }}
          placeholder='e.g. "why cost tiers over hardcoded models"'
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyUp={(e) => e.key === 'Enter' && run()}
        />
        {keys.length > 0 && (
          <select
            style={inputStyle}
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            title="scope to a source_table (memory / brain = decisions)"
          >
            <option value="">all</option>
            {keys.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        )}
        <button className="mbtn mbtn--primary" onClick={run}>
          <Icon name="search" size={11} />
          Search
        </button>
      </div>
      {s.status === 'loading' && (
        <div className="mono c-dim" style={{ fontSize: 11 }}>
          Searching…
        </div>
      )}
      {s.status === 'error' && (
        <div className="mono c-red" style={{ fontSize: 11 }}>
          Search failed — {s.err}
        </div>
      )}
      {s.status === 'empty' && (
        <div className="mono c-dim" style={{ fontSize: 11 }}>
          No matches.
        </div>
      )}
      {s.status === 'done' &&
        s.hits.map((h, i) => (
          <div
            key={i}
            style={{
              background: 'var(--gl-surface)',
              border: '1px solid var(--gl-hairline)',
              borderRadius: 4,
              padding: '6px 8px',
              marginBottom: 6,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 8,
              }}
            >
              <span className="mono c-cyan" style={{ fontSize: 10 }}>
                {h.source_table}#{h.source_id}
                {h.writer ? ' · ' + h.writer : ''}
              </span>
              <span className="mono c-mint tnum" style={{ fontSize: 10 }}>
                {(h.similarity != null ? h.similarity : 0).toFixed(3)}
              </span>
            </div>
            <div
              className="c-text"
              style={{ fontSize: 11, marginTop: 3, lineHeight: 1.4 }}
            >
              {(h.text_preview || '').slice(0, 240)}
            </div>
          </div>
        ))}
    </>
  );
}

/* ─── Revenue ───────────────────────────────────────────────── */
function RevenuePanel({ revenue, onOpen }) {
  const r = revenue;
  const delta = Math.round(((r.month - r.prevMonth) / r.prevMonth) * 100);
  return (
    <Panel
      idx="R1"
      title="REVENUE"
      meta={`MRR $${r.mrr} · LEMON SQUEEZY`}
      flush
      action="Detail"
      onAction={onOpen}
    >
      <div
        style={{ padding: 12, borderBottom: '1px solid var(--gl-hairline)' }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span className="kpi__value is-mint" style={{ fontSize: 32 }}>
            <span className="unit" style={{ fontSize: 16 }}>
              $
            </span>
            {r.month.toLocaleString()}
          </span>
          <span className={`delta ${delta >= 0 ? 'delta--up' : 'delta--down'}`}>
            {delta >= 0 ? '+' : ''}
            {delta}% MoM
          </span>
        </div>
        <div className="mono c-dim" style={{ fontSize: 10, marginTop: 2 }}>
          month to date · ${r.today} today · net ${r.net.toLocaleString()}
        </div>
        <div style={{ marginTop: 10 }}>
          <MiniBars
            data={r.daily}
            color="cyan"
            labels={['30d', 'today']}
            height={48}
          />
        </div>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3,1fr)',
          gap: 1,
          background: 'var(--gl-hairline)',
        }}
      >
        {[
          ['Orders', r.orders, ''],
          ['Subs', r.subscriptions, 'is-mint'],
          ['Refunds', r.refunds, r.refunds > 0 ? 'is-amber' : ''],
        ].map(([l, v, c]) => (
          <div
            key={l}
            style={{ background: 'var(--gl-surface)', padding: '9px 12px' }}
          >
            <div className="kpi__label">{l}</div>
            <div className={`kpi__value ${c}`} style={{ fontSize: 22 }}>
              {v}
            </div>
          </div>
        ))}
      </div>
      <div style={{ padding: '10px 12px' }}>
        <div className="kpi__label" style={{ marginBottom: 8 }}>
          Top earning posts
        </div>
        {r.topPosts.slice(0, 3).map((p) => (
          <div
            key={p.title}
            className="svc"
            style={{
              gridTemplateColumns: '1fr auto',
              padding: '6px 0',
              cursor: 'default',
            }}
          >
            <span
              className="svc__name truncate"
              style={{ fontSize: 11 }}
              title={p.title}
            >
              {p.title}
            </span>
            <span className="svc__metric c-mint">${p.rev}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ─── Media pipeline (Stage 2) ──────────────────────────────── */
const MEDIUM_TAG = { video: 'cyan', podcast: 'amber', short: 'mint' };
function MediaPanel({ media, onOpenItem, onApprove, onReject }) {
  const m = media || {};
  const queue = m.queue || [];
  // Render-rate KPIs have no read on the media-approval route → '—' in live
  // (feedback_no_dummy_data); gate2Pending + the queue are the real signals.
  const dash = (v, suffix = '') => (v == null ? '—' : v + suffix);
  const meta =
    m.renderSuccess24h != null
      ? `RENDER ${m.renderSuccess24h}% · ${m.dispatched} DISPATCHED`
      : `${m.gate2Pending ?? 0} GATE-2 PENDING`;
  return (
    <Panel idx="M1" title="MEDIA PIPELINE · STAGE 2" meta={meta} flush>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3,1fr)',
          gap: 1,
          background: 'var(--gl-hairline)',
          borderBottom: '1px solid var(--gl-hairline)',
        }}
      >
        {[
          ['Render OK 24h', dash(m.renderSuccess24h, '%'), ''],
          [
            'Gate-2 pending',
            dash(m.gate2Pending),
            m.gate2Pending > 0 ? 'is-amber' : '',
          ],
          ['Videos total', dash(m.videosPersisted), ''],
        ].map(([l, v, c]) => (
          <div
            key={l}
            style={{ background: 'var(--gl-surface)', padding: '9px 12px' }}
          >
            <div className="kpi__label">{l}</div>
            <div className={`kpi__value ${c}`} style={{ fontSize: 20 }}>
              {v}
            </div>
          </div>
        ))}
      </div>
      <div>
        {queue.length === 0 ? (
          <div className="mono c-dim" style={{ padding: 12, fontSize: 11 }}>
            No media awaiting Gate-2.
          </div>
        ) : (
          queue.map((it) => (
            <div
              key={it.id}
              className="svc"
              style={{
                gridTemplateColumns: 'auto 1fr auto auto',
                cursor: 'pointer',
              }}
              onClick={() => onOpenItem(it)}
            >
              <span className={`tag tag--${MEDIUM_TAG[it.medium] || 'cyan'}`}>
                {it.medium}
              </span>
              <span
                className="svc__name truncate"
                style={{ fontSize: 11 }}
                title={it.title}
              >
                {it.title}
                <small>
                  {it.dur ? it.dur + ' · ' : ''}Q{it.quality ?? '—'}
                </small>
              </span>
              <span className="svc__metric c-dim">{it.age}</span>
              <span
                className="act-item__acts"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  className="mbtn mbtn--primary"
                  title="Approve · clear for dispatch"
                  onClick={() => onApprove(it)}
                >
                  <Icon name="check" size={11} />
                </button>
                {onReject && (
                  <button
                    className="mbtn mbtn--danger mbtn--ghost"
                    title="Reject · regenerate"
                    onClick={() => onReject(it)}
                  >
                    <Icon name="x" size={11} />
                  </button>
                )}
              </span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}

/* ─── QA Rails ──────────────────────────────────────────────── */
function QAPanel({ qa, onOpen }) {
  const live = window.PX.api.isLive();
  const rails = qa.rails || [];
  const hardCount = rails.filter((r) => r.gate === 'hard').length;
  const maxReason = Math.max(...qa.reasons.map((r) => r[1]));
  return (
    <Panel
      idx="Q1"
      title="QA RAILS · MULTI-MODEL REVIEW"
      meta={
        live
          ? `${rails.length} RAILS · ${hardCount} HARD`
          : `PASS ${qa.passRate}% · REJECT ${qa.rejectionRate}%`
      }
      flush
      action="Quality detail"
      onAction={onOpen}
    >
      {/* Rails — real config (modules/content/atoms/qa_*.py → qa.aggregate). */}
      <div
        style={{ padding: 12, borderBottom: '1px solid var(--gl-hairline)' }}
      >
        <div className="kpi__label" style={{ marginBottom: 8 }}>
          Rails · {rails.length} → qa.aggregate
        </div>
        {rails.map((r) => (
          <div
            key={r.rail}
            className="svc"
            style={{
              gridTemplateColumns: '1fr auto',
              padding: '4px 0',
              cursor: 'default',
            }}
          >
            <span
              className="mono"
              style={{ fontSize: 10.5, color: 'var(--gl-text-muted)' }}
            >
              {r.rail}
            </span>
            <span
              className="mono tnum"
              style={{
                fontSize: 9,
                padding: '1px 5px',
                borderRadius: 3,
                border: '1px solid var(--gl-hairline-strong)',
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                color:
                  r.gate === 'hard' ? 'var(--gl-red)' : 'var(--gl-text-dim)',
              }}
            >
              {r.gate}
            </span>
          </div>
        ))}
      </div>

      {live && (
        <div style={{ padding: 12 }}>
          <div className="empty" style={{ padding: '18px 8px', fontSize: 11 }}>
            Per-pass QA metrics (pass-rate, rejection reasons, by-model) come
            from <span className="c-cyan">audit_log · qa_pass_completed</span> —
            see the QA Rails Grafana board. A console read is pending.
          </div>
        </div>
      )}

      {!live && (
        <>
          <div
            style={{
              padding: 12,
              borderBottom: '1px solid var(--gl-hairline)',
            }}
          >
            <div className="kpi__label" style={{ marginBottom: 8 }}>
              Rejection reasons · 7d
            </div>
            {qa.reasons.map(([name, n]) => (
              <div key={name} style={{ marginBottom: 7 }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 10.5,
                    display: 'flex',
                    justifyContent: 'space-between',
                    marginBottom: 2,
                  }}
                >
                  <span className="c-muted">{name}</span>
                  <span className="c-text tnum">{n}</span>
                </div>
                <Meter value={n} max={maxReason} color="amber" />
              </div>
            ))}
          </div>
          <div
            style={{
              padding: '10px 12px',
              borderBottom: '1px solid var(--gl-hairline)',
            }}
          >
            <div className="kpi__label" style={{ marginBottom: 8 }}>
              ⚠ Hallucination guardrails · rate/5m
            </div>
            {qa.hallucination.map((h) => (
              <div
                key={h.rule}
                className="svc"
                style={{
                  gridTemplateColumns: '1fr auto',
                  padding: '5px 0',
                  cursor: 'default',
                }}
              >
                <span
                  className="mono"
                  style={{ fontSize: 10.5, color: 'var(--gl-text-muted)' }}
                >
                  {h.rule}
                </span>
                <span
                  className={`mono c-${h.tone} tnum`}
                  style={{ fontSize: 11 }}
                >
                  {h.rate.toFixed(1)}
                </span>
              </div>
            ))}
          </div>
          <div style={{ padding: '10px 12px' }}>
            <div className="kpi__label" style={{ marginBottom: 8 }}>
              Approval rate by writer model
            </div>
            {qa.byModel.map((m) => (
              <div key={m.model} style={{ marginBottom: 7 }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 10.5,
                    display: 'flex',
                    justifyContent: 'space-between',
                    marginBottom: 2,
                  }}
                >
                  <span className="c-muted">
                    {m.model}
                    <span className="c-dim"> · {m.tasks}</span>
                  </span>
                  <span className="c-text tnum">{m.appr}%</span>
                </div>
                <Meter
                  value={m.appr}
                  max={100}
                  color={m.appr >= 85 ? 'mint' : 'amber'}
                />
              </div>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

/* ─── Sub-tool launcher + voice ─────────────────────────────── */
function LauncherPanel({ tools, onLaunch, onVoice }) {
  return (
    <Panel idx="X1" title="LAUNCH" meta="EXTERNAL TOOLS" flush>
      <button className="voice-cta" onClick={onVoice}>
        <span className="voice-cta__dot">
          <span />
        </span>
        <span className="voice-cta__txt">
          <b>Talk to Poindexter</b>
          <small>LiveKit voice · ask anything</small>
        </span>
        <Icon name="play" size={15} />
      </button>
      <div className="launch-grid">
        {tools.map((t) => (
          <button
            key={t.name}
            className="launch-tile"
            onClick={() => onLaunch(t)}
          >
            <span
              className={`svc__led ${{ ok: 'led-ok', warn: 'led-warn', err: 'led-err' }[t.status] || 'led-off'}`}
            />
            <span className="launch-tile__name">{t.name}</span>
            <span className="launch-tile__sub">{t.sub}</span>
            <Icon name="link" size={12} className="launch-tile__arr" />
          </button>
        ))}
      </div>
    </Panel>
  );
}

/* ─── Topics triage (open discovery batches) ────────────────── */
// One open batch per niche; each holds the ranked candidates a discovery
// sweep produced. The operator Picks a winner (operator_rank #1), then
// Resolves (advance winner → content pipeline) or Rejects (discard the batch,
// free the niche for a fresh sweep). Resolve is disabled until something is
// ranked — the backend 400s an unranked resolve, so we gate it in the UI too.
const CAND_KIND_TAG = { external: 'cyan', internal: 'amber' };
function TopicsPanel({ topics, onPick, onResolve, onReject }) {
  const batches = (topics && topics.batches) || [];
  return (
    <Panel
      idx="T1"
      title="TOPICS · DISCOVERY BATCHES"
      meta={`${batches.length} OPEN · AWAITING DECISION`}
      flush
    >
      {batches.length === 0 && (
        <div className="empty" style={{ padding: '20px 12px', fontSize: 11 }}>
          No open topic batches. Discovery sweeps queue candidates per niche;
          when one is pending you’ll rank + resolve it here.
        </div>
      )}
      {batches.map((b) => {
        const cands = b.candidates || [];
        const ranked = cands.some((c) => c.operator_rank === 1);
        return (
          <div
            key={b.batch_id}
            style={{ borderBottom: '1px solid var(--gl-hairline)' }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '9px 12px',
              }}
            >
              <span className="tag tag--cyan">{b.niche_slug || 'niche'}</span>
              <span className="mono c-dim" style={{ fontSize: 10, flex: 1 }}>
                {b.candidate_count} candidates ·{' '}
                {String(b.batch_id).slice(0, 8)}
              </span>
              <button
                className="mbtn mbtn--primary"
                disabled={!ranked}
                title={
                  ranked
                    ? 'Advance the rank-1 winner into the content pipeline'
                    : 'Pick a winner first'
                }
                onClick={() => onResolve(b)}
              >
                <Icon name="play" size={11} />
                Resolve
              </button>
              <button
                className="mbtn mbtn--danger mbtn--ghost"
                title="Discard this batch — frees the niche for a fresh sweep"
                onClick={() => onReject(b)}
              >
                <Icon name="kill" size={11} />
              </button>
            </div>
            <div>
              {cands.map((c) => {
                const isWinner = c.operator_rank === 1;
                return (
                  <div
                    key={c.id}
                    className="svc"
                    style={{
                      gridTemplateColumns: 'auto 1fr auto',
                      cursor: 'default',
                      padding: '5px 12px',
                      background: isWinner
                        ? 'var(--gl-mint-dim, rgba(80,220,160,0.06))'
                        : 'transparent',
                    }}
                  >
                    <span
                      className="mono tnum c-dim"
                      style={{ fontSize: 10, minWidth: 34 }}
                    >
                      {c.operator_rank
                        ? `#${c.operator_rank}`
                        : `sys${c.rank_in_batch}`}
                    </span>
                    <span
                      className="svc__name truncate"
                      style={{ fontSize: 11 }}
                      title={c.operator_edited_topic || c.title}
                    >
                      {c.operator_edited_topic || c.title}
                      <small>
                        <span className={`c-${CAND_KIND_TAG[c.kind] || 'dim'}`}>
                          {c.kind}
                        </span>{' '}
                        · eff {Number(c.effective_score).toFixed(1)}
                      </small>
                    </span>
                    <span
                      className="act-item__acts"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        className={`mbtn ${isWinner ? 'mbtn--primary' : 'mbtn--ghost'}`}
                        title="Make this the winner (operator rank #1)"
                        onClick={() => onPick(b, c)}
                      >
                        <Icon name="check" size={10} />
                        {isWinner ? 'Winner' : 'Pick'}
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </Panel>
  );
}

Object.assign(window, {
  RevenuePanel,
  MediaPanel,
  QAPanel,
  LauncherPanel,
  TopicsPanel,
});

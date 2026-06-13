/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — gap-closing panels.
   Revenue · Media (Stage 2) · QA Rails · Sub-tool Launcher.
   ────────────────────────────────────────────────────────────── */

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
function MediaPanel({ media, onOpenItem, onApprove }) {
  const m = media;
  return (
    <Panel
      idx="M1"
      title="MEDIA PIPELINE · STAGE 2"
      meta={`RENDER ${m.renderSuccess24h}% · ${m.dispatched} DISPATCHED`}
      flush
    >
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
          ['Render OK 24h', m.renderSuccess24h + '%', ''],
          [
            'Gate-2 pending',
            m.gate2Pending,
            m.gate2Pending > 0 ? 'is-amber' : '',
          ],
          ['Videos total', m.videosPersisted, ''],
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
        {m.queue.map((it) => (
          <div
            key={it.id}
            className="svc"
            style={{
              gridTemplateColumns: 'auto 1fr auto auto',
              cursor: 'pointer',
            }}
            onClick={() => onOpenItem(it)}
          >
            <span className={`tag tag--${MEDIUM_TAG[it.medium]}`}>
              {it.medium}
            </span>
            <span
              className="svc__name truncate"
              style={{ fontSize: 11 }}
              title={it.title}
            >
              {it.title}
              <small>
                {it.dur} · Q{it.quality}
              </small>
            </span>
            <span className="svc__metric c-dim">{it.age}</span>
            <span
              className="act-item__acts"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                className="mbtn mbtn--primary"
                onClick={() => onApprove(it)}
              >
                <Icon name="check" size={11} />
              </button>
            </span>
          </div>
        ))}
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

Object.assign(window, { RevenuePanel, MediaPanel, QAPanel, LauncherPanel });

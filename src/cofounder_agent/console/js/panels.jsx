/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — domain panels.
   Each panel renders data + exposes inline actions / drill-in.
   ────────────────────────────────────────────────────────────── */

/* ─── KPI strip ─────────────────────────────────────────────── */
function KpiStrip({ kpis, onOpen }) {
  return (
    <div className="grid grid--kpi">
      {kpis.map((k) => {
        const toneCls =
          k.tone === 'amber'
            ? 'kpi--amber'
            : k.tone === 'alert'
              ? 'kpi--alert'
              : '';
        const valCls =
          k.tone === 'amber'
            ? 'is-amber'
            : k.tone === 'mint'
              ? 'is-mint'
              : k.tone === 'alert'
                ? 'is-red'
                : '';
        const sparkColor =
          k.tone === 'amber'
            ? 'var(--gl-amber)'
            : k.tone === 'alert'
              ? 'var(--gl-red)'
              : k.tone === 'mint'
                ? 'var(--gl-mint)'
                : 'var(--gl-cyan)';
        const deltaCls =
          k.delta > 0
            ? k.tone === 'alert'
              ? 'delta--down'
              : 'delta--up'
            : k.delta < 0
              ? 'delta--down'
              : 'delta--flat';
        return (
          <button
            key={k.id}
            className={`kpi ${toneCls}`}
            onClick={() => onOpen(k)}
          >
            <span className="kpi__label">{k.label}</span>
            <span className={`kpi__value ${valCls}`}>
              {k.unit === '$' && <span className="unit">$</span>}
              {typeof k.value === 'number' && k.value % 1 !== 0
                ? k.value.toFixed(
                    k.id === 'spend' || k.id === 'quality'
                      ? k.id === 'quality'
                        ? 1
                        : 2
                      : 1
                  )
                : k.value.toLocaleString()}
            </span>
            <div className="kpi__spark">
              <Sparkline data={k.spark} color={sparkColor} height={22} />
            </div>
            <span className="kpi__foot">
              <span className={`delta ${deltaCls}`}>{k.deltaLabel}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* ─── Action Inbox ──────────────────────────────────────────── */
const INBOX_ICON = {
  approve: 'doc',
  fail: 'kill',
  alert: 'bell',
  drift: 'link',
  media: 'play',
  social: 'pulse',
};
function ActionInbox({
  items,
  onOpen,
  onApprove,
  onReject,
  onRetry,
  onAck,
  onFix,
  onSocialApprove,
  onSocialReject,
  filter,
  setFilter,
}) {
  const counts = items.reduce(
    (a, i) => ((a[i.kind] = (a[i.kind] || 0) + 1), a),
    {}
  );
  const filtered =
    filter === 'all' ? items : items.filter((i) => i.kind === filter);
  const chips = [
    ['all', 'All', items.length],
    ['approve', 'Approvals', counts.approve || 0],
    ['media', 'Media', counts.media || 0],
    ['social', 'Social', counts.social || 0],
    ['fail', 'Failures', counts.fail || 0],
    ['alert', 'Alerts', counts.alert || 0],
    ['drift', 'Drift', counts.drift || 0],
  ];

  return (
    <section className="panel panel--amber" style={{ minHeight: 0 }}>
      <header className="panel__head">
        <span className="panel__title">
          <span className="idx">A1</span>NEEDS YOU
        </span>
        <span className="panel__spacer" />
        <span className="panel__meta">{items.length} OPEN</span>
      </header>
      <div
        style={{
          display: 'flex',
          gap: 6,
          padding: '8px 12px',
          borderBottom: '1px solid var(--gl-hairline)',
          flexWrap: 'wrap',
        }}
      >
        {chips.map(([id, lbl, n]) => (
          <button
            key={id}
            className={`tag ${filter === id ? 'tag--cyan' : ''}`}
            style={{
              cursor: 'pointer',
              background: filter === id ? 'var(--gl-cyan-bg)' : 'transparent',
            }}
            onClick={() => setFilter(id)}
          >
            {lbl} · {n}
          </button>
        ))}
      </div>
      <div
        className="panel__body panel__body--flush"
        style={{ overflowY: 'auto' }}
      >
        {filtered.length === 0 ? (
          <div className="empty">
            <span className="glyph" aria-hidden="true">
              ✓
            </span>
            Inbox zero. Nothing needs you right now.
          </div>
        ) : (
          filtered.map((it) => (
            <div
              key={it.id}
              className={`act-item act-item--${it.kind}`}
              onClick={() => onOpen(it)}
            >
              <span className="act-item__icon">
                <Icon name={INBOX_ICON[it.kind]} size={14} />
              </span>
              <div className="act-item__body">
                <div className="act-item__title">
                  {it.tags.map(([t, l], i) => (
                    <span key={i} className={`tag tag--${t}`}>
                      {l}
                    </span>
                  ))}
                  <span
                    className="truncate"
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {it.title}
                  </span>
                </div>
                <div className="act-item__sub">
                  {it.sub.map(([k, v], i) => (
                    <span key={i}>
                      <span className="c-dim">{k}</span> <b>{v}</b>
                    </span>
                  ))}
                  <span className="sep">·</span>
                  <span>{it.age}</span>
                </div>
              </div>
              <div
                className="act-item__acts"
                onClick={(e) => e.stopPropagation()}
              >
                {it.kind === 'approve' && (
                  <>
                    <button
                      className="mbtn mbtn--primary"
                      onClick={() => onApprove(it)}
                    >
                      <Icon name="check" size={12} />
                      Approve
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => onReject(it)}
                      title="Reject"
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </>
                )}
                {it.kind === 'fail' && (
                  <>
                    <button
                      className="mbtn mbtn--amber"
                      onClick={() => onRetry(it)}
                    >
                      <Icon name="retry" size={12} />
                      Retry
                    </button>
                    <button
                      className="mbtn mbtn--danger mbtn--ghost"
                      onClick={() => onReject(it)}
                      title="Kill"
                    >
                      <Icon name="kill" size={12} />
                    </button>
                  </>
                )}
                {it.kind === 'alert' && (
                  <button className="mbtn" onClick={() => onAck(it)}>
                    <Icon name="check" size={12} />
                    Ack
                  </button>
                )}
                {it.kind === 'drift' && (
                  <button
                    className="mbtn mbtn--amber"
                    onClick={() => onFix(it)}
                  >
                    <Icon name="bolt" size={12} />
                    Fix
                  </button>
                )}
                {it.kind === 'media' && (
                  <>
                    <button
                      className="mbtn mbtn--primary"
                      onClick={() => onApprove(it)}
                    >
                      <Icon name="check" size={12} />
                      Publish
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => onReject(it)}
                      title="Reject"
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </>
                )}
                {it.kind === 'social' && (
                  <>
                    <button
                      className="mbtn mbtn--primary"
                      onClick={() => onSocialApprove && onSocialApprove(it)}
                    >
                      <Icon name="check" size={12} />
                      Post
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => onSocialReject && onSocialReject(it)}
                      title="Reject"
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

/* ─── Service health grid ───────────────────────────────────── */
const LED = { ok: 'led-ok', warn: 'led-warn', err: 'led-err', off: 'led-off' };
function ServiceGrid({ services, onOpen, onRestart }) {
  const down = services.filter((s) => s.status === 'err').length;
  const warn = services.filter((s) => s.status === 'warn').length;
  return (
    <Panel
      idx="S1"
      title="SERVICE HEALTH"
      meta={`${services.length} CONTAINERS · ${down} DOWN · ${warn} DEGRADED`}
      flush
    >
      <div>
        {services.map((s) => (
          <div key={s.name} className="svc" onClick={() => onOpen(s)}>
            <span className={`svc__led ${LED[s.status]}`} title={s.status} />
            <span className="svc__name">
              {s.name}
              {s.port && <small>:{s.port}</small>}
              <small style={{ color: 'var(--gl-text-muted)' }}>{s.sub}</small>
            </span>
            <span className="svc__metric">{s.metric}</span>
            <span
              className="act-item__acts"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                className="mbtn mbtn--ghost"
                title="Restart"
                onClick={() => onRestart(s)}
              >
                <Icon name="retry" size={12} />
              </button>
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ─── GPU HUD ───────────────────────────────────────────────── */
function GpuHud({ gpu, onOpen }) {
  return (
    <Panel
      idx="G1"
      title="GPU · RTX 5090"
      meta={gpu.driver ? `DRIVER ${gpu.driver}` : 'LIVE · nvidia_gpu'}
      flush
      action="Detail"
      onAction={onOpen}
    >
      <div className="gauges">
        <ArcGauge value={gpu.util} max={100} label="Utilization" unit="%" />
        <ArcGauge
          value={gpu.temp}
          max={95}
          label="Temp"
          unit="°"
          warnAt={0.74}
          dangerAt={0.88}
        />
        <ArcGauge value={gpu.power} max={gpu.powerMax} label="Power" unit="W" />
      </div>
      <div
        className="gauges"
        style={{ borderTop: '1px solid var(--gl-hairline)' }}
      >
        <ArcGauge
          value={gpu.vramUsed}
          max={gpu.vramTotal}
          label="VRAM GB"
          warnAt={0.8}
          dangerAt={0.92}
        />
        <ArcGauge value={gpu.fan} max={100} label="Fan" unit="%" />
        <ArcGauge
          value={gpu.clock}
          max={gpu.clockMax}
          label="Clock MHz"
          warnAt={2}
          dangerAt={3}
        />
      </div>
    </Panel>
  );
}

/* ─── Pipeline panel ────────────────────────────────────────── */
const TASK_STATUS = { ok: 'ok', fail: 'err', run: 'run' };
function PipelinePanel({ pipeline, onOpen, onOpenTask, onRetry }) {
  return (
    <Panel
      idx="P1"
      title="CONTENT PIPELINE"
      meta={`SUCCESS ${pipeline.successRate}% · AVG ${pipeline.avgCompletion}`}
      flush
      action="Pipeline detail"
      onAction={onOpen}
    >
      <div
        style={{ padding: 12, borderBottom: '1px solid var(--gl-hairline)' }}
      >
        <div className="flow">
          {pipeline.stages.map((s) => (
            <div
              key={s.name}
              className={`flow__stage ${s.state}`}
              onClick={onOpen}
            >
              <div className="flow__stage-name">{s.name}</div>
              <div className="flow__stage-count">{s.count}</div>
            </div>
          ))}
        </div>
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th>Task</th>
            <th>Topic</th>
            <th>Stage</th>
            <th className="num">Q</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {pipeline.tasks.slice(0, 6).map((t) => (
            <tr key={t.id} onClick={() => onOpenTask(t)}>
              <td>
                <b>#{t.id}</b>
              </td>
              <td
                className="truncate"
                title={t.topic}
                style={{ color: 'var(--gl-text)' }}
              >
                {t.topic}
              </td>
              <td>
                <StatusText kind={TASK_STATUS[t.status]}>{t.stage}</StatusText>
              </td>
              <td className="num">{t.quality ?? '—'}</td>
              <td className="num" onClick={(e) => e.stopPropagation()}>
                {t.status === 'fail' ? (
                  <button
                    className="mbtn mbtn--amber"
                    onClick={() => onRetry(t)}
                  >
                    <Icon name="retry" size={11} />
                  </button>
                ) : (
                  <Icon
                    name="chevron"
                    size={13}
                    style={{ color: 'var(--gl-text-dim)' }}
                  />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

/* ─── Brain panel ───────────────────────────────────────────────
   Corpus headline (total + by-source) is the real /api/memory/stats read.
   decisions24h / decisions + knowledgeTotal come from /api/brain/stats
   (brain_routes.py). brain_queue was dropped 2026-04-21 — not referenced. */
function BrainPanel({ brain, onOpen, onEmbed }) {
  const decisionsKnown = brain.decisions24h != null;
  const sources = brain.bySource || [];
  const max = sources[0]?.[1] || 1;
  const decisions = brain.decisions || [];
  return (
    <Panel
      idx="B1"
      title="BRAIN · SEMANTIC MEMORY"
      meta={`${(brain.totalEmbeddings ?? 0).toLocaleString()} VECTORS`}
      flush
      action="Detail"
      onAction={onOpen}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 1,
          background: 'var(--gl-hairline)',
        }}
      >
        <div style={{ background: 'var(--gl-surface)', padding: 12 }}>
          <div className="kpi__label">Decisions (24h)</div>
          {decisionsKnown ? (
            <>
              <div
                className="kpi__value"
                style={{ fontSize: 30, color: 'var(--gl-text)' }}
              >
                {brain.decisions24h}
              </div>
              <div
                className="mono c-dim"
                style={{ fontSize: 10, marginTop: 4 }}
              >
                {brain.decisions7d} past 7d ·{' '}
                {brain.avgConfidence7d != null
                  ? `avg conf ${brain.avgConfidence7d}`
                  : 'conf n/a'}
              </div>
              <div
                className="mono c-dim"
                style={{ fontSize: 10, marginTop: 2 }}
              >
                knowledge {(brain.knowledgeTotal ?? 0).toLocaleString()} entries
              </div>
            </>
          ) : (
            <>
              <div
                className="kpi__value"
                style={{ fontSize: 30, color: 'var(--gl-text-muted)' }}
              >
                —
              </div>
              <div
                className="mono c-dim"
                style={{ fontSize: 10, marginTop: 4 }}
              >
                brain_decisions · loading…
              </div>
            </>
          )}
        </div>
        <div style={{ background: 'var(--gl-surface)', padding: 12 }}>
          <div className="kpi__label" style={{ marginBottom: 8 }}>
            By source
          </div>
          {sources.map(([k, v], i) => (
            <div key={k} style={{ marginBottom: 6 }}>
              <div
                className="mono"
                style={{
                  fontSize: 10,
                  display: 'flex',
                  justifyContent: 'space-between',
                  color: 'var(--gl-text-muted)',
                  marginBottom: 2,
                }}
              >
                <span>{k}</span>
                <span className="c-text tnum">{v.toLocaleString()}</span>
              </div>
              <Meter value={v} max={max} color={i === 0 ? '' : 'amber'} />
            </div>
          ))}
        </div>
      </div>
      <div
        style={{
          padding: '10px 12px',
          borderTop: '1px solid var(--gl-hairline)',
        }}
      >
        <div className="kpi__label" style={{ marginBottom: 8 }}>
          Recent decisions
        </div>
        {decisions.length > 0 ? (
          <div className="feed" style={{ padding: 0 }}>
            {decisions.slice(0, 4).map((d, i) => (
              <div key={i} className="feed__line" style={{ padding: '2px 0' }}>
                <span className="feed__ts">{d.ts}</span>
                <span className={`feed__tag c-${d.tone}`}>
                  {d.kind.toUpperCase()}
                </span>
                <span className="feed__msg">{d.msg}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="mono c-dim" style={{ fontSize: 10 }}>
            {PX.api.isLive()
              ? 'no decisions yet'
              : 'brain_decisions · mock mode'}
          </div>
        )}
      </div>
    </Panel>
  );
}

/* ─── Cost panel ────────────────────────────────────────────── */
function CostPanel({ cost, onOpen }) {
  const pct =
    cost.percentUsed != null
      ? cost.percentUsed
      : (cost.monthToDate / cost.budget) * 100;
  const warn = pct > 80 || cost.status === 'warning';
  const energyUsd =
    cost.energyKwhMonth != null
      ? cost.energyKwhMonth * cost.electricityRate
      : null;
  // The honest cost rows. Infra is $0 (self-hosted); the real levers are LLM/API
  // spend vs cap (the headline) + local energy. `energyKwhMonth === null` means
  // live mode with no backend read → explicit "backend read pending", never a
  // fabricated number (feedback_no_dummy_data).
  const rows = [
    ['Infra', '$0/mo', cost.infraNote],
    [
      'Energy',
      energyUsd != null ? `~$${energyUsd.toFixed(2)}/mo` : '— pending',
      energyUsd != null
        ? `${cost.energyKwhMonth} kWh × $${cost.electricityRate}/kWh`
        : 'cost_guard energy read not yet routed',
    ],
    [
      'Daily burn',
      `$${(cost.dailyBurn ?? 0).toFixed(2)}/day`,
      `projected $${cost.projected.toFixed(2)} this month`,
    ],
    [
      'Agent API',
      `$${(cost.agentApiMonth ?? 0).toFixed(2)}/mo`,
      cost.agentApiNote,
    ],
  ];
  return (
    <Panel
      idx="C1"
      title="COST CONTROL"
      meta={`PROJECTED $${cost.projected.toFixed(0)} / $${cost.budget}`}
      flush
      action="Detail"
      onAction={onOpen}
    >
      <div style={{ padding: 12 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 8,
            marginBottom: 4,
          }}
        >
          <span className="kpi__value" style={{ fontSize: 30 }}>
            <span className="unit" style={{ fontSize: 15 }}>
              $
            </span>
            {cost.monthToDate.toFixed(2)}
          </span>
          <span className="mono c-dim" style={{ fontSize: 11 }}>
            LLM/API spend · month · {pct.toFixed(0)}% of cap
          </span>
        </div>
        <Meter
          value={cost.monthToDate}
          max={cost.budget}
          color={warn ? 'amber' : 'mint'}
        />
        <div style={{ marginTop: 14 }}>
          {rows.map(([label, val, note]) => (
            <div
              key={label}
              className="svc"
              style={{
                gridTemplateColumns: '1fr auto',
                padding: '6px 0',
                borderColor: 'var(--gl-hairline)',
                cursor: 'default',
              }}
            >
              <span className="svc__name" style={{ fontSize: 11 }}>
                {label}
                <small>{note}</small>
              </span>
              <span className="svc__metric c-text">{val}</span>
            </div>
          ))}
        </div>
        <div
          className="mono c-mint"
          style={{
            fontSize: 10.5,
            marginTop: 10,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <span aria-hidden="true">✓</span> infra fully self-hosted — only
          energy + LLM/API spend cost real money
        </div>
      </div>
    </Panel>
  );
}

/* ─── Audit / live feed ─────────────────────────────────────── */
function AuditFeed({ lines, onOpen }) {
  return (
    <Panel
      idx="L1"
      title="EVENT STREAM"
      meta={
        <span>
          <span className="live-dot" style={{ marginRight: 6 }} />
          LIVE
        </span>
      }
      flush
      action="Audit log"
      onAction={onOpen}
    >
      <div className="feed" style={{ maxHeight: 280, overflowY: 'auto' }}>
        {lines.map((l, i) => (
          <div
            key={l.key || i}
            className={`feed__line ${l.fresh ? 'flash' : ''}`}
          >
            <span className="feed__ts">{l.ts}</span>
            <span className={`feed__tag c-${l.tag[0]}`}>{l.tag[1]}</span>
            <span
              className="feed__msg"
              dangerouslySetInnerHTML={{ __html: l.html }}
            />
          </div>
        ))}
      </div>
    </Panel>
  );
}

Object.assign(window, {
  KpiStrip,
  ActionInbox,
  ServiceGrid,
  GpuHud,
  PipelinePanel,
  BrainPanel,
  CostPanel,
  AuditFeed,
});

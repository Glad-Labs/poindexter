/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — view modes.
   FeedMode · SystemMap · WallDisplay. Share data + actions w/ console.
   ────────────────────────────────────────────────────────────── */

/* ═══ FEED MODE — one actionable timeline ═══════════════════ */
const FEED_ICON = {
  approve: 'doc',
  fail: 'kill',
  alert: 'bell',
  drift: 'link',
  media: 'play',
  social: 'pulse',
};
const FEED_VERB = {
  approve: 'needs approval',
  fail: 'failed',
  alert: 'raised an alert',
  drift: 'detected drift',
  media: 'rendered · needs review',
  social: 'social draft · needs approval',
};
const FEED_ACTOR = {
  approve: 'pipeline',
  fail: 'pipeline',
  alert: 'brain',
  drift: 'brain',
  media: 'media pipeline',
  social: 'social poster',
};

function FeedMode({ inbox, feed, filter, setFilter, onOpen, A }) {
  const chips = [
    ['all', 'All'],
    ['action', 'Needs action'],
    ['activity', 'Activity'],
  ];
  const showAction = filter !== 'activity';
  const showActivity = filter !== 'action';
  return (
    <div className="feedmode">
      <div className="feedmode__head">
        <span className="panel__title">
          <span className="idx">F</span>OPERATIONS FEED
        </span>
        <span className="panel__spacer" style={{ flex: 1 }} />
        <div className="feedmode__filters">
          {chips.map(([id, lbl]) => (
            <button
              key={id}
              className={`tag ${filter === id ? 'tag--cyan' : ''}`}
              style={{
                cursor: 'pointer',
                background: filter === id ? 'var(--gl-cyan-bg)' : 'transparent',
              }}
              onClick={() => setFilter(id)}
            >
              {lbl}
            </button>
          ))}
        </div>
      </div>

      {showAction &&
        inbox.map((it) => (
          <article key={it.id} className={`fcard fcard--act fcard--${it.kind}`}>
            <div className="fcard__rail">
              <span className="fcard__avatar">
                <Icon name={FEED_ICON[it.kind]} size={17} />
              </span>
              <span className="fcard__line" />
            </div>
            <div>
              <div className="fcard__head">
                <span className="fcard__actor">{FEED_ACTOR[it.kind]}</span>
                <span className="fcard__verb">{FEED_VERB[it.kind]}</span>
                {it.tags.map(([t, l], i) => (
                  <span key={i} className={`tag tag--${t}`}>
                    {l}
                  </span>
                ))}
                <span className="fcard__time">{it.age}</span>
              </div>
              <div
                className="fcard__title"
                style={{ cursor: 'pointer' }}
                onClick={() => onOpen(it)}
              >
                {it.title}
              </div>
              <div className="fcard__meta">
                {it.sub.map(([k, v], i) => (
                  <span key={i}>
                    <span className="c-dim">{k}</span> {v}
                  </span>
                ))}
              </div>
              <div className="fcard__acts">
                {it.kind === 'approve' && (
                  <>
                    <button
                      className="mbtn mbtn--primary"
                      onClick={() => A.approve(it)}
                    >
                      <Icon name="check" size={12} />
                      Approve & publish
                    </button>
                    <button className="mbtn" onClick={() => A.schedule(it)}>
                      Schedule
                    </button>
                    <button
                      className="mbtn mbtn--ghost mbtn--danger"
                      onClick={() => A.reject(it)}
                    >
                      <Icon name="x" size={12} />
                      Reject
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => onOpen(it)}
                    >
                      Read draft
                    </button>
                  </>
                )}
                {it.kind === 'fail' && (
                  <>
                    <button
                      className="mbtn mbtn--amber"
                      onClick={() => A.retry(it)}
                    >
                      <Icon name="retry" size={12} />
                      Retry
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => A.skipStage(it)}
                    >
                      Skip stage
                    </button>
                    <button
                      className="mbtn mbtn--ghost mbtn--danger"
                      onClick={() => A.kill(it)}
                    >
                      <Icon name="kill" size={12} />
                      Kill
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => onOpen(it)}
                    >
                      Trace
                    </button>
                  </>
                )}
                {it.kind === 'alert' && (
                  <>
                    <button
                      className="mbtn mbtn--primary"
                      onClick={() => A.ack(it)}
                    >
                      <Icon name="check" size={12} />
                      Acknowledge
                    </button>
                    <button
                      className="mbtn mbtn--amber"
                      onClick={() => A.runProbe(it)}
                    >
                      <Icon name="bolt" size={12} />
                      Re-run probe
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => A.snooze(it)}
                    >
                      Snooze 1h
                    </button>
                  </>
                )}
                {it.kind === 'drift' && (
                  <>
                    <button
                      className="mbtn mbtn--amber"
                      onClick={() => A.fix(it)}
                    >
                      <Icon name="bolt" size={12} />
                      Apply fix
                    </button>
                    <button
                      className="mbtn mbtn--ghost"
                      onClick={() => onOpen(it)}
                    >
                      View SQL
                    </button>
                  </>
                )}
                {it.kind === 'media' && (
                  <>
                    <button
                      className="mbtn mbtn--primary"
                      onClick={() => A.approve(it)}
                    >
                      <Icon name="check" size={12} />
                      Publish
                    </button>
                    <button
                      className="mbtn mbtn--ghost mbtn--danger"
                      onClick={() => A.reject(it)}
                    >
                      <Icon name="x" size={12} />
                      Reject
                    </button>
                  </>
                )}
                {it.kind === 'social' && (
                  <>
                    <button
                      className="mbtn mbtn--primary"
                      onClick={() => A.socialApproveDraft(it)}
                    >
                      <Icon name="check" size={12} />
                      Post
                    </button>
                    <button
                      className="mbtn mbtn--ghost mbtn--danger"
                      onClick={() => A.socialRejectDraft(it)}
                    >
                      <Icon name="x" size={12} />
                      Reject
                    </button>
                  </>
                )}
              </div>
            </div>
          </article>
        ))}

      {showAction && inbox.length === 0 && (
        <div
          className="empty"
          style={{
            border: '1px solid var(--gl-hairline)',
            background: 'var(--gl-surface)',
          }}
        >
          <span className="glyph" aria-hidden="true">
            ✓
          </span>
          Inbox zero — nothing needs you.
        </div>
      )}

      {showActivity && (
        <>
          {showAction && (
            <div className="cmdk__group" style={{ padding: '14px 2px 6px' }}>
              ACTIVITY
            </div>
          )}
          {feed.map((l, i) => (
            <article
              key={l.key || i}
              className={`fcard ${l.fresh ? 'flash' : ''}`}
              style={{ gridTemplateColumns: '36px 1fr' }}
            >
              <div className="fcard__rail">
                <span
                  className="fcard__avatar"
                  style={{ width: 28, height: 28 }}
                >
                  <Icon name="pulse" size={13} />
                </span>
              </div>
              <div>
                <div className="fcard__head">
                  <span className={`fcard__actor c-${l.tag[0]}`}>
                    {l.tag[1]}
                  </span>
                  <span className="fcard__time">{l.ts}</span>
                </div>
                <div
                  className="fcard__meta"
                  style={{ fontSize: 12, color: 'var(--gl-text-muted)' }}
                  dangerouslySetInnerHTML={{ __html: l.html }}
                />
              </div>
            </article>
          ))}
        </>
      )}
    </div>
  );
}

/* ═══ SYSTEM MAP — live node graph ══════════════════════════ */
// Keys MUST match the `name` field of the entries in data.js `services` —
// SystemMap looks each node up via svcByName[name] for its live status. (The
// 2026-06 service rename dropped the `poindexter-` display prefix; the real
// cAdvisor container name lives on `s.container`, used by the health query.)
const MAP_NODES = [
  { key: 'worker', x: 50, y: 48, core: true },
  { key: 'postgres-local', x: 22, y: 26 },
  { key: 'ollama', x: 76, y: 24 },
  { key: 'image-gen-server', x: 84, y: 52 },
  { key: 'brain-daemon', x: 50, y: 16 },
  { key: 'prometheus', x: 20, y: 70 },
  { key: 'loki', x: 38, y: 84 },
  { key: 'tempo', x: 62, y: 84 },
  { key: 'prefect-server', x: 80, y: 78 },
  { key: 'glitchtip-web', x: 16, y: 48 },
  { key: 'gpu', x: 88, y: 36, gpu: true },
];
const MAP_EDGES = [
  ['worker', 'postgres-local', 'hot'],
  ['worker', 'ollama', 'hot'],
  ['worker', 'loki', 'hot'],
  ['worker', 'tempo', 'hot'],
  ['brain-daemon', 'worker', 'hot'],
  ['brain-daemon', 'prometheus', ''],
  ['ollama', 'gpu', 'hot'],
  ['image-gen-server', 'gpu', 'amber'],
  ['prometheus', 'worker', ''],
  ['prefect-server', 'worker', 'err'],
  ['worker', 'glitchtip-web', ''],
  ['worker', 'image-gen-server', 'hot'],
];

function SystemMap({ services, gpu, onOpen, onOpenGpu, onRestart }) {
  const wrapRef = React.useRef(null);
  const [box, setBox] = React.useState({ w: 1200, h: 640 });
  React.useEffect(() => {
    const update = () => {
      if (wrapRef.current) {
        const r = wrapRef.current.getBoundingClientRect();
        setBox({ w: r.width, h: r.height });
      }
    };
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);
  const svcByName = React.useMemo(
    () => Object.fromEntries(services.map((s) => [s.name, s])),
    [services]
  );
  const nodeByKey = Object.fromEntries(MAP_NODES.map((n) => [n.key, n]));
  const pos = (n) => ({ x: (n.x / 100) * box.w, y: (n.y / 100) * box.h });

  const gpuNode = {
    name: 'RTX 5090',
    status: gpu.util > 90 ? 'warn' : 'ok',
    metric: `${gpu.util}% · ${gpu.temp}°C · ${gpu.power}W`,
    sub: 'GPU',
  };

  return (
    <div className="mapwrap" ref={wrapRef}>
      <svg
        className="map-svg"
        viewBox={`0 0 ${box.w} ${box.h}`}
        preserveAspectRatio="none"
      >
        {MAP_EDGES.map(([a, b, kind], i) => {
          const pa = pos(nodeByKey[a]),
            pb = pos(nodeByKey[b]);
          const mx = (pa.x + pb.x) / 2,
            my = (pa.y + pb.y) / 2 - 18;
          const d = `M${pa.x},${pa.y} Q${mx},${my} ${pb.x},${pb.y}`;
          const svcA = svcByName[a],
            svcB = svcByName[b];
          const isErr =
            kind === 'err' ||
            (svcB && svcB.status === 'err') ||
            (svcA && svcA.status === 'err');
          return (
            <g key={i}>
              <path
                className={`map-edge ${kind === 'hot' ? 'hot' : ''}`}
                d={d}
              />
              <path
                className={`map-flow ${isErr ? 'err' : kind === 'amber' ? 'amber' : ''}`}
                d={d}
              />
            </g>
          );
        })}
      </svg>

      {MAP_NODES.map((n) => {
        const p = pos(n);
        const svc = n.gpu ? gpuNode : svcByName[n.key];
        if (!svc) return null;
        const st = svc.status;
        return (
          <div
            key={n.key}
            className={`map-node ${st} ${n.core ? 'core' : ''}`}
            style={{ left: p.x, top: p.y }}
            onClick={() => (n.gpu ? onOpenGpu() : onOpen(svc))}
          >
            <div className="map-node__top">
              <span
                className={`map-node__led ${{ ok: 'led-ok', warn: 'led-warn', err: 'led-err' }[st] || 'led-off'}`}
              />
              <span className="map-node__name">
                {n.gpu ? 'RTX 5090' : svc.name.replace('poindexter-', '')}
              </span>
            </div>
            <div className="map-node__metric">{svc.metric}</div>
            {st === 'err' && (
              <button
                className="mbtn mbtn--ghost"
                style={{ marginTop: 7, padding: '4px 8px', fontSize: 9 }}
                onClick={(e) => {
                  e.stopPropagation();
                  onRestart(svc);
                }}
              >
                <Icon name="retry" size={10} />
                Restart
              </button>
            )}
          </div>
        );
      })}

      <div className="map-hint">
        CLICK A NODE TO INSPECT · ANIMATED EDGES = LIVE DATA FLOW
      </div>
      <div className="map-legend">
        <span>
          <i className="led-ok" />
          healthy
        </span>
        <span>
          <i className="led-warn" />
          degraded
        </span>
        <span>
          <i className="led-err" />
          down
        </span>
        <span>
          <i style={{ background: 'var(--gl-cyan)' }} />
          flow
        </span>
      </div>
    </div>
  );
}

/* ═══ WALL DISPLAY — ambient always-on HUD ══════════════════ */
function WallDisplay({
  kpis,
  gpu,
  pipeline,
  brain,
  services,
  inbox,
  clock,
  sysState,
  revenue,
}) {
  const get = (id) => kpis.find((k) => k.id === id) || {};
  const inFlight = pipeline.tasks.filter((t) => t.status === 'run').length;
  const down = services.filter((s) => s.status === 'err');
  const top = inbox[0];
  const date = 'MON 08 JUN 2026';

  const cells = [
    {
      label: 'Revenue MTD',
      val: '$' + (revenue ? revenue.month.toLocaleString() : '0'),
      foot: 'this month',
      tone: 'mint',
    },
    {
      label: 'Published 30d',
      val: get('published').value,
      foot: '+6 vs prev',
      tone: '',
    },
    {
      label: 'Avg Quality 7d',
      val: get('quality').value,
      foot: 'target ≥ 80',
      tone: 'mint',
    },
    {
      label: 'GPU Util',
      val: gpu.util,
      unit: '%',
      foot: `${gpu.temp}°C · ${gpu.power}W`,
      tone: gpu.util > 90 ? 'amber' : '',
    },
    {
      label: 'Spend MTD',
      val: '$' + get('spend').value.toFixed(2),
      foot: 'of $50 budget',
      tone: '',
    },
    { label: 'In Flight', val: inFlight, foot: 'pipeline tasks', tone: '' },
    {
      // queueDepth (brain_queue) has no HTTP route — null in live → honest '—'.
      label: 'Embed Queue',
      val: brain.queueDepth == null ? '—' : brain.queueDepth,
      foot:
        brain.queueDepth == null
          ? 'no HTTP route'
          : brain.queueDepth > 15
            ? 'behind'
            : 'nominal',
      tone: brain.queueDepth > 15 ? 'amber' : 'mint',
    },
    {
      label: 'Failed 24h',
      val: get('failed').value,
      foot: 'tasks',
      tone: get('failed').value > 0 ? 'red' : 'mint',
    },
  ];

  return (
    <div className="wall">
      <div className="wall__hero">
        <div className="wall__status">
          <div className="wall__status-eyebrow">
            // POINDEXTER · SYSTEM STATUS
          </div>
          <div className={`wall__status-big ${sysState[0]}`}>
            {sysState[0] === 'ok'
              ? '✓ NOMINAL'
              : sysState[0] === 'warn'
                ? '⚠ ATTENTION'
                : '✕ DEGRADED'}
          </div>
          <div className="wall__status-sub">
            {down.length > 0
              ? `${down.map((s) => s.name).join(', ')} down · `
              : ''}
            {services.filter((s) => s.status === 'ok').length}/{services.length}{' '}
            services healthy · {inbox.length} open items
          </div>
        </div>
        <div className="wall__clock">
          <div className="wall__clock-time">{clock}</div>
          <div className="wall__clock-date">{date}</div>
          <div className="wall__status-sub" style={{ marginTop: '1.4vh' }}>
            <span className="live-dot" style={{ marginRight: 8 }} />
            LIVE · 5m SYNC
          </div>
        </div>
      </div>

      <div className={`wall__attention ${top ? '' : 'ok'}`}>
        <span className="wall__attention-glyph" aria-hidden="true">
          {top ? (top.kind === 'fail' ? '✕' : '⚠') : '✓'}
        </span>
        <div className="wall__attention-txt">
          {top ? (
            <>
              <div className="wall__attention-title">{top.title}</div>
              <div className="wall__attention-sub">
                {FEED_ACTOR[top.kind]} · {FEED_VERB[top.kind]} · {top.age} ·{' '}
                {inbox.length - 1} more in queue
              </div>
            </>
          ) : (
            <>
              <div className="wall__attention-title">
                Inbox zero — nothing needs you
              </div>
              <div className="wall__attention-sub">
                All approvals cleared · no failures · no unacked alerts
              </div>
            </>
          )}
        </div>
      </div>

      <div className="wall__grid">
        {cells.map((c, i) => (
          <div key={i} className="wall__cell">
            <div className="wall__cell-label">{c.label}</div>
            <div className={`wall__cell-val ${c.tone}`}>
              {c.val}
              {c.unit && <small>{c.unit}</small>}
            </div>
            <div className="wall__cell-foot">{c.foot}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { FeedMode, SystemMap, WallDisplay });

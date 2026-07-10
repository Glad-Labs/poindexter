/* ──────────────────────────────────────────────────────────────
   NOW RUNNING band — live activity hero (design 1a).
   Answers "what is the system doing right now" at a glance:
   in-flight pipeline tasks, media renders, and system vitals.

   Mock: PX.pipeline.tasks (status 'run') + PX.mediaRendering.
   Live: tasks come from the same /api/tasks poll the Pipeline
   panel uses (no new read); media render PROGRESS has no HTTP
   route yet → honest-empty with the gate-2 pending count
   (feedback_no_dummy_data). Wire a renders feed here when a
   render-progress read lands on the worker.
   ────────────────────────────────────────────────────────────── */
function NowRunningBand({ pipeline, renders, gpu, media, brain, onOpenTask }) {
  const running = (pipeline.tasks || []).filter((t) => t.status === 'run');
  const stages = pipeline.stages || [];
  const nBlocks = stages.length || 6;
  const nodeToIdx = {};
  stages.forEach((b, i) => (b.nodes || []).forEach((n) => (nodeToIdx[n] = i)));
  const blockOf = (stage) => {
    const i = nodeToIdx[stage];
    return i == null ? null : (stages[i] || {}).name;
  };
  const pctFor = (stage) => {
    const i = nodeToIdx[stage];
    return i == null ? 8 : Math.round(((i + 0.5) / nBlocks) * 100);
  };
  const live = window.PX.api.isLive();
  const gate2 = media && media.gate2Pending;
  return (
    <section className="nowrun" aria-label="Now running">
      <div className="nowrun__scan">
        <i />
      </div>
      <header className="nowrun__head">
        <span className="nowrun__dot" />
        <span className="nowrun__title">NOW RUNNING</span>
        <span className="nowrun__sum">
          {running.length} task{running.length === 1 ? '' : 's'} in flight
          {renders.length ? ` · ${renders.length} media rendering` : ''}
          {' · GPU '}
          {gpu.util}%
        </span>
        <span className="nowrun__eq" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
          <i />
        </span>
      </header>
      <div className="nowrun__grid">
        <div className="nowrun__col">
          <div className="nowrun__collabel">CONTENT PIPELINE</div>
          {running.length === 0 && (
            <div className="nowrun__empty">— idle · no tasks in flight</div>
          )}
          {running.map((t) => (
            <div
              key={t.id}
              className="nowrun__job"
              onClick={() => onOpenTask && onOpenTask(t)}
            >
              <div className="nowrun__jobtop">
                <Icon
                  name="doc"
                  size={13}
                  style={{ color: 'var(--gl-cyan)', flex: 'none' }}
                />
                <span className="nowrun__jobtitle">{t.topic}</span>
                <span className="nowrun__age">{t.age}</span>
              </div>
              <div className="nowrun__jobmeta">
                <span className="nowrun__stage">
                  {blockOf(t.stage) ? blockOf(t.stage) + ' · ' : ''}
                  {t.stage}
                </span>
                <span className="nowrun__dimmeta">
                  #{t.id} · {t.model}
                </span>
              </div>
              <div className="nowrun__bar">
                <div
                  className="nowrun__fill"
                  style={{ width: pctFor(t.stage) + '%' }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="nowrun__col">
          <div className="nowrun__collabel nowrun__collabel--amber">
            MEDIA RENDER · VIDEO &amp; PODCAST
          </div>
          {renders.length === 0 && (
            <div className="nowrun__empty">
              {live
                ? `— · no render-progress route yet${
                    gate2 ? ` · ${gate2} rendered await gate-2` : ''
                  }`
                : '— idle · nothing rendering'}
            </div>
          )}
          {renders.map((r) => (
            <div key={r.id} className="nowrun__job nowrun__job--media">
              <div className="nowrun__jobtop">
                <Icon
                  name="play"
                  size={13}
                  style={{ color: 'var(--gl-amber)', flex: 'none' }}
                />
                <span className="nowrun__jobtitle">{r.title}</span>
                <span className="nowrun__age nowrun__age--amber">{r.age}</span>
              </div>
              <div className="nowrun__jobmeta">
                <span className="nowrun__stage nowrun__stage--amber">
                  {(r.medium || '').toUpperCase()} · {r.step}
                </span>
                <span className="nowrun__dimmeta">{r.eta}</span>
              </div>
              <div className="nowrun__bar">
                <div
                  className="nowrun__fill nowrun__fill--amber"
                  style={{ width: r.pct + '%' }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="nowrun__col nowrun__vitals">
          <div className="nowrun__collabel">SYSTEM VITALS</div>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: 5,
            }}
          >
            <span
              style={{
                fontFamily: 'var(--gl-font-mono)',
                fontSize: 10,
                color: 'var(--gl-text-muted)',
                letterSpacing: '.1em',
              }}
            >
              GPU · {gpu.name}
            </span>
            <span
              style={{
                fontFamily: 'var(--gl-font-display)',
                fontWeight: 700,
                fontSize: 18,
                color: 'var(--gl-cyan)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {gpu.util}
              <span style={{ fontSize: 11, color: 'var(--gl-text-muted)' }}>
                %
              </span>
            </span>
          </div>
          <div className="nowrun__bar" style={{ height: 5 }}>
            <div className="nowrun__fill" style={{ width: gpu.util + '%' }} />
          </div>
          <div
            className="nowrun__vrow"
            style={{ color: 'var(--gl-text-dim)', fontSize: 10 }}
          >
            <span>
              {gpu.temp}°C · {gpu.power}W
            </span>
            <span>
              VRAM {gpu.vramUsed}/{gpu.vramTotal} GB
            </span>
          </div>
          <div
            style={{
              height: 1,
              background: 'var(--gl-hairline)',
              margin: '7px 0',
            }}
          />
          <div className="nowrun__vrow">
            <span style={{ color: 'var(--gl-text-muted)' }}>
              Tasks in flight
            </span>
            <span className="tnum" style={{ color: 'var(--gl-text)' }}>
              {running.length}
            </span>
          </div>
          <div className="nowrun__vrow">
            <span style={{ color: 'var(--gl-text-muted)' }}>
              Media rendering
            </span>
            <span className="tnum" style={{ color: 'var(--gl-amber)' }}>
              {renders.length}
            </span>
          </div>
          <div className="nowrun__vrow">
            <span style={{ color: 'var(--gl-text-muted)' }}>Embed queue</span>
            <span className="tnum" style={{ color: 'var(--gl-text)' }}>
              {brain.queueDepth != null ? brain.queueDepth : '—'}
            </span>
          </div>
          <div className="nowrun__vrow">
            <span style={{ color: 'var(--gl-text-muted)' }}>
              Last brain cycle
            </span>
            <span style={{ color: 'var(--gl-mint)' }}>
              ✓ {brain.lastCycle || '—'}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
window.NowRunningBand = NowRunningBand;

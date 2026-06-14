/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — drill-in detail drawer.
   Verbose readout + action footer, dispatched by entity type.
   ────────────────────────────────────────────────────────────── */

function DL({ rows }) {
  return (
    <dl className="dl">
      {rows.map(([k, v], i) => (
        <React.Fragment key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

function Drawer({ entity, onClose, actions }) {
  const open = !!entity;
  // Reject-with-feedback sub-state for the approve drawer. Two-gate model:
  // Approve STAGES the post; Reject sends it back to edit with operator notes.
  const [rejectOpen, setRejectOpen] = useState(false);
  const [feedback, setFeedback] = useState('');
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  // Reset the reject panel whenever the drawer target changes.
  useEffect(() => {
    setRejectOpen(false);
    setFeedback('');
  }, [entity]);

  let title = '',
    eyebrow = '',
    body = null,
    foot = null;

  if (entity) {
    const e = entity.data;
    switch (entity.type) {
      case 'inbox': {
        const d = e.detail || {};
        if (e.kind === 'approve') {
          eyebrow = 'CONTENT · AWAITING APPROVAL';
          title = e.title;
          body = (
            <>
              {d.featured_image_url ? (
                <>
                  <div className="section-label">Featured image</div>
                  <img
                    src={d.featured_image_url}
                    alt=""
                    loading="lazy"
                    style={{
                      width: '100%',
                      borderRadius: 8,
                      border: '1px solid var(--gl-hairline-strong)',
                      marginBottom: 12,
                      display: 'block',
                    }}
                  />
                </>
              ) : null}
              <div className="section-label">Draft preview</div>
              <div className="preview">
                <h4>{e.title}</h4>
                <p>
                  {d.excerpt || (
                    <span className="c-dim">No preview available.</span>
                  )}
                </p>
                {d.topic ? (
                  <p className="mono c-dim" style={{ fontSize: 10 }}>
                    {d.topic}
                    {e.age ? ` · ${e.age}` : ''}
                  </p>
                ) : null}
              </div>
              {d.quality != null ? (
                <>
                  <div className="section-label">
                    Quality · {Math.round(d.quality)}/100
                  </div>
                  {Array.isArray(d.breakdown) && d.breakdown.length ? (
                    d.breakdown.map(([k, v]) => (
                      <div key={k} style={{ marginBottom: 8 }}>
                        <div
                          className="mono"
                          style={{
                            fontSize: 11,
                            display: 'flex',
                            justifyContent: 'space-between',
                            marginBottom: 3,
                          }}
                        >
                          <span className="c-muted">{k}</span>
                          <span className="c-text tnum">{v}</span>
                        </div>
                        <Meter
                          value={v}
                          max={100}
                          color={v >= 80 ? 'mint' : 'amber'}
                        />
                      </div>
                    ))
                  ) : (
                    <Meter
                      value={Math.round(d.quality)}
                      max={100}
                      color={d.quality >= 80 ? 'mint' : 'amber'}
                    />
                  )}
                </>
              ) : null}
              <div className="section-label">Routing</div>
              <DL
                rows={[
                  ['Topic', d.topic || '—'],
                  ['Stage', d.pipeline || 'awaiting_approval'],
                  ['Task', d.task || e.id],
                ]}
              />
              {rejectOpen ? (
                <div className="field" style={{ marginTop: 14 }}>
                  <label>Reason for sending back</label>
                  <textarea
                    autoFocus
                    rows={3}
                    value={feedback}
                    onChange={(ev) => setFeedback(ev.target.value)}
                    placeholder="What needs to change before this can be approved?"
                    style={{ resize: 'vertical' }}
                  />
                </div>
              ) : null}
            </>
          );
          foot = rejectOpen ? (
            <>
              <button
                className="mbtn mbtn--danger"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                onClick={() => {
                  e.detail = { ...(e.detail || {}), feedback };
                  actions.reject(e);
                }}
              >
                <Icon name="x" size={13} />
                Send back to edit
              </button>
              <button
                className="mbtn mbtn--ghost"
                style={{ padding: '10px 14px' }}
                onClick={() => {
                  setRejectOpen(false);
                  setFeedback('');
                }}
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                className="mbtn mbtn--primary"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                title="Stage for publish — does not go live"
                onClick={() => actions.approve(e)}
              >
                <Icon name="check" size={13} />
                Approve
              </button>
              <button
                className="mbtn"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.schedule(e)}
              >
                Schedule
              </button>
              <button
                className="mbtn mbtn--ghost mbtn--danger"
                style={{ padding: '10px 14px' }}
                onClick={() => setRejectOpen(true)}
              >
                <Icon name="x" size={13} />
                Reject
              </button>
            </>
          );
        } else if (e.kind === 'fail') {
          eyebrow = 'PIPELINE · FAILED TASK';
          title = `Task #${d.task}`;
          body = (
            <>
              <div className="section-label">Failure</div>
              <div
                className="preview"
                style={{ borderLeftColor: 'var(--gl-red)' }}
              >
                <p className="mono c-red" style={{ margin: 0, fontSize: 12 }}>
                  {d.error}
                </p>
              </div>
              <div className="section-label">Trace</div>
              <div className="gl-log" style={{ fontSize: 11 }}>
                {d.trace}
              </div>
              <div className="section-label">Context</div>
              <DL
                rows={[
                  ['Topic', d.topic],
                  ['Stage', d.stage],
                  ['Retries', `${d.retries} / 3`],
                  ['Last run', d.lastRun],
                ]}
              />
            </>
          );
          foot = (
            <>
              <button
                className="mbtn mbtn--amber"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                onClick={() => actions.retry(e)}
              >
                <Icon name="retry" size={13} />
                Retry task
              </button>
              <button
                className="mbtn"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.skipStage(e)}
              >
                Skip stage
              </button>
              <button
                className="mbtn mbtn--danger mbtn--ghost"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.kill(e)}
              >
                <Icon name="kill" size={13} />
                Kill
              </button>
            </>
          );
        } else if (e.kind === 'alert') {
          eyebrow = `ALERT · ${d.severity?.toUpperCase()}`;
          title = e.title;
          body = (
            <>
              <div className="section-label">Detail</div>
              <p className="gl-body" style={{ fontSize: 13, marginTop: 0 }}>
                {d.detail}
              </p>
              <div className="section-label">Recommended</div>
              <div className="preview">
                <p style={{ margin: 0 }}>{d.recommend}</p>
              </div>
              <div className="section-label">Source</div>
              <DL
                rows={[
                  ['Probe', d.probe],
                  ['Origin', d.source],
                  ['Severity', d.severity],
                  ['First seen', d.firstSeen],
                ]}
              />
            </>
          );
          foot = (
            <>
              <button
                className="mbtn mbtn--primary"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                onClick={() => actions.ack(e)}
              >
                <Icon name="check" size={13} />
                Acknowledge
              </button>
              <button
                className="mbtn mbtn--amber"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.runProbe(e)}
              >
                <Icon name="bolt" size={13} />
                Re-run probe
              </button>
              <button
                className="mbtn mbtn--ghost"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.snooze(e)}
              >
                Snooze 1h
              </button>
            </>
          );
        } else if (e.kind === 'drift') {
          eyebrow = 'OPERATOR URL DRIFT';
          title = d.surface;
          body = (
            <>
              <div className="section-label">Failure</div>
              <DL
                rows={[
                  [
                    'URL',
                    <span
                      className="c-amber"
                      style={{ wordBreak: 'break-all' }}
                    >
                      {d.url}
                    </span>,
                  ],
                  ['Error', <span className="c-red">{d.error}</span>],
                  ['First seen', d.firstSeen],
                ]}
              />
              <div className="section-label">Recommended fix</div>
              <p className="gl-body" style={{ fontSize: 13, marginTop: 0 }}>
                {d.recommend}
              </p>
              <div className="gl-log" style={{ fontSize: 11 }}>
                <span className="c-mint">$</span> {d.fix}
              </div>
            </>
          );
          foot = (
            <>
              <button
                className="mbtn mbtn--primary"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                onClick={() => actions.fix(e)}
              >
                <Icon name="bolt" size={13} />
                Apply fix
              </button>
              <button
                className="mbtn"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.runProbe(e)}
              >
                Re-probe
              </button>
            </>
          );
        } else if (e.kind === 'media') {
          eyebrow = `MEDIA · ${(e.detail.medium || '').toUpperCase()} · GATE 2`;
          title = e.detail.title;
          body = (
            <>
              <div className="section-label">Rendered asset</div>
              <div
                className="preview"
                style={{ borderLeftColor: 'var(--gl-mint)' }}
              >
                <h4 style={{ fontSize: 16 }}>{e.detail.title}</h4>
                <p className="mono c-dim" style={{ margin: 0, fontSize: 11 }}>
                  {e.detail.medium} · {e.detail.dur} · quality{' '}
                  {e.detail.quality}
                  {e.detail.shots ? ` · ${e.detail.shots} shots` : ''}
                </p>
              </div>
              <div className="section-label">Pipeline</div>
              <DL
                rows={[
                  ['Medium', e.detail.medium],
                  ['Duration', e.detail.dur],
                  ['Quality', e.detail.quality],
                  ['Stage', e.detail.stage],
                  ['Shot list', e.detail.shots || '—'],
                ]}
              />
            </>
          );
          foot = (
            <>
              <button
                className="mbtn mbtn--primary"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                onClick={() => {
                  actions.mediaApprove(e.detail);
                  onClose();
                }}
              >
                <Icon name="check" size={13} />
                Publish to channel
              </button>
              <button
                className="mbtn mbtn--danger mbtn--ghost"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.reject(e)}
              >
                <Icon name="x" size={13} />
                Reject
              </button>
            </>
          );
        }
        break;
      }
      case 'service': {
        eyebrow = 'CONTAINER';
        title = e.name;
        body = (
          <>
            <div className="section-label">Status</div>
            <DL
              rows={[
                [
                  'State',
                  <StatusText
                    kind={
                      e.status === 'ok'
                        ? 'ok'
                        : e.status === 'warn'
                          ? 'warn'
                          : 'err'
                    }
                  >
                    {e.status === 'ok'
                      ? 'healthy'
                      : e.status === 'warn'
                        ? 'degraded'
                        : 'down'}
                  </StatusText>,
                ],
                ['Image', e.img],
                ['Port', e.port || '—'],
                ['Uptime', e.uptime],
                ['Last probe', e.probe],
              ]}
            />
            <div className="section-label">Resources</div>
            <div style={{ marginBottom: 10 }}>
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  display: 'flex',
                  justifyContent: 'space-between',
                  marginBottom: 3,
                }}
              >
                <span className="c-muted">CPU</span>
                <span className="c-text tnum">{e.cpu}%</span>
              </div>
              <Meter
                value={e.cpu}
                max={100}
                color={e.cpu > 80 ? 'amber' : ''}
              />
            </div>
            <div>
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  display: 'flex',
                  justifyContent: 'space-between',
                  marginBottom: 3,
                }}
              >
                <span className="c-muted">Memory</span>
                <span className="c-text tnum">{e.mem} MB</span>
              </div>
              <Meter
                value={e.mem}
                max={12000}
                color={e.mem > 9000 ? 'amber' : ''}
              />
            </div>
          </>
        );
        foot = (
          <>
            <button
              className="mbtn mbtn--primary"
              style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
              onClick={() => actions.restart(e)}
            >
              <Icon name="retry" size={13} />
              Restart
            </button>
            <button
              className="mbtn"
              style={{ padding: '10px 14px' }}
              onClick={() => actions.probe(e)}
            >
              <Icon name="bolt" size={13} />
              Probe
            </button>
            <button
              className="mbtn mbtn--ghost"
              style={{ padding: '10px 14px' }}
              onClick={() => actions.logs(e)}
            >
              Logs
            </button>
          </>
        );
        break;
      }
      case 'gpu': {
        const g = e;
        eyebrow = 'HARDWARE';
        title = `GPU · ${g.name}`;
        body = (
          <>
            <div className="section-label">Live telemetry</div>
            <DL
              rows={[
                ['Utilization', g.util + '%'],
                ['Temperature', g.temp + '°C'],
                ['Power', `${g.power} / ${g.powerMax} W`],
                ['VRAM', `${g.vramUsed} / ${g.vramTotal} GB`],
                ['Fan', g.fan + '%'],
                ['Clock', `${g.clock} / ${g.clockMax} MHz`],
                ['Driver', g.driver],
              ]}
            />
            <div className="section-label">Utilization · last hour</div>
            <div
              style={{
                background: 'var(--gl-surface)',
                padding: '12px 10px',
                border: '1px solid var(--gl-hairline)',
              }}
            >
              <Sparkline data={g.utilHist} color="var(--gl-cyan)" height={64} />
            </div>
            <div className="section-label">VRAM by process</div>
            {g.procs.map((p) => (
              <div key={p.name} style={{ marginBottom: 9 }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    display: 'flex',
                    justifyContent: 'space-between',
                    marginBottom: 3,
                  }}
                >
                  <span className="c-muted">{p.name}</span>
                  <span className="c-text tnum">{p.vram} GB</span>
                </div>
                <Meter value={p.vram} max={g.vramTotal} />
              </div>
            ))}
          </>
        );
        foot = (
          <button
            className="mbtn mbtn--ghost"
            style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
            onClick={onClose}
          >
            Close
          </button>
        );
        break;
      }
      case 'task': {
        eyebrow = `PIPELINE · ${e.stage.toUpperCase()}`;
        title = `Task #${e.id}`;
        body = (
          <>
            <div className="section-label">Task</div>
            <div
              className="preview"
              style={{ borderLeftColor: 'var(--gl-cyan)' }}
            >
              <h4 style={{ fontSize: 15 }}>{e.topic}</h4>
            </div>
            <div className="section-label">State</div>
            <DL
              rows={[
                ['Stage', e.stage],
                [
                  'Status',
                  <StatusText
                    kind={{ ok: 'ok', fail: 'err', run: 'run' }[e.status]}
                  >
                    {e.status}
                  </StatusText>,
                ],
                ['Quality', e.quality ?? '—'],
                ['Model', e.model],
                ['Age', e.age],
              ]}
            />
          </>
        );
        foot =
          e.status === 'fail' ? (
            <>
              <button
                className="mbtn mbtn--amber"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                onClick={() => actions.retry(e)}
              >
                <Icon name="retry" size={13} />
                Retry
              </button>
              <button
                className="mbtn mbtn--danger mbtn--ghost"
                style={{ padding: '10px 14px' }}
                onClick={() => actions.kill(e)}
              >
                <Icon name="kill" size={13} />
                Kill
              </button>
            </>
          ) : e.status === 'run' ? (
            <>
              <button
                className="mbtn mbtn--danger mbtn--ghost"
                style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
                onClick={() => actions.kill(e)}
              >
                <Icon name="kill" size={13} />
                Cancel task
              </button>
              <button
                className="mbtn mbtn--ghost"
                style={{ padding: '10px 14px' }}
                onClick={onClose}
              >
                Close
              </button>
            </>
          ) : (
            <button
              className="mbtn mbtn--ghost"
              style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
              onClick={onClose}
            >
              Close
            </button>
          );
        break;
      }
      case 'pipeline': {
        const p = e;
        eyebrow = 'PIPELINE OVERVIEW';
        title = 'Content Pipeline';
        body = (
          <>
            <div className="section-label">Throughput · last 30 days</div>
            <div
              style={{
                background: 'var(--gl-surface)',
                padding: 12,
                border: '1px solid var(--gl-hairline)',
              }}
            >
              <MiniBars
                data={p.perDay}
                color="cyan"
                labels={['30d ago', 'today']}
                height={90}
              />
            </div>
            <div className="section-label">Stage distribution</div>
            {p.stages.map((s) => (
              <div key={s.name} style={{ marginBottom: 8 }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    display: 'flex',
                    justifyContent: 'space-between',
                    marginBottom: 3,
                  }}
                >
                  <span className="c-muted">{s.name}</span>
                  <span className="c-text tnum">{s.count}</span>
                </div>
                <Meter
                  value={s.count}
                  max={Math.max(...p.stages.map((x) => x.count)) || 1}
                  color={s.state === 'warn' ? 'amber' : ''}
                />
              </div>
            ))}
            <div className="section-label">SLO</div>
            <DL
              rows={[
                ['Success rate', p.successRate + '%'],
                ['Avg completion', p.avgCompletion],
                ['Cadence', '0.8 / 1.0 per day'],
              ]}
            />
          </>
        );
        foot = (
          <>
            <button
              className="mbtn mbtn--primary"
              style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
              onClick={() => actions.runPipeline()}
            >
              <Icon name="play" size={13} />
              Trigger run
            </button>
            <button
              className="mbtn"
              style={{ padding: '10px 14px' }}
              onClick={() => actions.openPrefect()}
            >
              Open Prefect
            </button>
          </>
        );
        break;
      }
      case 'brain': {
        const b = e;
        eyebrow = 'SEMANTIC MEMORY';
        title = 'Brain';
        body = (
          <>
            <MemorySearch sources={b.bySource} />
            <div className="section-label">Corpus · by writer</div>
            <table
              className="tbl"
              style={{ border: '1px solid var(--gl-hairline)' }}
            >
              <thead>
                <tr>
                  <th>Writer</th>
                  <th className="num">Vectors</th>
                  <th className="num">Age</th>
                </tr>
              </thead>
              <tbody>
                {(b.byWriter || []).map((w, i) => {
                  const a = w.age;
                  const ageStr =
                    a == null
                      ? '—'
                      : a < 60
                        ? a + 's'
                        : a < 3600
                          ? Math.floor(a / 60) + 'm'
                          : a < 86400
                            ? Math.floor(a / 3600) + 'h'
                            : Math.floor(a / 86400) + 'd';
                  return (
                    <tr key={i}>
                      <td className={w.stale ? 'c-amber' : 'c-text'}>
                        {w.key}
                        {w.stale ? ' · stale' : ''}
                      </td>
                      <td className="num tnum">
                        {(w.count || 0).toLocaleString()}
                      </td>
                      <td className={`num ${w.stale ? 'c-amber' : 'c-dim'}`}>
                        {ageStr}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {/* growth + recent are brain_queue/daemon internals (no HTTP route)
                — present in mock, honest-empty in live (feedback_no_dummy_data). */}
            {b.growth && b.growth.length > 0 && (
              <>
                <div className="section-label">Embeddings · growth (30d)</div>
                <div
                  style={{
                    background: 'var(--gl-surface)',
                    padding: 12,
                    border: '1px solid var(--gl-hairline)',
                  }}
                >
                  <MiniBars
                    data={b.growth}
                    color="cyan"
                    labels={['30d', 'today']}
                    height={80}
                  />
                </div>
              </>
            )}
            {b.recent && b.recent.length > 0 && (
              <>
                <div className="section-label">Recently embedded</div>
                <table
                  className="tbl"
                  style={{ border: '1px solid var(--gl-hairline)' }}
                >
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Preview</th>
                      <th className="num">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.recent.map((r, i) => (
                      <tr key={i}>
                        <td className="c-cyan">
                          {r.src}#{r.id}
                        </td>
                        <td className="truncate" title={r.preview}>
                          {r.preview}
                        </td>
                        <td className="num c-dim">{r.at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <div className="section-label">Memory</div>
            <DL
              rows={[
                ['Total vectors', (b.totalEmbeddings ?? 0).toLocaleString()],
                ['Model', b.model + (b.dim ? ` · ${b.dim}d` : '')],
                [
                  'Queue depth',
                  b.queueDepth == null ? '— · no HTTP route' : b.queueDepth,
                ],
                [
                  'Last cycle',
                  b.lastCycle == null ? '— · no HTTP route' : b.lastCycle,
                ],
              ]}
            />
          </>
        );
        // Embed-trigger has no HTTP route — offer it only in mock (queueDepth
        // known); live mode shows no fake action button.
        foot =
          b.queueDepth != null ? (
            <button
              className="mbtn mbtn--amber"
              style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
              onClick={() => actions.embed()}
            >
              <Icon name="bolt" size={13} />
              Trigger embed cycle
            </button>
          ) : null;
        break;
      }
      case 'cost': {
        const c = e;
        eyebrow = 'COST CONTROL';
        title = 'Cost — spend, energy, infra';
        // Empty arrays mean live mode with no backend route — show an explicit
        // "backend read pending" note rather than a fabricated chart/table
        // (feedback_no_dummy_data).
        const pending = (label) => (
          <div
            className="mono c-dim"
            style={{
              fontSize: 11,
              padding: '10px 12px',
              border: '1px dashed var(--gl-hairline)',
            }}
          >
            {label} — backend read pending (no HTTP route yet)
          </div>
        );
        const energyUsd =
          c.energyKwhMonth != null
            ? c.energyKwhMonth * c.electricityRate
            : null;
        body = (
          <>
            <div className="section-label">LLM/API spend · 30 days</div>
            {c.daily && c.daily.length ? (
              <div
                style={{
                  background: 'var(--gl-surface)',
                  padding: 12,
                  border: '1px solid var(--gl-hairline)',
                }}
              >
                <MiniBars
                  data={c.daily}
                  color="amber"
                  labels={['30d', 'today']}
                  height={80}
                />
              </div>
            ) : (
              pending('Daily spend series')
            )}
            <div className="section-label">LLM spend by model</div>
            {c.byModel && c.byModel.length ? (
              <table
                className="tbl"
                style={{ border: '1px solid var(--gl-hairline)' }}
              >
                <thead>
                  <tr>
                    <th>Model</th>
                    <th className="num">Calls</th>
                    <th className="num">Tokens</th>
                    <th className="num">kWh</th>
                  </tr>
                </thead>
                <tbody>
                  {c.byModel.map((m) => (
                    <tr key={m.model}>
                      <td className="c-cyan">{m.model}</td>
                      <td className="num">{m.calls.toLocaleString()}</td>
                      <td className="num">{m.tokens}</td>
                      <td className="num c-amber">{m.kwh}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              pending('By-model breakdown')
            )}
            <div className="section-label">Budget · spend vs cap</div>
            <DL
              rows={[
                ['Month to date', '$' + c.monthToDate.toFixed(2)],
                ['Monthly cap', '$' + c.budget],
                ['Projected', '$' + c.projected.toFixed(2)],
                ['Daily burn', '$' + (c.dailyBurn ?? 0).toFixed(2) + '/day'],
                ['Status', c.status || '—'],
              ]}
            />
            <div className="section-label">Infra &amp; energy</div>
            <DL
              rows={[
                ['Infra', '$0/mo · self-hosted'],
                [
                  'Energy',
                  energyUsd != null
                    ? `~$${energyUsd.toFixed(2)}/mo · ${c.energyKwhMonth} kWh`
                    : '— pending',
                ],
                ['Agent API', '$' + (c.agentApiMonth ?? 0).toFixed(2) + '/mo'],
              ]}
            />
            <div
              className="mono c-dim"
              style={{ fontSize: 10.5, marginTop: 8 }}
            >
              {c.agentApiNote}
            </div>
          </>
        );
        foot = (
          <button
            className="mbtn"
            style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
            onClick={() => actions.editBudget()}
          >
            <Icon name="settings" size={13} />
            Edit budget
          </button>
        );
        break;
      }
      case 'findings': {
        const fd = e;
        eyebrow = 'PROBE FINDINGS · #461';
        title = 'Findings — routing triage';
        const rows = fd.findings || [];
        // Read-only: findings are delivered by the brain's router (watermark-
        // based). No ack/route footer — the drawer is a triage view.
        const SEVT = {
          critical: 'red',
          warn: 'amber',
          warning: 'amber',
          info: 'cyan',
        };
        const STAT = { PENDING: 'amber', routed: 'mint', 'log-only': 'cyan' };
        body = (
          <>
            <div className="section-label">Counts · {fd.hours || 168}h</div>
            <DL
              rows={[
                ['Emitted', String((fd.counts && fd.counts.emitted) || 0)],
                [
                  'Pending delivery',
                  String((fd.counts && fd.counts.pending) || 0),
                ],
                ['Route watermark', 'id ' + (fd.watermark || 0)],
              ]}
            />
            <div className="section-label">By severity</div>
            <div
              style={{
                display: 'flex',
                gap: 6,
                flexWrap: 'wrap',
                marginBottom: 10,
              }}
            >
              {(fd.by_severity || []).map((s) => (
                <span
                  key={s.severity}
                  className={`tag tag--${SEVT[s.severity] || 'cyan'}`}
                >
                  {s.severity} · {s.count}
                </span>
              ))}
            </div>
            <div className="section-label">Kind → delivery policy</div>
            <table
              className="tbl"
              style={{ border: '1px solid var(--gl-hairline)' }}
            >
              <thead>
                <tr>
                  <th>Kind</th>
                  <th className="num">Count</th>
                  <th>Delivery</th>
                </tr>
              </thead>
              <tbody>
                {(fd.by_kind || []).map((k) => (
                  <tr key={k.kind}>
                    <td className="c-cyan">{k.kind}</td>
                    <td className="num">{k.count}</td>
                    <td>
                      {(fd.delivery_by_kind && fd.delivery_by_kind[k.kind]) ||
                        'route'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="section-label">Latest findings</div>
            {rows.length === 0 ? (
              <div className="mono c-dim" style={{ fontSize: 11 }}>
                No findings in the window.
              </div>
            ) : (
              rows.map((row) => (
                <div
                  key={row.id}
                  style={{
                    marginBottom: 8,
                    paddingBottom: 8,
                    borderBottom: '1px solid var(--gl-hairline)',
                  }}
                >
                  <div
                    className="mono"
                    style={{
                      fontSize: 11,
                      display: 'flex',
                      justifyContent: 'space-between',
                    }}
                  >
                    <span className="c-text">{row.kind}</span>
                    <span className={`tag tag--${STAT[row.status] || 'cyan'}`}>
                      {row.status}
                    </span>
                  </div>
                  <div className="c-dim" style={{ fontSize: 11, marginTop: 2 }}>
                    {row.title || '—'}
                  </div>
                  <div
                    className="mono c-dim"
                    style={{ fontSize: 10, marginTop: 2 }}
                  >
                    {row.severity} · {row.source} · → {row.delivery} · id{' '}
                    {row.id}
                  </div>
                </div>
              ))
            )}
          </>
        );
        break;
      }
      case 'revenue': {
        const r = e;
        eyebrow = 'REVENUE · LEMON SQUEEZY';
        title = 'Revenue';
        body = (
          <>
            <div className="section-label">Daily revenue · 30 days</div>
            <div
              style={{
                background: 'var(--gl-surface)',
                padding: 12,
                border: '1px solid var(--gl-hairline)',
              }}
            >
              <MiniBars
                data={r.daily}
                color="cyan"
                labels={['30d', 'today']}
                height={80}
              />
            </div>
            <div className="section-label">By product type</div>
            {r.byType.map(([name, amt]) => (
              <div key={name} style={{ marginBottom: 8 }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    display: 'flex',
                    justifyContent: 'space-between',
                    marginBottom: 3,
                  }}
                >
                  <span className="c-muted">{name}</span>
                  <span className="c-text tnum">${amt}</span>
                </div>
                <Meter
                  value={amt}
                  max={r.byType[0][1]}
                  color={name.includes('sub') ? 'amber' : 'mint'}
                />
              </div>
            ))}
            <div className="section-label">Top earning posts</div>
            <table
              className="tbl"
              style={{ border: '1px solid var(--gl-hairline)' }}
            >
              <thead>
                <tr>
                  <th>Post</th>
                  <th className="num">Orders</th>
                  <th className="num">Revenue</th>
                </tr>
              </thead>
              <tbody>
                {r.topPosts.map((p, i) => (
                  <tr key={i}>
                    <td className="truncate" title={p.title}>
                      {p.title}
                    </td>
                    <td className="num">{p.orders}</td>
                    <td className="num c-mint">${p.rev}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="section-label">Recent events</div>
            {r.recent.map((ev, i) => (
              <div
                key={i}
                className="svc"
                style={{
                  gridTemplateColumns: 'auto 1fr auto',
                  padding: '6px 0',
                  cursor: 'default',
                }}
              >
                <span
                  className={`tag tag--${ev.kind === 'refund' ? 'amber' : ev.kind === 'sub' ? 'mint' : 'cyan'}`}
                >
                  {ev.kind}
                </span>
                <span className="svc__name truncate" style={{ fontSize: 11 }}>
                  {ev.what}
                  <small>{ev.src}</small>
                </span>
                <span
                  className={`svc__metric ${ev.amt < 0 ? 'c-amber' : 'c-mint'}`}
                >
                  {ev.amt < 0 ? '-' : ''}${Math.abs(ev.amt)}
                </span>
              </div>
            ))}
          </>
        );
        foot = (
          <button
            className="mbtn mbtn--ghost"
            style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
            onClick={() => actions.openLemon()}
          >
            <Icon name="link" size={13} />
            Open Lemon Squeezy
          </button>
        );
        break;
      }
      case 'qa': {
        const Q = e;
        eyebrow = 'QA RAILS';
        title = 'Multi-Model Review';
        body = (
          <>
            <div className="section-label">Throughput</div>
            <DL
              rows={[
                ['Pass rate', Q.passRate + '%'],
                ['Rejection rate', Q.rejectionRate + '%'],
                ['RAG fallback', Q.ragFallback],
              ]}
            />
            <div className="section-label">Rejection reasons · 7d</div>
            {Q.reasons.map(([name, n]) => (
              <div key={name} style={{ marginBottom: 8 }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    display: 'flex',
                    justifyContent: 'space-between',
                    marginBottom: 3,
                  }}
                >
                  <span className="c-muted">{name}</span>
                  <span className="c-text tnum">{n}</span>
                </div>
                <Meter value={n} max={Q.reasons[0][1]} color="amber" />
              </div>
            ))}
            <div className="section-label">Hallucination guardrails</div>
            <table
              className="tbl"
              style={{ border: '1px solid var(--gl-hairline)' }}
            >
              <thead>
                <tr>
                  <th>Rule</th>
                  <th className="num">Rate / 5m</th>
                </tr>
              </thead>
              <tbody>
                {Q.hallucination.map((h) => (
                  <tr key={h.rule}>
                    <td>{h.rule}</td>
                    <td className={`num c-${h.tone}`}>{h.rate.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="section-label">Approval rate by writer model</div>
            {Q.byModel.map((m) => (
              <div key={m.model} style={{ marginBottom: 8 }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    display: 'flex',
                    justifyContent: 'space-between',
                    marginBottom: 3,
                  }}
                >
                  <span className="c-muted">
                    {m.model} · {m.tasks} tasks
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
          </>
        );
        foot = (
          <button
            className="mbtn mbtn--ghost"
            style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
            onClick={onClose}
          >
            Close
          </button>
        );
        break;
      }
      case 'kpi': {
        eyebrow = 'METRIC';
        title = e.label;
        body = (
          <>
            <div className="section-label">Trend · 30 points</div>
            <div
              style={{
                background: 'var(--gl-surface)',
                padding: 12,
                border: '1px solid var(--gl-hairline)',
              }}
            >
              <Sparkline
                data={e.spark}
                color={
                  e.tone === 'amber'
                    ? 'var(--gl-amber)'
                    : e.tone === 'alert'
                      ? 'var(--gl-red)'
                      : e.tone === 'mint'
                        ? 'var(--gl-mint)'
                        : 'var(--gl-cyan)'
                }
                height={70}
              />
            </div>
            <div className="section-label">Current</div>
            <DL
              rows={[
                ['Value', (e.unit === '$' ? '$' : '') + e.value],
                ['Change', e.deltaLabel],
              ]}
            />
          </>
        );
        foot = (
          <button
            className="mbtn mbtn--ghost"
            style={{ flex: 1, justifyContent: 'center', padding: '10px' }}
            onClick={onClose}
          >
            Close
          </button>
        );
        break;
      }
    }
  }

  return (
    <>
      <div className={`drawer-scrim ${open ? 'open' : ''}`} onClick={onClose} />
      <aside
        className={`drawer ${open ? 'open' : ''}`}
        role="dialog"
        aria-modal="true"
      >
        {entity && (
          <>
            <header className="drawer__head">
              <div>
                <div className="topbar__eyebrow" style={{ marginBottom: 4 }}>
                  {eyebrow}
                </div>
                <div className="drawer__title">{title}</div>
              </div>
              <button
                className="drawer__close"
                onClick={onClose}
                aria-label="Close"
              >
                <Icon name="close" size={16} />
              </button>
            </header>
            <div className="drawer__body">{body}</div>
            {foot && <footer className="drawer__foot">{foot}</footer>}
          </>
        )}
      </aside>
    </>
  );
}

window.Drawer = Drawer;

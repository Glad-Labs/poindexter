/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — Task-Trace views.

   Front-door board (what's running now + 24h health) and the full-bleed
   per-task deep-dive (node spine · corpus · per-node output · QA/gate
   decisions · cost rollup · Langfuse escape hatch). A thin view over the
   tested pure logic in trace-helpers.js (window.TraceH).

   Hooks (useState/useEffect), Icon, and Freshness come from the shared
   global scope (primitives.jsx) — this file must NOT re-declare them
   (one global lexical scope across the babel scripts). Wrapped in an IIFE
   so its own component names stay local; the two entry components are
   published on window like the other console modules.
   ────────────────────────────────────────────────────────────── */
(function () {
  const H = window.TraceH;
  const PX = window.PX || {};
  const api = PX.api;

  const EMPTY_DETAIL = {
    task_id: '',
    run_id: null,
    runs: [],
    task: null,
    nodes: [],
    corpus: '',
    decisions: [],
    qa: [],
    cost_rollup: { by_model: [], total_usd: 0 },
    final: null,
    halt: null,
    langfuse: { session_id: '', run_id: null },
  };

  // Colorblind-safe status glyph (glyph carries the signal; class reinforces).
  function Glyph({ status }) {
    const g = H.glyphOf(status);
    return (
      <span className={g.cls} aria-hidden="true">
        {g.char}
      </span>
    );
  }

  // ── Front-door board ──────────────────────────────────────
  function TraceBoard({ data, fresh, onOpen }) {
    // The health strip has its own cadence (matches the route's 60s cache).
    const summaryR = window.PXR.usePolledResource(
      () =>
        api && api.isLive()
          ? api.traceSummary()
          : Promise.resolve(PX.traceSummary || {}),
      { intervalMs: 60000, key: 'traceSummary' }
    );
    const board = data || { runs: [], recent: [] };
    const running = (board.runs || []).map(H.mapActiveRun);
    const recent = (board.recent || []).map(H.mapRecentRun);
    const health = H.deriveHealth(summaryR.data || {});

    return (
      <div className="trace-board">
        <div className="thealth">
          {health.map((h, i) => (
            <div key={i}>
              <span className="k">{h.k}</span>
              <span className="v">{h.v}</span>
            </div>
          ))}
          <span className="win">
            {fresh ? (
              <Freshness
                lastUpdatedAt={fresh.lastUpdatedAt}
                stale={fresh.stale}
              />
            ) : (
              'last 24h'
            )}
          </span>
        </div>

        {running.length === 0 ? (
          <div className="trace-empty">Nothing running right now.</div>
        ) : (
          <div className="trun-grid">
            {running.map((r) => (
              <div
                key={r.id}
                className="trun trun--running"
                onClick={() => onOpen(r.id)}
              >
                <div className="trun__top">
                  <span className="trun__topic">{r.topic}</span>
                </div>
                <div className="trun__node">
                  <Icon name="bolt" size={12} /> node {r.nodesDone} ·{' '}
                  <span>{r.node}</span>
                </div>
                <div className="tprog">
                  <div className="tprog__fill" style={{ width: r.pct + '%' }} />
                </div>
                <div className="trun__meta">
                  <span className="tag tag--cyan">{r.template}</span>
                  <span>◐ {r.status}</span>
                  <span>{r.elapsed}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="trace-subhdr">Recently finished</div>
        {recent.length === 0 ? (
          <div className="trace-empty">No recent runs.</div>
        ) : (
          <div className="trun-grid">
            {recent.map((r) => (
              <div
                key={r.id}
                className={
                  'trun ' + (r.status === 'halt' ? 'trun--halt' : 'trun--done')
                }
                onClick={() => onOpen(r.id)}
              >
                <div className="trun__top">
                  <span className="trun__topic">{r.topic}</span>
                </div>
                <div className="trun__meta">
                  {r.status === 'halt' ? (
                    <span className="c-red">✕ {r.gate}</span>
                  ) : (
                    <span className="c-mint">✓ {r.gate}</span>
                  )}
                  <span>{r.wall}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── Deep-dive: per-node detail ────────────────────────────
  function NodeDetail({ node, onLangfuse }) {
    const isQA = node.atom.indexOf('qa.') === 0 && node.score != null;
    const norm = node.score != null && node.score <= 1;
    const shown = norm ? (node.score * 100).toFixed(0) : node.score;
    return (
      <div className="tdetail">
        <div className="tdetail__hd">
          <h3 className="tdetail__title">
            <Glyph status={node.status} /> {String(node.id).replace(/_/g, ' ')}
          </h3>
          <span className="tdetail__atom">{node.atom}</span>
        </div>

        <div className="tsec">
          <div className="tsec__h">Execution</div>
          <dl className="kv">
            <dt>status</dt>
            <dd>
              {node.rawStatus || node.status}
              {node.verdict ? ' · ' + node.verdict : ''}
            </dd>
            <dt>latency</dt>
            <dd>
              {(node.ms / 1000).toFixed(1)}s{node.slow ? ' · ' + node.slow : ''}
            </dd>
            <dt>model</dt>
            <dd>
              {node.model} <span className="c-dim">({node.tier})</span>
            </dd>
            <dt>cost</dt>
            <dd>{H.money(node.cost)}</dd>
            <dt>retries</dt>
            <dd>{node.retries}</dd>
          </dl>
        </div>

        {isQA && (
          <div className="tsec">
            <div className="tsec__h">
              QA verdict{node.adv ? ' · ' + node.adv : ''}
            </div>
            <div className="tscore">
              <span
                className="tscore__n"
                style={{
                  color: H.scoreColor(norm ? node.score * 100 : node.score),
                }}
              >
                {shown}
              </span>
              <span className="c-muted tscore__d">
                {norm ? '/ 1.0 normalized' : '/ 100'}
              </span>
            </div>
            <div className="scoremeter">
              <div
                className="scoremeter__f"
                style={{
                  width: (norm ? node.score * 100 : node.score) + '%',
                  background: H.scoreColor(
                    norm ? node.score * 100 : node.score
                  ),
                }}
              />
            </div>
          </div>
        )}

        <div className="tsec">
          <div className="tsec__h">{isQA ? 'Reasoning' : 'Output preview'}</div>
          <div className="preview">{node.preview || '—'}</div>
        </div>

        {node.outKeys.length > 0 && (
          <div className="tsec">
            <div className="tsec__h">State output</div>
            <div className="keys">
              {node.outKeys.map((k) => (
                <span key={k} className="kchip kchip--out">
                  {k}
                </span>
              ))}
            </div>
          </div>
        )}

        {onLangfuse && (
          <button className="lf-btn" onClick={onLangfuse}>
            <Icon name="link" size={13} /> View this step in Langfuse ↗
          </button>
        )}
      </div>
    );
  }

  // ── Deep-dive: whole-run overview ─────────────────────────
  function RunOverview({ trace, nodes, onLangfuse }) {
    const corpus = H.parseCorpus(trace.corpus);
    const decisions = trace.decisions || [];
    const rails = nodes.filter((n) => n.atom.indexOf('qa.') === 0 && !n.loop);
    const roll = (trace.cost_rollup && trace.cost_rollup.by_model) || [];
    const total = (trace.cost_rollup && trace.cost_rollup.total_usd) || 0;
    const maxUsd =
      roll.reduce((m, r) => Math.max(m, Number(r.usd) || 0), 0) || 1;
    const models = roll
      .map((r) => r.model)
      .filter(Boolean)
      .join(' · ');
    const final = trace.final;

    const decGlyph = (kind) => {
      const k = String(kind || '').toLowerCase();
      if (k.indexOf('approve') >= 0) return 'ok';
      if (k.indexOf('reject') >= 0) return 'halt';
      if (k.indexOf('regen') >= 0 || k.indexOf('rewrite') >= 0) return 'run';
      return 'pend';
    };

    return (
      <div className="tdetail">
        <div className="tsec" style={{ marginTop: 0 }}>
          <div className="tsec__h">
            Corpus handed to the writer · {corpus.length} source
            {corpus.length === 1 ? '' : 's'}
          </div>
          {corpus.length === 0 ? (
            <div className="trace-empty sm">
              No research corpus recorded for this run.
            </div>
          ) : (
            <div className="corpus">
              {corpus.map((c, i) => (
                <div key={i} className="src">
                  <span className="n">{c.n}</span>
                  {c.u && (
                    <a
                      className="u"
                      href={c.u}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {c.u} ↗
                    </a>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="tsec">
          <div className="tsec__h">Operator decisions</div>
          {decisions.length === 0 ? (
            <div className="trace-empty sm">
              No operator gate decisions — this run was decided by the automated
              QA gates (below).
            </div>
          ) : (
            <div className="dlog">
              {decisions.map((d, i) => (
                <div key={i} className="dlog__row">
                  <Glyph status={decGlyph(d.event_kind)} />
                  <span className="dlog__node">
                    {d.gate_name || d.event_kind}
                  </span>
                  <span className="dlog__what">
                    {d.feedback || d.event_kind}
                    {d.actor ? ' · ' + d.actor : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="tsec">
          <div className="tsec__h">
            QA decision summary · {rails.length} rails
          </div>
          {rails.length === 0 ? (
            <div className="trace-empty sm">No QA rails ran for this run.</div>
          ) : (
            <div className="qa-list">
              {rails.map((r, i) => (
                <div key={i} className="qa-row">
                  <Glyph
                    status={
                      r.status === 'halt'
                        ? 'halt'
                        : r.verdict === 'veto' || r.verdict === 'reject'
                          ? 'halt'
                          : 'ok'
                    }
                  />
                  <span className="rail">{r.atom.replace('qa.', '')}</span>
                  <span className="adv">{r.adv || ''}</span>
                  <span className="sc">
                    {r.score == null
                      ? '—'
                      : r.score <= 1
                        ? r.score.toFixed(2)
                        : r.score}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="tsec">
          <div className="tsec__h">Cost &amp; models · {H.money(total)}</div>
          {roll.length === 0 ? (
            <div className="trace-empty sm">No cost recorded.</div>
          ) : (
            <div className="croll">
              {roll.map((c, i) => {
                const usd = Number(c.usd) || 0;
                const cloud =
                  c.provider &&
                  c.provider !== 'ollama' &&
                  c.provider !== 'local';
                return (
                  <div key={i} className="croll__row">
                    <div>
                      <div className="croll__lbl">
                        {c.model} <span className="c-dim">×{c.calls}</span>
                      </div>
                      <div className="croll__bar">
                        <i
                          style={{
                            width: Math.max(2, (usd / maxUsd) * 100) + '%',
                            background: cloud
                              ? 'var(--gl-amber)'
                              : 'var(--gl-cyan-dim)',
                          }}
                        />
                      </div>
                    </div>
                    <span className="croll__amt">{H.money(usd)}</span>
                    <span className="croll__tier">
                      {cloud ? 'cloud' : 'local'}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {models && <div className="croll__models">{models}</div>}
        </div>

        <div className="tsec">
          <div className="tsec__h">Final post</div>
          {final ? (
            <div>
              <div className="preview" style={{ maxHeight: 120 }}>
                {final.title || '(untitled)'}
                {'\n\n'}
                {(final.content_length || 0).toLocaleString()} chars
                {final.quality_score != null
                  ? ' · q' + final.quality_score
                  : ''}
                {trace.task && trace.task.status
                  ? ' · ' + trace.task.status
                  : ''}
              </div>
              {onLangfuse && (
                <button className="lf-btn" onClick={onLangfuse}>
                  <Icon name="link" size={13} /> Whole-run Langfuse trace ↗
                </button>
              )}
            </div>
          ) : (
            <div className="trace-empty sm">
              No persisted post for this run yet.
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Deep-dive: master–detail shell ────────────────────────
  function TraceDeepDive({ taskId, onBack, onAction, onLangfuse }) {
    const [runId, setRunId] = useState(null);
    const [sel, setSel] = useState('overview');
    const [autoOpened, setAutoOpened] = useState(false);

    const detailR = window.PXR.usePolledResource(
      () =>
        api && api.isLive()
          ? api.traceDetail(taskId, runId)
          : Promise.resolve(PX.traceDetail || EMPTY_DETAIL),
      { intervalMs: 6000, key: 'traceDetail:' + taskId + ':' + (runId || '') }
    );

    const trace = detailR.data || EMPTY_DETAIL;
    const task = trace.task || {};
    const nodes = H.normalizeNodes(trace.nodes);
    const mx = H.maxMs(nodes);
    const runs = trace.runs || [];

    // Reset the view when the task or selected run changes.
    useEffect(() => {
      setSel('overview');
      setAutoOpened(false);
    }, [taskId, runId]);

    // Open-on-halt: default the selection to the halting node once loaded.
    useEffect(() => {
      if (autoOpened) return;
      const halt = trace.halt;
      if (halt && nodes.length) {
        const idx = nodes.findIndex((n) => n.seq === halt.seq);
        if (idx >= 0) {
          setSel(idx);
          setAutoOpened(true);
        }
      }
    }, [taskId, runId, trace.halt && trace.halt.seq, nodes.length, autoOpened]);

    const st = task.status || '';
    const canApprove = st === 'awaiting_approval';
    const canPublish = st === 'approved';
    const doLangfuse = onLangfuse
      ? () =>
          onLangfuse(
            trace.langfuse ? trace.langfuse.session_id : taskId,
            trace.run_id
          )
      : null;

    const statusChip =
      st === 'rejected' ||
      st === 'failed' ||
      st === 'cancelled' ||
      st.indexOf('reject') >= 0 ? (
        <span className="c-red">✕ {st}</span>
      ) : st === 'in_progress' || st === 'pending' ? (
        <span className="c-cyan">◐ {st}</span>
      ) : (
        <span className="c-mint">✓ {st || 'run'}</span>
      );

    let lastGroup = null;
    return (
      <div className="tdeep">
        <div className="tdeep__hdr">
          <button
            className="tdeep__back"
            onClick={onBack}
            title="Back to board"
          >
            <Icon
              name="chevron"
              size={16}
              style={{ transform: 'rotate(180deg)' }}
            />
          </button>
          <div>
            <div className="tdeep__topic">{task.topic || taskId}</div>
            <div className="tdeep__sub">
              <span className="tag tag--cyan">{task.template_slug || '—'}</span>{' '}
              {statusChip} · {trace.run_id || taskId}
              {detailR.stale && (
                <span style={{ marginLeft: 8 }}>
                  <Freshness
                    lastUpdatedAt={detailR.lastUpdatedAt}
                    stale={detailR.stale}
                  />
                </span>
              )}
            </div>
          </div>
          <div className="tdeep__stats">
            <div className="tstat">
              <span className="tstat__k">wall</span>
              <span className="tstat__v">
                {H.fmtDur(task.started_at, task.completed_at)}
              </span>
            </div>
            <div className="tstat">
              <span className="tstat__k">cost</span>
              <span className="tstat__v">
                {H.money(trace.cost_rollup ? trace.cost_rollup.total_usd : 0)}
              </span>
            </div>
            <div className="tstat">
              <span className="tstat__k">quality</span>
              <span className="tstat__v">
                {trace.final && trace.final.quality_score != null
                  ? trace.final.quality_score
                  : '—'}
              </span>
            </div>
            <div className="tstat">
              <span className="tstat__k">gate</span>
              <span className="tstat__v">{st || '—'}</span>
            </div>
          </div>
          {onAction && (canApprove || canPublish) && (
            <div className="tacts">
              {canApprove && (
                <button
                  className="tact tact--go"
                  onClick={() => onAction('approve', taskId)}
                >
                  ✓ Approve
                </button>
              )}
              {/* Same two-gate reject as the drawer: retry regenerates,
                  final closes the task out for good. */}
              {canApprove && (
                <button
                  className="tact tact--no"
                  title="rejected_retry — the pipeline rewrites it and it comes back for review"
                  onClick={() => onAction('reject', taskId)}
                >
                  ✕ Reject
                </button>
              )}
              {canApprove && (
                <button
                  className="tact tact--no"
                  title="rejected_final — closes the task out, nothing regenerates"
                  onClick={() => onAction('reject_final', taskId)}
                >
                  ⊘ Reject · final
                </button>
              )}
              {canPublish && (
                <button
                  className="tact"
                  onClick={() => onAction('publish', taskId)}
                >
                  Publish ↗
                </button>
              )}
            </div>
          )}
        </div>

        <div className="tdeep__pills">
          <span
            className={'rpill ' + (sel === 'overview' ? 'rpill--on' : '')}
            onClick={() => setSel('overview')}
          >
            Run overview
          </span>
          {runs.length > 1 &&
            runs.map((r) => (
              <span
                key={r.run_id}
                className={
                  'rpill ' +
                  ((runId || H.defaultRunId(runs)) === r.run_id
                    ? 'rpill--on'
                    : '')
                }
                onClick={() => setRunId(r.run_id)}
                title={r.template_slug + ' · ' + r.node_count + ' nodes'}
              >
                {r.kind}
                {r.status === 'halted' ? ' ✕' : ''}
              </span>
            ))}
        </div>

        <div className="tdeep__body">
          <div className="tspine">
            {nodes.length === 0 ? (
              <div className="trace-empty sm">
                No captured nodes for this run yet.
              </div>
            ) : (
              nodes.map((n, idx) => {
                const grp = n.group !== lastGroup ? n.group : null;
                lastGroup = n.group;
                return (
                  <React.Fragment key={idx}>
                    {grp && <div className="tspine__group">{grp}</div>}
                    <div
                      className={
                        'tnode' +
                        (sel === idx ? ' tnode--sel' : '') +
                        (n.loop ? ' tnode--loop' : '')
                      }
                      onClick={() => setSel(idx)}
                    >
                      <span className="tnode__seq">{n.seq}</span>
                      <span className="tnode__glyph">
                        <Glyph status={n.status} />
                      </span>
                      <span className="tnode__id">
                        {String(n.id).replace(/_/g, ' ')}{' '}
                        <span className="atom">{n.atom}</span>
                        {n.slow && <span className="tflag">{n.slow}</span>}
                      </span>
                      <span className="tnode__t">
                        {(n.ms / 1000).toFixed(1)}s
                        {n.cost > 0 ? ' · ' + H.money(n.cost) : ''}
                      </span>
                      <span className="tnode__bar">
                        <i
                          style={{
                            width: H.barPct(n.ms, mx) + '%',
                            background:
                              n.tier && n.tier !== 'local' && n.tier !== 'free'
                                ? 'var(--gl-amber)'
                                : 'var(--gl-cyan-dim)',
                          }}
                        />
                      </span>
                    </div>
                  </React.Fragment>
                );
              })
            )}
          </div>
          {sel === 'overview' ? (
            <RunOverview trace={trace} nodes={nodes} onLangfuse={doLangfuse} />
          ) : (
            <NodeDetail node={nodes[sel]} onLangfuse={doLangfuse} />
          )}
        </div>
      </div>
    );
  }

  Object.assign(window, { TraceBoard, TraceDeepDive });
})();

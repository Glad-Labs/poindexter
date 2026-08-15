/* ──────────────────────────────────────────────────────────────
   SYSTEM PULSE band — "what is the system doing right now" at a glance.
   Three columns fed by the /api/activity ledger (services/live_activity):

     In Production  — content + media runs, with real node-position progress
     Background     — scheduled jobs + brain cycles currently running
     Just Happened  — the recent-finished trail (✓ ok / ✕ fail / ⚠ stale)

   Data comes from PX.mapActivity(activity, Date.now()) (js/activity.js).
   Truly-running work is sourced from the ledger, NOT a loose status filter —
   so terminal tasks (rejected / awaiting-gate) never read as "in flight"
   (the taskStatusKind over-mapping the mock band carried). Idle → honest
   empty, never a fabricated row (feedback_no_dummy_data).

   `fresh` is the activity polled resource (app.jsx passes fresh={activityR}).
   On a failed poll the resource retains last-good data — deliberately — but
   the elapsed ages here are recomputed from started_at every render, so a
   retained row keeps aging on screen exactly like live work. When the
   resource goes stale the band therefore dresses down (nowrun--stale freezes
   the liveness choreography + dims the columns) and the header carries the
   amber "stale Ns" <Freshness> badge. Retained rows stay VISIBLE: a stale
   label over real data is honest; swapping in a fabricated idle is not.
   ────────────────────────────────────────────────────────────── */
function NowRunningBand({ activity, fresh, onOpenTask }) {
  const m = window.PX.mapActivity(activity || {}, Date.now());
  const f = window.PX.mapPulseFreshness(fresh);
  const fmtAge = (s) =>
    s == null ? '' : s < 90 ? `${s}s` : `${Math.round(s / 60)}m`;
  const glyph = { ok: '✓', fail: '✕', stale: '⚠' };
  const ellipsis = {
    flex: 1,
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  };
  return (
    <section className={f.bandClass} aria-label="System pulse">
      <div className="nowrun__scan">
        <i />
      </div>
      <header className="nowrun__head">
        <span className="nowrun__dot" />
        <span className="nowrun__title">SYSTEM PULSE</span>
        <span className="nowrun__sum">
          {m.inProduction.length} in production · {m.background.length}{' '}
          background · {m.trail.length} recent
        </span>
        {f.showBadge && (
          <Freshness lastUpdatedAt={f.lastUpdatedAt} stale={f.stale} />
        )}
        <span className="nowrun__eq" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
          <i />
        </span>
      </header>
      <div className="nowrun__grid">
        {/* ── In Production: content + media, with real progress ── */}
        <div className="nowrun__col">
          <div className="nowrun__collabel">IN PRODUCTION</div>
          {m.inProduction.length === 0 && (
            <div className="nowrun__empty">— idle · nothing in production</div>
          )}
          {m.inProduction.map((r) => {
            const media = r.kind === 'media';
            return (
              <div
                key={r.kind + (r.ref_id || r.title)}
                className={`nowrun__job${media ? ' nowrun__job--media' : ''}`}
                onClick={() => {
                  if (onOpenTask && r.kind === 'content' && r.ref_id) {
                    onOpenTask({ id: r.ref_id, topic: r.title });
                  }
                }}
              >
                <div className="nowrun__jobtop">
                  <Icon
                    name={media ? 'play' : 'doc'}
                    size={13}
                    style={{
                      color: media ? 'var(--gl-amber)' : 'var(--gl-cyan)',
                      flex: 'none',
                    }}
                  />
                  <span className="nowrun__jobtitle">{r.title}</span>
                  <span
                    className={`nowrun__age${media ? ' nowrun__age--amber' : ''}`}
                  >
                    {fmtAge(r.elapsedS)}
                  </span>
                </div>
                <div className="nowrun__jobmeta">
                  <span
                    className={`nowrun__stage${media ? ' nowrun__stage--amber' : ''}`}
                  >
                    {r.step || (media ? 'rendering' : '')}
                  </span>
                  <span className="nowrun__dimmeta">
                    {r.progress_pct != null ? r.progress_pct + '%' : ''}
                  </span>
                </div>
                {r.progress_pct != null && (
                  <div className="nowrun__bar">
                    <div
                      className={`nowrun__fill${media ? ' nowrun__fill--amber' : ''}`}
                      style={{ width: r.progress_pct + '%' }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {/* ── Background: jobs + brain cycles ── */}
        <div className="nowrun__col">
          <div className="nowrun__collabel">BACKGROUND</div>
          {m.background.length === 0 && (
            <div className="nowrun__empty">— idle</div>
          )}
          {m.background.map((r) => (
            <div key={r.kind + (r.ref_id || r.title)} className="nowrun__vrow">
              <span className="nowrun__dot" style={{ marginRight: 8 }} />
              <span style={ellipsis}>{r.title}</span>
              <span className="nowrun__age">{fmtAge(r.elapsedS)}</span>
            </div>
          ))}
        </div>
        {/* ── Just Happened: recent-finished trail ── */}
        <div className="nowrun__col">
          <div className="nowrun__collabel">JUST HAPPENED</div>
          {m.trail.length === 0 && <div className="nowrun__empty">—</div>}
          {m.trail.map((r, i) => (
            <div key={i} className="nowrun__trow">
              <span className={r.status === 'fail' ? 'fail' : 'ok'}>
                {glyph[r.status] || '·'}
              </span>
              <span style={ellipsis}>{r.title}</span>
              <span className="dur">
                {r.durationMs != null
                  ? Math.round(r.durationMs / 100) / 10 + 's'
                  : ''}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
window.NowRunningBand = NowRunningBand;

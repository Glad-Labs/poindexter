/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — Task-Trace pure logic (TraceH).

   The derivation layer between live /api/trace/* rows and the view
   (trace.jsx). Live `atom_runs` rows are lean — {seq, atom, node_id,
   status, tier, model, latency_ms, cost, output_keys, output_preview}
   — so the phase grouping, QA score/verdict, rescue-loop flag, and
   slow-node flag the deep-dive shows are all DERIVED here.

   Kept as plain JS (no JSX) so node:test can require() it directly.
   UMD tail: window.TraceH in the browser, module.exports for tests.
   ────────────────────────────────────────────────────────────── */
(function (root) {
  // ── money / score colour ──────────────────────────────────
  function money(n) {
    if (n == null || n === 0) return '$0.00';
    const v = Number(n);
    if (!Number.isFinite(v)) return '$0.00';
    return '$' + v.toFixed(3);
  }
  function scoreColor(v) {
    return v >= 85
      ? 'var(--gl-mint)'
      : v >= 70
        ? 'var(--gl-cyan)'
        : v >= 50
          ? 'var(--gl-amber)'
          : 'var(--gl-red)';
  }

  // ── status normalization + colorblind glyph ────────────────
  // Live atom_runs statuses collapse onto the 6 glyph buckets. Glyph carries
  // the signal (colorblind-safe); the tone class only reinforces it.
  const GLYPH = {
    ok: ['✓', 'c-mint'],
    halt: ['✕', 'c-red'],
    run: ['◐', 'c-cyan'],
    skip: ['○', 'c-dim'],
    pend: ['·', 'c-dim'],
    err: ['✕', 'c-red'],
  };
  function normStatus(s) {
    s = String(s || '').toLowerCase();
    if (s === 'halted' || s === 'error' || s === 'failed' || s === 'timeout')
      return 'halt';
    if (s === 'skipped' || s === 'skip') return 'skip';
    if (s === 'running' || s === 'in_progress') return 'run';
    if (s === 'ok' || s === 'success' || s === 'completed' || s === 'done')
      return 'ok';
    if (!s) return 'pend';
    return 'ok'; // any other terminal status renders as done
  }
  function glyphOf(status) {
    const k = GLYPH[status] ? status : normStatus(status);
    const g = GLYPH[k] || GLYPH.pend;
    return { char: g[0], cls: g[1] };
  }

  // ── phase grouping (the spine's section headers) ───────────
  // Derived from atom + node_id because the graph_def carries no phase label.
  // Order matters: qa.* is checked first; approval_gate is disambiguated by
  // node_id (draft_gate → Writer, preview_gate → Finalize).
  function groupFor(atom, nodeId) {
    const a = String(atom || '').toLowerCase();
    const hay = a + ' ' + String(nodeId || '').toLowerCase();
    const has = function () {
      for (let i = 0; i < arguments.length; i++)
        if (hay.indexOf(arguments[i]) >= 0) return true;
      return false;
    };
    if (a.indexOf('qa.') === 0)
      return a.indexOf('rewrite') >= 0 ? 'QA rescue' : 'QA rails';
    if (
      has(
        'verify_task',
        'generate_draft',
        'generate_title',
        'title_originality',
        'normalize_draft',
        'draft_gate',
        'writer_self_review',
        'internal_link',
        'reconcile_citations',
        'quality_evaluation',
        'url_validation',
        'narrate_bundle'
      )
    )
      return 'Writer';
    if (has('image', 'caption', 'featured')) return 'Image';
    if (has('seo', 'media_script', 'shot_list', 'training_data', 'video'))
      return 'SEO + media';
    if (
      has(
        'compile_meta',
        'persist_task',
        'record_pipeline_version',
        'evaluate_auto_publish',
        'social.generate_drafts',
        'preview_gate',
        'finalize'
      )
    )
      return 'Finalize';
    return 'Pipeline';
  }

  // ── output_preview → object (best-effort) ──────────────────
  // Previews are JSON strings from _preview()'s json.dumps, but the byte cap
  // can truncate them (they end with '…'), so JSON.parse must be allowed to
  // fail → null, and the node just shows its raw preview text.
  function parsePreview(preview) {
    if (!preview || typeof preview !== 'string') return null;
    const s = preview.trim();
    if (s[0] !== '{' && s[0] !== '[') return null;
    try {
      return JSON.parse(s);
    } catch {
      return null;
    }
  }
  // Extract {score, verdict, adv} from a parsed qa.* preview.
  function qaFromPreview(obj) {
    const out = {};
    if (typeof obj.score === 'number') out.score = obj.score;
    else if (typeof obj.faithfulness === 'number') out.score = obj.faithfulness;
    if (obj.approved === false) out.verdict = obj.veto ? 'veto' : 'reject';
    else if (obj.advisory === true) out.verdict = 'advisory';
    else if (obj.approved === true) out.verdict = 'pass';
    out.adv = obj.advisory === true ? 'advisory' : 'gate';
    return out;
  }

  // ── the core adapter: live rows → enriched view nodes ──────
  function normalizeNodes(raw) {
    if (!Array.isArray(raw) || !raw.length) return [];
    const nz = raw
      .map((n) => Number(n.latency_ms))
      .filter((v) => Number.isFinite(v) && v > 0)
      .sort((a, b) => a - b);
    const median = nz.length ? nz[Math.floor(nz.length / 2)] : 0;
    const seen = {};
    return raw.map((n) => {
      const atom = n.atom || n.node_id || '';
      const ms = Number(n.latency_ms) || 0;
      const preview = n.output_preview || '';
      const obj = parsePreview(preview);
      const isQA = String(atom).toLowerCase().indexOf('qa.') === 0;
      const qa = isQA && obj ? qaFromPreview(obj) : {};
      const loop = !!seen[atom];
      seen[atom] = true;
      const slow =
        median > 0 && ms > 2 * median
          ? (ms / median).toFixed(1) + '× median'
          : null;
      return {
        seq: n.seq,
        id: n.node_id || atom,
        atom,
        status: normStatus(n.status),
        rawStatus: n.status || '',
        ms,
        model: n.model || '—',
        tier: n.tier || 'local',
        cost: Number(n.cost) || 0,
        retries: Number(n.retries) || 0,
        inKeys: [], // get_trace's node SELECT doesn't surface input_keys
        outKeys: Array.isArray(n.output_keys) ? n.output_keys : [],
        preview,
        group: groupFor(atom, n.node_id),
        score: qa.score,
        verdict: qa.verdict,
        adv: qa.adv,
        loop,
        slow,
      };
    });
  }

  function maxMs(nodes) {
    let m = 1;
    (nodes || []).forEach((n) => {
      if ((n.ms || 0) > m) m = n.ms;
    });
    return m;
  }
  function barPct(ms, max) {
    const p = max > 0 ? (ms / max) * 100 : 0;
    return Math.max(2, p);
  }

  // ── run switcher default (content-first) ───────────────────
  function defaultRunId(runs) {
    if (!Array.isArray(runs) || !runs.length) return null;
    const content = runs.find((r) => r.kind === 'content');
    return (content || runs[0]).run_id;
  }

  // ── duration ('Xm YYs') from ISO timestamps ────────────────
  function fmtDur(fromIso, toIso) {
    if (!fromIso) return '—';
    const from = new Date(fromIso).getTime();
    if (!Number.isFinite(from)) return '—';
    const to = toIso ? new Date(toIso).getTime() : Date.now();
    if (!Number.isFinite(to)) return '—';
    let s = Math.max(0, Math.round((to - from) / 1000));
    const m = Math.floor(s / 60);
    s = s % 60;
    return m + 'm ' + String(s).padStart(2, '0') + 's';
  }

  // ── board mappers ──────────────────────────────────────────
  function mapActiveRun(r) {
    return {
      id: r.task_id,
      topic: r.topic || r.task_id,
      template: r.template_slug || '',
      node: r.stage || r.status || '',
      pct: Number(r.percentage) || 0,
      nodesDone: Number(r.nodes_done) || 0,
      elapsed: fmtDur(r.started_at, null),
      status: r.status || '',
    };
  }
  const _HALT = {
    rejected: 1,
    rejected_final: 1,
    failed: 1,
    cancelled: 1,
  };
  function mapRecentRun(r) {
    return {
      id: r.task_id,
      topic: r.topic || r.task_id,
      status: _HALT[r.status] ? 'halt' : 'done',
      gate: r.status || '',
      wall: fmtDur(r.started_at, r.completed_at),
    };
  }

  // ── 24h health strip (honest '—' for null KPIs) ────────────
  function deriveHealth(summary) {
    const s = summary || {};
    const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
    return [
      { k: 'runs (24h)', v: s.tasks == null ? '—' : String(s.tasks) },
      { k: 'pass rate', v: pct(s.pass_rate) },
      {
        k: 'avg quality',
        v: s.avg_quality == null ? '—' : String(s.avg_quality),
      },
      {
        k: 'avg cost',
        v: s.avg_cost_usd == null ? '—' : money(s.avg_cost_usd),
      },
    ];
  }

  // ── corpus string → [{n, u}] ──────────────────────────────
  // research_context is a freeform "name → url" bullet list. Light parse: keep
  // url-less lines intact (never drop context we can't structure).
  function parseCorpus(str) {
    if (!str || typeof str !== 'string') return [];
    return str
      .split('\n')
      .map((l) => l.replace(/^[-*•]\s*/, '').trim())
      .filter(Boolean)
      .map((line) => {
        const m = line.match(/(https?:\/\/[^\s]+|memory:\/\/[^\s]+)/);
        const url = m ? m[1] : '';
        let name = url ? line.slice(0, line.indexOf(url)) : line;
        name = name.replace(/[→|·]\s*$/, '').trim();
        if (!name) name = url || line;
        return { n: name, u: url };
      });
  }

  const TraceH = {
    money,
    scoreColor,
    normStatus,
    glyphOf,
    groupFor,
    parsePreview,
    qaFromPreview,
    normalizeNodes,
    maxMs,
    barPct,
    defaultRunId,
    fmtDur,
    mapActiveRun,
    mapRecentRun,
    deriveHealth,
    parseCorpus,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = TraceH;
  if (root) root.TraceH = TraceH;
})(typeof window !== 'undefined' ? window : null);

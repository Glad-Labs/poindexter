/* ══════════════════════════════════════════════════════════════
   QA review summary — pure helpers (window.PXQa)
   ──────────────────────────────────────────────────────────────
   Plain-JS, no React. Turns whatever QA evidence an approval row
   carries into ONE normalized shape the drawer's QA REVIEW section
   renders, so the operator sees WHY a draft is flagged on the card
   instead of hunting through the trace view (Matt, 2026-08-20:
   "why is it qa flagged? I still have to go and search for the qa
   feedback somewhere else").

   Two sources, structured preferred:
     1. metadata.qa_rail_breakdown — list of
        {reviewer, provider, score, approved, advisory, gated, feedback}
        written by task_metadata.build_task_metadata from
        aggregate_rail_reviews (drafts generated after 2026-08-20).
     2. qa_feedback — the formatted text compile_meta has written for
        every draft: one "Final score: N/100" line, then
        "- <reviewer> [<provider>] <score>/100 <pass|FAIL>: <feedback>"
        per rail. Parsed so older drafts get the same card.

   Kept out of drawer.jsx (schedule-helpers.js pattern) so the
   node:test + vm suite exercises the REAL logic with no DOM.
   ══════════════════════════════════════════════════════════════ */
(function () {
  const LINE_RE =
    /^-\s+(?<reviewer>[A-Za-z0-9_.\-]+)\s+\[(?<provider>[^\]]*)\]\s+(?<score>\d+(?:\.\d+)?)\/100\s+(?<verdict>pass|FAIL)\s*:?\s*(?<feedback>.*)$/;
  const FINAL_RE = /Final score:\s*(\d+(?:\.\d+)?)\/100/;

  // Parse compile_meta's qa_feedback text into rail dicts. Advisory rails
  // announce themselves in their feedback ("[advisory] (failed, not
  // required_to_pass) …"); the verdict token is the gate's view.
  function parseFeedback(text) {
    const out = { finalScore: null, rails: [] };
    if (!text || typeof text !== 'string') return out;
    const fm = text.match(FINAL_RE);
    if (fm) out.finalScore = Number(fm[1]);
    for (const raw of text.split('\n')) {
      const m = raw.trim().match(LINE_RE);
      if (!m) continue;
      const g = m.groups;
      const feedback = (g.feedback || '').trim();
      const advisory = /^\[advisory\]/i.test(feedback);
      // In the text form, an advisory rail that failed still prints "pass"
      // (the gate accepted it); its own failure is inside the feedback.
      const advisoryFailed = advisory && /\(failed/i.test(feedback);
      out.rails.push({
        reviewer: g.reviewer,
        provider: g.provider,
        score: Number(g.score),
        approved: g.verdict === 'pass' && !advisoryFailed,
        advisory,
        feedback: feedback.replace(/^\[advisory\]\s*\((?:passed|failed)[^)]*\)\s*/i, ''),
      });
    }
    return out;
  }

  function fromBreakdown(list) {
    return (Array.isArray(list) ? list : [])
      .filter((r) => r && typeof r === 'object' && r.reviewer)
      .map((r) => ({
        reviewer: String(r.reviewer),
        provider: r.provider != null ? String(r.provider) : '',
        score: r.score != null && isFinite(Number(r.score)) ? Number(r.score) : null,
        approved: !!r.approved,
        advisory: !!r.advisory,
        feedback: String(r.feedback || ''),
      }));
  }

  // Sort: vetoes (non-advisory fails) first, then advisory fails, then
  // passes — the operator reads the reason for the flag before anything.
  function rank(r) {
    if (!r.approved && !r.advisory) return 0;
    if (!r.approved) return 1;
    return 2;
  }

  /**
   * summarize({ metadata, qa_feedback, quality_score }) →
   *   { flagged, verdict, vetoedBy, finalScore, rails, vetoes, advisoryFails,
   *     passes, source, headline }
   */
  function summarize(row) {
    const meta = (row && row.metadata) || {};
    let rails = fromBreakdown(meta.qa_rail_breakdown);
    let source = 'structured';
    let finalScore =
      meta.qa_final_score != null && isFinite(Number(meta.qa_final_score))
        ? Number(meta.qa_final_score)
        : null;
    if (!rails.length) {
      const parsed = parseFeedback(row && row.qa_feedback);
      rails = parsed.rails;
      if (finalScore == null) finalScore = parsed.finalScore;
      source = rails.length ? 'feedback' : 'none';
    }
    rails.sort((a, b) => rank(a) - rank(b) || a.score - b.score);

    const vetoes = rails.filter((r) => !r.approved && !r.advisory);
    const advisoryFails = rails.filter((r) => !r.approved && r.advisory);
    const passes = rails.filter((r) => r.approved);

    const flagged = !!meta.qa_flagged;
    // Stored reasons win (they include "missing_required:<rail>", which no
    // rail line can express); fall back to the vetoing rails we can see.
    const vetoedBy = Array.isArray(meta.qa_vetoed_by) && meta.qa_vetoed_by.length
      ? meta.qa_vetoed_by.map(String)
      : vetoes.map((r) => r.reviewer);
    const verdict = meta.qa_final_verdict || (flagged || vetoes.length ? 'reject' : rails.length ? 'approve' : '');

    let headline;
    if (flagged || vetoedBy.length) {
      const missing = vetoedBy.filter((v) => v.startsWith('missing_required:'));
      const real = vetoedBy.filter((v) => !v.startsWith('missing_required:'));
      const parts = [];
      if (real.length) parts.push(`vetoed by ${real.join(', ')}`);
      if (missing.length)
        parts.push(
          `required rail produced no review: ${missing
            .map((v) => v.slice('missing_required:'.length))
            .join(', ')}`
        );
      headline = `Flagged — ${parts.join(' · ') || 'QA rejected this draft'}`;
    } else if (rails.length) {
      headline = advisoryFails.length
        ? `Passed the gate · ${advisoryFails.length} advisory rail${advisoryFails.length === 1 ? '' : 's'} objected`
        : 'Passed every rail';
    } else {
      headline = 'No QA evidence on this draft';
    }

    return {
      flagged,
      verdict,
      vetoedBy,
      finalScore,
      rails,
      vetoes,
      advisoryFails,
      passes,
      source,
      headline,
    };
  }

  window.PXQa = { parseFeedback, summarize };
})();

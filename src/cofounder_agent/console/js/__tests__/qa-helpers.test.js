'use strict';

// Contract tests for the approval card's QA REVIEW summary
// (src/cofounder_agent/console/js/qa-helpers.js). Same vm harness as the
// schedule/chat helper tests: the REAL file evaluated in a Node vm, no DOM.
//
// Why pinned: on 2026-08-20 three QA-rejected drafts sat in the queue
// showing only a score; the operator had to leave the card to learn WHY.
// The summary must put the veto reason first, handle both the structured
// breakdown (new drafts) and the formatted qa_feedback text (every older
// draft), and never invent a verdict when there is no evidence.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const j = (x) => JSON.parse(JSON.stringify(x));
const SRC = fs.readFileSync(path.join(__dirname, '..', 'qa-helpers.js'), 'utf8');

function load() {
  const sandbox = { console };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'qa-helpers.js' });
  return sandbox.PXQa;
}
const Q = load();

// Verbatim shape compile_meta wrote for the Anti-Goal draft (8c80bcb1).
const FEEDBACK = [
  'Final score: 99/100',
  "- programmatic_validator [programmatic] 0/100 FAIL: 1 critical issue(s) — first: Impossible claim about My Company: 'our revenue'",
  "- deepeval_brand_fabrication [deepeval] 0/100 pass: [advisory] (failed, not required_to_pass) 1 fabrication(s) detected: company_claim: 'our revenue'",
  '- content_originality [content_originality_gate] 23/100 pass: [advisory] (passed, not required_to_pass) content original — nearest published post at 0.77',
  '- topic_delivery [consistency_gate] 95/100 pass: The article faithfully executes the topic',
  '- ollama_critic [ollama] 96/100 pass: The article is excellent',
].join('\n');

test('parseFeedback reads the final score and every rail line', () => {
  const p = Q.parseFeedback(FEEDBACK);
  assert.equal(p.finalScore, 99);
  assert.equal(p.rails.length, 5);
  const v = p.rails.find((r) => r.reviewer === 'programmatic_validator');
  assert.equal(v.approved, false);
  assert.equal(v.advisory, false);
  assert.equal(v.score, 0);
  assert.match(v.feedback, /Impossible claim/);
});

test('an advisory rail that printed "pass" but failed internally is an advisory fail', () => {
  const p = Q.parseFeedback(FEEDBACK);
  const r = p.rails.find((r) => r.reviewer === 'deepeval_brand_fabrication');
  assert.equal(r.advisory, true);
  assert.equal(r.approved, false);
  // the "[advisory] (failed, not required_to_pass)" prefix is stripped for display
  assert.ok(!/^\[advisory\]/.test(r.feedback));
  assert.match(r.feedback, /fabrication/);
});

test('summarize: feedback-text fallback puts the veto first and names it in the headline', () => {
  const s = Q.summarize({
    metadata: { qa_flagged: true, qa_vetoed_by: ['programmatic_validator'] },
    qa_feedback: FEEDBACK,
  });
  assert.equal(s.source, 'feedback');
  assert.equal(s.flagged, true);
  assert.equal(s.verdict, 'reject');
  assert.equal(s.rails[0].reviewer, 'programmatic_validator');
  assert.deepEqual(j(s.vetoedBy), ['programmatic_validator']);
  assert.equal(s.vetoes.length, 1);
  assert.equal(s.advisoryFails.length, 1);
  assert.equal(s.passes.length, 3);
  assert.match(s.headline, /^Flagged — vetoed by programmatic_validator/);
  assert.equal(s.finalScore, 99);
});

test('summarize: structured breakdown wins over the text when present', () => {
  const s = Q.summarize({
    metadata: {
      qa_flagged: false,
      qa_final_verdict: 'approve',
      qa_final_score: 91.5,
      qa_rail_breakdown: [
        { reviewer: 'llm_critic', provider: 'ollama', score: 94, approved: true, advisory: false, feedback: 'good' },
        { reviewer: 'ragas_eval', provider: 'ragas', score: 40, approved: false, advisory: true, feedback: 'low relevancy' },
      ],
    },
    qa_feedback: FEEDBACK, // must be ignored
  });
  assert.equal(s.source, 'structured');
  assert.equal(s.rails.length, 2);
  assert.equal(s.verdict, 'approve');
  assert.equal(s.finalScore, 91.5);
  assert.match(s.headline, /Passed the gate · 1 advisory rail objected/);
  // advisory fail sorts before the pass
  assert.equal(s.rails[0].reviewer, 'ragas_eval');
});

test('summarize: missing_required veto is explained, not shown as a rail name', () => {
  const s = Q.summarize({
    metadata: { qa_flagged: true, qa_vetoed_by: ['missing_required:topic_delivery'] },
    qa_feedback: '- ollama_critic [ollama] 96/100 pass: fine',
  });
  assert.match(s.headline, /required rail produced no review: topic_delivery/);
  assert.equal(s.vetoes.length, 0); // no visible rail vetoed
});

test('summarize: no evidence → honest "none", never a fabricated verdict', () => {
  const s = Q.summarize({ metadata: {}, qa_feedback: null });
  assert.equal(s.source, 'none');
  assert.equal(s.verdict, '');
  assert.equal(s.rails.length, 0);
  assert.match(s.headline, /No QA evidence/);
});

test('summarize: clean pass reads as passed every rail', () => {
  const s = Q.summarize({
    metadata: {},
    qa_feedback: 'Final score: 93/100\n- ollama_critic [ollama] 93/100 pass: great\n- topic_delivery [consistency_gate] 98/100 pass: yes',
  });
  assert.equal(s.headline, 'Passed every rail');
  assert.equal(s.verdict, 'approve');
});

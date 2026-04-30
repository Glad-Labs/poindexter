# Pipeline Tuning Experiments

Rolling log of writer/critic/threshold/workflow tweaks and their outcomes.
Append-only — newest batch at the top. Each batch records:

- **Config**: what was changed from the previous batch
- **Topics**: fixed control set (see "Control Topic Set" below) unless noted
- **Results**: approval rate, score distribution, rejection reasons, duration
- **Notes**: what we learned, what to try next

Goal: move the system from 30% → 60%+ approval without lowering content
standards. Every knob change is captured so we can roll back when a
tweak makes things worse.

---

## Control Topic Set (CTS-v1)

Fixed 10-topic batch used across writer comparisons for apples-to-apples
evaluation. All on-brand (AI/ML, self-hosted, local inference niche).

1. Self-hosting Qwen 3 models with vLLM on a single 5090
2. Local inference latency benchmarks: Ollama vs llama.cpp vs vLLM
3. How RAG over your notes beats a general-purpose chatbot
4. Vector database tradeoffs: pgvector vs Qdrant vs Weaviate for small teams
5. GGUF quantization levels explained: when does Q4 beat Q8 for coding tasks
6. Fine-tuning a local LLM on your own writing without leaking data
7. Why prompt engineering became a self-hosted discipline
8. Context window limits and chunking strategies for long documents
9. LoRA adapters versus full fine-tuning for domain-specific models
10. Local-first AI agents: giving Claude-style tools to open-weight models

---

## Design principle: gates are veto-only, not scored

As of batch D we locked in `qa_gate_weight = 0`. Rationale:

The gate reviewers — `url_verifier`, `web_factcheck`, `vision_gate`,
`consistency_gate` — all answer binary pass/fail questions. A URL
either resolves or doesn't. External facts either hold up or don't.
The rendered page either looks right or doesn't. The post either
contradicts itself catastrophically or it doesn't.

None of these produce meaningful _gradations_ that should drag a
weighted average. Feeding a gate's score into the aggregate treats
it as a "soft opinion" on quality, which double-counts LLM judgment
(since the critic LLM is already scoring quality) and penalizes
posts for advisory complaints on top of their real quality score.

Correct model:

- **Gates**: veto-only. Hard pass/fail. Contribute 0 to the aggregate.
- **Scoring reviewers**: `programmatic_validator` (deterministic rule score)
  - critic LLM (judgment score). Different kinds of signal, sensibly
    combined in a weighted average.

Default in code still ships `qa_gate_weight=0.3` — but operators
running Poindexter should override to `0` until the upstream default
is fixed.

---

## Global Thresholds (as of 2026-04-23)

| Setting                                      | Value | Effect                                                                                      |
| -------------------------------------------- | ----- | ------------------------------------------------------------------------------------------- |
| `qa_final_score_threshold`                   | 80    | Min weighted score to approve                                                               |
| `qa_consistency_veto_threshold`              | 30    | internal_consistency hard-vetoes if score < 30                                              |
| `content_validator_warning_reject_threshold` | 5     | >5 same-category warnings promote to critical                                               |
| `content_validator_warning_qa_penalty`       | 3     | Pts subtracted per validator warning                                                        |
| `qa_validator_weight`                        | 0.4   | Weight of programmatic_validator in final score                                             |
| `qa_critic_weight`                           | 0.6   | Weight of critic LLM in final score                                                         |
| `qa_gate_weight`                             | 0 ⬅   | Was 0.3. Zeroed in batch D — gates are veto-only, not scored. See "Design principle" above. |

---

## Batch F — Tightened anti-fabrication prompt (prompt change, not config)

- **Config**: writer glm-4.7-5090, critic gemma3:27b, weights 0.4/0.6, gate_weight=0, threshold 80. Identical config to batch A/D/E. **Only change: rewrote `blog_generation.initial_draft` in the premium prompts repo** to move anti-fabrication rules to the top, add a pre-submission self-check block, and tighten the "never cite unlinked named sources" language.
- **Task IDs**: 866-875
- **Approval rate**: 2/10 (20%)
- **Max score**: 94 (batch A max was 87 — +7 ceiling lift)
- **Approved cleanly** (score, programmatic, citation_verifier all green):
  - **871 @ 94** — programmatic 100/100, citation_verifier 100/100. Cleanest post of the whole session.
  - 870 @ 88 — programmatic 97/100 with one advisory warning.

### Results

| ID  | Status      | Score | Failure Mode                                               |
| --- | ----------- | ----- | ---------------------------------------------------------- |
| 866 | rejected    | 75    | named-source no URL + unlinked citation                    |
| 867 | rejected    | 68    | score-gate                                                 |
| 868 | rejected    | 76    | score-gate                                                 |
| 869 | rejected    | 72    | score-gate                                                 |
| 870 | ✅ approved | 88    | —                                                          |
| 871 | ✅ approved | 94    | — (programmatic 100/100, citation_verifier 100/100)        |
| 872 | rejected    | 90    | named-source no URL                                        |
| 873 | rejected    | 27    | **FALSE POSITIVE: `pgvector` flagged as hallucinated lib** |
| 874 | rejected    | 90    | named-source no URL                                        |
| 875 | rejected    | 72    | score-gate                                                 |

### Comparison: A vs F (same config, new prompt)

| Metric                         | A (old prompt) | F (tightened prompt) |
| ------------------------------ | -------------- | -------------------- |
| Approval rate                  | 3/10           | 2/10                 |
| Avg score                      | 77.4           | 75.2                 |
| Max score                      | 87             | **94**               |
| Fabrication-related rejections | 2/10           | 3/10                 |
| Score-gate rejections          | 5/10           | 4/10                 |
| Validator false-positives      | 0/10           | 1/10 (pgvector)      |

### Findings

- **Prompt change LIFTED ceiling, not approval rate.** Max score jumped 87→94, and 871 was the cleanest content we've produced. The writer IS following the anti-fabrication rules more rigorously when it follows them at all.
- **But the writer still produces ≥1 unsourced named citation in 3/10 posts** (866, 872, 874). Those posts otherwise scored 75/90/90 — they'd have cleared 80 if not for that one phrase. So the prompt isn't fully self-checking.
- **Score-gate remains the persistent drag** — same pattern as A/D/E. The critic LLM's "section tension" advisory is independent of writer behavior.
- **Newly-surfaced validator false-positive: `pgvector`**. Same class as LoRA / REST / PostgreSQL / transformers. The hallucinated-library whitelist is incomplete for AI/ML/database extensions. Dropped task 873 from a passing-quality score to 27 on a single false flag.

### Next levers (if continuing to tweak)

1. **Fix the validator whitelist** — add `pgvector`, `lora`, `rest`, `postgresql`, `transformers` and similar AI-adjacent non-PyPI identifiers. Code change, small surface area.
2. **Pre-publish rewrite loop targeted at named-source** — if the programmatic_validator catches a named-source-no-URL, automatically rewrite that single sentence (we have a rewrite loop in cross_model_qa, but it's general-purpose, not surgical).
3. **Prompt variant with a MANDATORY "suggested_urls" JSON block** — force the writer to commit to a source list up front. Then the validator knows which named sources are "real" for this post.

---

## Batch E — N=2 repro of batch D (exact same config)

- **Config**: identical to batch D. writer `glm-4.7-5090`, critic `gemma3:27b`, weights 0.4/0.6, `qa_gate_weight`=0, threshold 80.
- **Task IDs**: 851-860
- **Approval rate**: **1/10 (10%)** — massive regression vs D's 5/10
- **Avg score**: 71.2 (D was 79.5, A was 77.4)

### Results

| ID  | Status      | Score | Failure Mode                                   |
| --- | ----------- | ----- | ---------------------------------------------- |
| 851 | rejected    | 63    | url_verifier — dead link (Qwen3 HF path)       |
| 852 | rejected    | 66    | score-gate                                     |
| 853 | rejected    | 57    | score-gate                                     |
| 854 | rejected    | 81    | named-source without URL                       |
| 855 | rejected    | 87    | named-source without URL                       |
| 856 | rejected    | 74    | score-gate                                     |
| 857 | rejected    | 65    | score-gate                                     |
| 858 | ✅ approved | 86    | —                                              |
| 859 | rejected    | 75    | named-source without URL + hallucinated lib    |
| 860 | rejected    | 61    | url_verifier — dead HN links (hallucinated ID) |

### Same-topic comparison: D vs E (both at gate_weight=0)

| Topic               | D score | E score | Δ                                        |
| ------------------- | ------- | ------- | ---------------------------------------- |
| Qwen3 self-host     | 66      | 63      | -3                                       |
| Ollama vs llama.cpp | 87 ✅   | 66      | -21                                      |
| RAG over notes      | 69      | 57      | -12                                      |
| pgvector vs Qdrant  | 71      | 81      | +10                                      |
| GGUF quant          | 87 ✅   | 87      | 0 (flip: approve→reject on named-source) |
| Fine-tune local LLM | 83 ✅   | 74      | -9                                       |
| Prompt eng          | 84 ✅   | 65      | -19                                      |
| Context window      | 74      | 86 ✅   | +12                                      |
| LoRA                | 91 ✅   | 75      | -16                                      |
| Local AI agents     | 86 ✅   | 61      | -25                                      |

- **Mean Δ: −8.3 pts.** Generation variance dominates.
- Same config, same topics — approval rate swung from 50% down to 10%.

### Aggregate analysis across A/D/E

| Batch | Config   | Approved | Avg score |
| ----- | -------- | -------- | --------- |
| A     | gate=0.3 | 3/10     | 77.4      |
| D     | gate=0   | 5/10     | 79.5      |
| E     | gate=0   | 1/10     | 71.2      |

- **Combined D+E (N=2, 20 tasks)**: 6/20 = **30%** approved — identical to A.
- **The 50% approval in D was a lucky batch**, not a stable property of `gate_weight=0`.

### Revised interpretation

- **Matt's design principle is still right** — gates _should_ be veto-only by philosophy, and `gate_weight=0` is cleaner architecture (no double-counting of LLM judgment).
- **But the _empirical_ approval lift was noise.** With σ ~15pt per-topic between runs, a single 10-topic batch can swing from 1/10 to 5/10 on pure variance.
- **Real takeaway: the writer and critic produce wildly inconsistent output** on the same topic with the same prompt and same settings. This is the dominant problem, not any single knob.
- **Named-source without URL** is the most frequent hard-veto (2/10 D, 3/10 E). The writer keeps producing phrases like "according to the official documentation" without naming which documentation. This is a prompt-engineering problem, not a gate problem.
- **Dead links** are the second most frequent (1/10 D, 2/10 E). Writer hallucinates specific paths on real domains. Also a prompt problem.

### Follow-up recommendations

- **Keep `gate_weight=0`** as the right design, but drop the claim that it materially boosts approvals
- Run **N=3 batches per config** going forward. N=1 is dangerously misleading at this variance level
- **Address the named-source + dead-link pattern directly** — either by prompt ("never cite a source without a URL") or by wider regex on named-source-without-URL promotion. This would unblock 3-5 rejections per batch
- **Temperature / sampling** of the writer is probably contributing to variance — worth checking if generation temperature is tunable

---

## Batch H — G-config repro (2026-04-24 morning)

- **Config**: identical to batch G. writer `glm-4.7-5090`, critic `gemma3:27b`, weights 0.4/0.6, gate_weight=0, threshold 80, tech-name whitelist active.
- **Task IDs**: 930-939
- **Approval rate**: **7/10 (70%)**
- **Avg score (approved)**: 87.7

### Results

| ID  | Status      | Score | Failure Mode                                          |
| --- | ----------- | ----- | ----------------------------------------------------- |
| 930 | ✅ approved | 86    | —                                                     |
| 931 | rejected    | 90    | named-source without URL (programmatic veto)          |
| 932 | ✅ approved | 89    | —                                                     |
| 933 | ✅ approved | 90    | —                                                     |
| 934 | rejected    | 77    | citation_verifier — 50% dead huggingface.co/docs URLs |
| 935 | ✅ approved | 85    | —                                                     |
| 936 | rejected    | 71    | citation_verifier — 33% dead openai.com/docs URLs     |
| 937 | ✅ approved | 93    | programmatic 100/100, citation_verifier 100/100       |
| 938 | ✅ approved | 90    | —                                                     |
| 939 | ✅ approved | 81    | —                                                     |

### Confirming the whitelist fix

G + H combined (same config, N=2): **16/20 approved = 80%**. Compare:

- A/C/D/E/F combined (pre-whitelist, mixed configs): 13/60 = 22%
- D/E combined (pre-whitelist, same gate_weight=0 config): 6/20 = 30%
- **G/H combined (post-whitelist)**: 16/20 = 80%

The whitelist fix is real signal, not variance. Approval rate tripled on the same writer + same config.

### Remaining rejection classes

All three H rejections were **legitimate hard vetoes**:

1. **citation_verifier — hallucinated docs URLs** (934, 936). Writer confidently cites specific paths under huggingface.co/docs/... or platform.openai.com/docs/... that don't resolve. These are real hallucinations worth catching.
2. **named-source without URL** (931). Writer referenced "the documentation" / "the Medium article" without linking.

Neither is a validator false-positive; both catch real writer errors. Next-lever thinking: the writer prompt could explicitly forbid path-specific citations ("if you cite huggingface.co/docs, ALWAYS include the specific working URL and omit the citation if you can't verify it").

### Session snapshot after H

| Batch | Config                                | Approved | Avg score |
| ----- | ------------------------------------- | -------- | --------- |
| A     | baseline                              | 3/10     | 77.4      |
| B     | writer=qwen3:30b                      | 0/10     | 61.0      |
| C     | weights 0.5/0.5                       | 2/10     | 75.6      |
| D     | gate_weight=0 (first run)             | 5/10     | 79.5      |
| E     | gate_weight=0 repro                   | 1/10     | 71.2      |
| F     | tightened anti-fabrication prompt     | 2/10     | 75.2      |
| G     | tech-name whitelist fix               | 9/10     | 88.8      |
| **H** | **G-config repro (N=2 confirmation)** | **7/10** | **86.3**  |

---

## Batch G — Tech-name whitelist fix (2026-04-24 overnight)

- **Config**: writer `glm-4.7-5090:latest`, critic `gemma3:27b`, weights 0.4/0.6, `qa_gate_weight`=0, threshold 80. Same as D/E/F. **Delta vs F: `_HALLUCINATION_WHITELIST` expanded with common tech names** (pgvector, LoRA, LLM, REST, PostgreSQL, Kubernetes, FastAPI, Cloudflare, etc.) so real-but-not-on-PyPI names stop tripping the unknown-library detector.
- **Task IDs**: 876-885
- **Approval rate**: **9/10 (90%)** — new session high by a wide margin
- **Avg score (approved)**: 89.1
- **Three tasks hit 100/100/100** on programmatic_validator + citation_verifier: 876 @ 93, 877 @ 93, 878 @ 94. Cleanest output of the session.

### Results

| ID  | Status      | Score | Failure Mode                                                     |
| --- | ----------- | ----- | ---------------------------------------------------------------- |
| 876 | ✅ approved | 93    | programmatic 100/100, citation_verifier 100/100                  |
| 877 | ✅ approved | 93    | programmatic 100/100, citation_verifier 100/100                  |
| 878 | ✅ approved | 94    | programmatic 100/100, citation_verifier 100/100                  |
| 879 | ✅ approved | 88    | one filler-phrase advisory, non-blocking                         |
| 880 | ✅ approved | 88    | one unlinked-citation advisory, non-blocking                     |
| 881 | ✅ approved | 85    | `transformers` off-topic advisory, non-blocking                  |
| 882 | ✅ approved | 84    | one filler advisory, non-blocking                                |
| 883 | rejected    | 84    | citation_verifier — 100% dead github.com/openai/... URLs (legit) |
| 884 | ✅ approved | 91    | `transformers` off-topic advisory, non-blocking                  |
| 885 | ✅ approved | 88    | `LangChain` off-topic advisory, non-blocking                     |

### Session-wide approval-rate progression

| Batch | Config change                          | Approved | Avg score |
| ----- | -------------------------------------- | -------- | --------- |
| A     | baseline (writer=glm, gate_weight=0.3) | 3/10     | 77.4      |
| B     | writer=qwen3:30b                       | 0/10     | 61.0      |
| C     | weights 0.5/0.5                        | 2/10     | 75.6      |
| D     | gate_weight=0 (first run)              | 5/10     | 79.5      |
| E     | gate_weight=0 repro                    | 1/10     | 71.2      |
| F     | tightened anti-fabrication prompt      | 2/10     | 75.2      |
| **G** | **tech-name whitelist fix**            | **9/10** | **88.8**  |

### Findings

- **The whitelist gap was bigger than I measured.** Before the fix, the unknown-library detector was firing on `pgvector`, `LangChain`, `transformers`, etc. Even when they didn't hard-veto, they added to the warning count and dragged aggregate scores. With common tech names whitelisted, scores cluster in the 84-94 band instead of the 65-87 band.
- **The only rejection (883) was a hard veto on real hallucinated URLs** — writer claimed `github.com/openai/openai-cookbook/...` paths that 404. Exactly the class of reject we want the system to catch.
- **Three posts at 100/100/100** is notable — previously only task 871 (batch F) hit programmatic 100/100. Having three of them in one batch suggests the writer was already capable of clean output; the validator was just flagging real tool names as hallucinations.

### Caveats

- N=1 run. Session variance taught us batch D's 5/10 became E's 1/10 on the same config. Need N=3 runs of this config to confirm 9/10 is the new normal vs a lucky batch.
- One batch completed under an artificially raised `max_approval_queue` (20 → 50) because the session's approved-post backlog (21) had throttled new generation. The cap will drop back once operator reviews the queue. Throughput in steady-state depends on approval velocity.
- **Recommendation for the operator:** queue batch H with the same config (writer=glm, gate_weight=0, whitelist active) before making any more tuning changes. If H lands at 7-9/10, the whitelist fix is confirmed as a real improvement. If H lands at 1-4/10, we're back in variance territory.

---

## Batch D — qa_gate_weight = 0 (disable gate drag)

- **Config**: writer `glm-4.7-5090:latest`, critic `gemma3:27b`, weights reverted to `qa_validator_weight`=0.4 / `qa_critic_weight`=0.6, **`qa_gate_weight` = 0** (was 0.3). Only delta vs batch A is the gate weight.
- **Task IDs**: 837-846
- **Approval rate**: **5/10 (50%)** — up from batch A's 30%
- **Avg generation time**: ~170s per task

### Results

| ID  | Status      | Score | Duration | Failure Mode                                                               |
| --- | ----------- | ----- | -------- | -------------------------------------------------------------------------- |
| 837 | rejected    | 66    | 130s     | named-source without URL (programmatic veto)                               |
| 838 | ✅ approved | 87    | —        | —                                                                          |
| 839 | rejected    | 69    | 149s     | score-gate (critic dragged, gate contribution was 0)                       |
| 840 | rejected    | 71    | 172s     | citation_verifier — 50% dead dev.to URLs (hallucinated path, legit reject) |
| 841 | rejected    | 87    | 222s     | programmatic veto — unlinked citation "Beyond..."                          |
| 842 | ✅ approved | 83    | —        | —                                                                          |
| 843 | ✅ approved | 84    | —        | —                                                                          |
| 844 | rejected    | 74    | 177s     | score-gate (critic-only, gate contribution was 0)                          |
| 845 | ✅ approved | 91    | —        | —                                                                          |
| 846 | ✅ approved | 86    | —        | —                                                                          |

### Same-topic comparison: A vs D

| Topic               | A score | D score | Δ         |
| ------------------- | ------- | ------- | --------- |
| Qwen3 self-host     | 72      | 66      | -6        |
| Ollama vs llama.cpp | 84 ✅   | 87 ✅   | +3        |
| RAG over notes      | 84      | 69      | -15       |
| pgvector vs Qdrant  | 73      | 71      | -2        |
| GGUF quant          | 75      | 87      | +12       |
| Fine-tune local LLM | 67      | 83 ✅   | +16       |
| Prompt eng          | 80 ✅   | 84 ✅   | +4        |
| Context window      | 65      | 74      | +9        |
| LoRA                | 87 ✅   | 91 ✅   | +4        |
| Local AI agents     | 87      | 86 ✅   | -1 (flip) |

- **Mean Δ: +2.4 pts.** Approvals flipped from 3 → 5 (same threshold).
- Flips: Fine-tune (67→83), GGUF (75→87), Local AI agents (87 reject → 86 approve, cleared named-source issue).

### Notes

- **gate_weight=0 is a net positive.** Approval rate doubled from A (3→5) despite same noise floor. The gates — especially internal_consistency — were suppressing scores 2-3 pts on average and killing borderline cases.
- **Critic is still the bottleneck.** Tasks 839 (69), 844 (74) rejected at score-gate even with gate_weight=0 — i.e. critic scored those posts low enough that validator+critic average was under 80. "lowest reviewer internal_consistency @ 40/45" in the message is now cosmetic since its weight was 0.
- **Hard vetoes still fired** in D (837 named-source, 840 dead links, 841 unlinked citation) — the quality floor held. Disabling gate_weight did not let garbage through.
- Validator edges seen this batch: `REST`, `PostgreSQL`, `transformers` flagged as hallucinated/off-topic libraries. None blocking. Recurring low-impact noise.

### Recommendation

- **Keep `qa_gate_weight` = 0 as the new baseline.** 50% approval is the first crack in the 80 threshold wall, and no quality regression.
- Next lever to try: swap **critic model** from gemma3:27b to glm-4.7-5090 or qwen3:30b. Gemma is being harsh. A different critic might surface fewer "tension" complaints on tradeoff-aware content.
- Then consider **threshold sensitivity** (75 vs 80) as a cleaner quality-vs-approval knob.

---

## Batch C — Weight rebalance 0.5/0.5

- **Config**: writer `glm-4.7-5090:latest` (reverted from B), critic `gemma3:27b`, `qa_validator_weight`=0.5, `qa_critic_weight`=0.5 (was 0.4/0.6)
- **Task IDs**: 822-831
- **Approval rate**: 2/10 (20%)
- **Avg generation time**: ~145s per task

### Same-topic comparison: A vs C

Same 10 topics, same writer + critic — only `qa_validator_weight` and `qa_critic_weight` changed. Shows the generation-variance noise floor.

| Topic               | A score | C score | Δ   |
| ------------------- | ------- | ------- | --- |
| Qwen3 self-host     | 72      | 75      | +3  |
| Ollama vs llama.cpp | 84 ✅   | 83 ✅   | -1  |
| RAG over notes      | 84      | 75      | -9  |
| pgvector vs Qdrant  | 73      | 52      | -21 |
| GGUF quant          | 75      | 83 ✅   | +8  |
| Fine-tune local LLM | 67      | 67      | 0   |
| Prompt eng          | 80 ✅   | 74      | -6  |
| Context window      | 65      | 77      | +12 |
| LoRA                | 87 ✅   | 63      | -24 |
| Local AI agents     | 87      | 71      | -16 |

- **Mean Δ: −5.4 pts.** Within noise floor.
- **σ across same-topic runs: ~12 pts.**
- Two approvals this batch (823, 826); one same-topic flip won (826 GGUF), one lost (828 prompt eng).

### Notes

- The weight rebalance did not materially change outcomes. Direction is possibly slightly negative but swamped by generation variance.
- **Statistical reality:** a single 10-topic batch has noise of ±10-15 pts per topic. Need N=3 batches per config to distinguish signal from variance.
- **Confirmed bottleneck:** the `internal_consistency` advisory drag is present in both A and C as the dominant score suppressor. Lowering critic weight was NOT sufficient — the advisory flag contributes through the `internal_consistency` gate itself (weight 0.3), which we didn't touch.
- Additional validator edges observed: `REST` flagged as hallucinated library (all-caps acronym, same class as `LoRA`). Low-impact, didn't block approval.

### Follow-up experiments

- **Batch D (proposed):** disable `internal_consistency` gate entirely to isolate its contribution. If approval jumps to 60%+, we've isolated the bottleneck. If not, re-evaluate.
- Later: critic model swap (glm or qwen3 as critic instead of gemma3), threshold sensitivity (75 vs 80)

---

## Batch B — Writer: qwen3:30b

- **Config**: writer `qwen3:30b`, critic `gemma3:27b` (unchanged), thresholds unchanged
- **Task IDs**: 810-819
- **Approval rate**: 0/10 (0%)
- **Avg generation time**: 133s per task

### Results

| ID  | Status   | Score | Duration | Failure Mode                                                          |
| --- | -------- | ----- | -------- | --------------------------------------------------------------------- |
| 810 | rejected | 68    | 107s     | score-gate (internal_consistency @ 40)                                |
| 811 | rejected | 73    | 123s     | citation_verifier — 50% dead GitHub URLs (hallucinated)               |
| 812 | rejected | 0     | 137s     | programmatic veto — `pg_hba.conf` flagged (FALSE POSITIVE, real file) |
| 813 | rejected | 77    | 173s     | score-gate (internal_consistency @ 40)                                |
| 814 | rejected | 74    | 127s     | score-gate (internal_consistency @ 40)                                |
| 815 | rejected | 75    | 144s     | **fabricated person "Dr. Chen"** (programmatic veto)                  |
| 816 | rejected | 80    | 125s     | **fabricated stat "300% increase"** + named-source no URL             |
| 817 | rejected | 61    | 114s     | **fabricated stats "15% reduction", "10%"**                           |
| 818 | rejected | 0     | 114s     | **fabricated stat "12% drop"**                                        |
| 819 | rejected | 69    | 163s     | score-gate (internal_consistency @ 40)                                |

### Rejection Breakdown

- **4/10 fabrications** — writer hallucinated people ("Dr. Chen") and percentages ("12%", "15%", "300%"). This is a new failure class that glm-4.7-5090 did not produce.
- **4/10 score-gate** — below 80 threshold, same section-tension pattern as batch A.
- **1/10 dead links** — hallucinated GitHub paths on real domains.
- **1/10 validator false-positive** — `pg_hba.conf` flagged as hallucinated library. Snake_case + `.conf` extension not caught by the plain-TitleCase filter. Logged as a follow-up code fix.

### Comparison: A vs B (same topics, same critic, same thresholds)

| Metric                      | Batch A (glm-4.7-5090) | Batch B (qwen3:30b) |
| --------------------------- | ---------------------- | ------------------- |
| Approval rate               | 3/10 (30%)             | 0/10 (0%)           |
| Avg score on rejects        | 75.3                   | 61.0                |
| Fabrications (stats/people) | 0/10                   | 4/10                |
| Avg generation time         | 152s                   | 133s                |
| Dead citations              | 0/10                   | 1/10                |

### Notes

- **qwen3:30b fabricates heavily** — 40% of its output had a critical validator veto for fake stats or people. glm-4.7-5090 had 0 fabrications.
- qwen3:30b is ~15% faster per task but produces unpublishable content at a much higher rate.
- Decision: **qwen3:30b is unsuitable as writer for this niche.** Reverting writer to glm-4.7-5090 for subsequent batches.
- New validator edge found: `pg_hba.conf`-style config files flagged as hallucinated libraries. Will need a file-extension whitelist for dotted backticked tokens.

### Follow-up experiments queued

- Batch C: glm-4.7-5090 writer + rebalanced weights (0.5/0.5 validator/critic) to reduce critic drag
- After C: threshold sensitivity (try 75) or critic swap to qwen3:30b in the critique role

---

## Batch A — Writer: glm-4.7-5090 (baseline)

- **Config**: writer `glm-4.7-5090:latest`, critic `gemma3:27b`, threshold 80
- **Task IDs**: 796-805
- **Approval rate**: 3/10 (30%)
- **Avg generation time**: 152s per task (~2.5 min)

### Results

| ID  | Status      | Score | Duration | Failure Mode                                 |
| --- | ----------- | ----- | -------- | -------------------------------------------- |
| 796 | rejected    | 72    | 162s     | score-gate (internal_consistency @ 40)       |
| 797 | ✅ approved | 84    | —        | —                                            |
| 798 | rejected    | 84    | 129s     | named-source without URL (programmatic veto) |
| 799 | rejected    | 73    | 191s     | score-gate (internal_consistency @ 65)       |
| 800 | rejected    | 75    | 147s     | score-gate (internal_consistency @ 65)       |
| 801 | rejected    | 67    | 149s     | score-gate (internal_consistency @ 60)       |
| 802 | ✅ approved | 80    | —        | —                                            |
| 803 | rejected    | 65    | 135s     | score-gate (internal_consistency @ 60)       |
| 804 | ✅ approved | 87    | —        | —                                            |
| 805 | rejected    | 87    | 153s     | named-source without URL (programmatic veto) |

### Rejection Breakdown

- **5/7 score-gate** — scores 65-75, below 80 threshold. All had `internal_consistency` advisory flags (approved=False, score ≥ 30) from critic finding mild section-to-section tension. Not hard-vetoing but dragging aggregate score below threshold.
- **2/7 named-source without URL** — scores 84 and 87 (would pass if not promoted). Writer referenced "the official documentation", "a Medium article" etc. without linking. Legit validator catch.

### Notes

- Writer produces coherent content but critic is hyper-sensitive to section tensions (advocating X in section 2, Y in section 4 when Y caveats X). Penalty drags most posts to 65-75.
- 80 threshold + 0.6 critic weight makes this the kill zone. Either the threshold is too high OR the critic weight is too high.
- Named-source rule correctly caught 2 real issues — good signal.
- Trilogy of validator fixes (markdown headings, plain TitleCase, linked citations) all held — zero false-positives in this batch.

### Follow-ups suggested

1. Swap critic model (keep glm writer) — test if a different critic produces fewer advisory contradictions
2. Try threshold 75 vs 70 to see what score range is actually bad vs "just strict"
3. Consider rebalancing weights (0.5/0.5 validator/critic)
4. Consider disabling internal_consistency gate for a batch to isolate its impact

---

## Pre-Batch-A context

Prior to 2026-04-23 the pipeline was rejecting essentially all content (~100%
failure). Root causes found and fixed during the audit + tuning session:

- `c7df911c` — unlinked_citation regex matched markdown section headings
- `9e802e60` — hallucinated_reference flagged plain TitleCase English words ("Use API", "Large Language Models")
- `89768318` — unlinked_citation matched text inside `[title](url)` markdown links
- `e1b8aaed` — base `re.IGNORECASE` flag collapsed `[A-Z]` in UNLINKED_CITATION_PATTERNS to `[A-Za-z]`, matching any lowercase prose
- `aa4648ca` + `70297913` — rejection message misnamed score-gate failures as reviewer "vetoes" when the reviewer was only advisory

All fixes are code changes, not config. Batch A is the first apples-to-apples
data point after the pipeline was actually working.

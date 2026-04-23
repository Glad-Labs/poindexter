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

## Global Thresholds (as of 2026-04-23)

| Setting                                      | Value | Effect                                          |
| -------------------------------------------- | ----- | ----------------------------------------------- |
| `qa_final_score_threshold`                   | 80    | Min weighted score to approve                   |
| `qa_consistency_veto_threshold`              | 30    | internal_consistency hard-vetoes if score < 30  |
| `content_validator_warning_reject_threshold` | 5     | >5 same-category warnings promote to critical   |
| `content_validator_warning_qa_penalty`       | 3     | Pts subtracted per validator warning            |
| `qa_validator_weight`                        | 0.4   | Weight of programmatic_validator in final score |
| `qa_critic_weight`                           | 0.6   | Weight of critic LLM in final score             |
| `qa_gate_weight`                             | 0.3   | Weight of consistency/topic/vision/url gates    |

---

## Batch B — Writer: qwen3:30b (in progress)

- **Config**: writer `qwen3:30b`, critic `gemma3:27b` (unchanged), thresholds unchanged
- **Task IDs**: 810-819
- **Status**: ⏳ running

Results will be populated as the batch completes.

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

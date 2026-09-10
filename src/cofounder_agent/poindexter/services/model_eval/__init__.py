"""model_eval — champion–challenger model evaluation loop.

Cross-cutting kernel infrastructure that continuously answers, per model
slot: "is there a better open-source model than the one we run today?".

See the design spec at
``docs/architecture/2026-06-29-model-eval-loop-design.md`` and Plan 1
(eval-core reranker vertical slice) at
``docs/architecture/2026-06-29-model-eval-loop-wave1-plan.md``.
"""

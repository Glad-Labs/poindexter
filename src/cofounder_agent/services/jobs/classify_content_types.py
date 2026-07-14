"""ClassifyContentTypesJob — label published posts with content-types.

A post-publish sweep (spec 2026-07-13). Each run classifies up to
``batch_size`` published posts that have no ``post_content_types`` row yet — so
it backfills the existing corpus and keeps up with new posts through one code
path. Idempotent: a labeled post is never re-fetched.

## Method

Local LLM (feedback_no_paid_apis) via the ``llm_text`` seam, pinned to the
structured-extraction model (JSON output must not use a reasoner —
feedback_reasoning_models_empty_json). The prompt is a SKILL.md pack
(``content.classify_content_type``); output is validated deterministically
against the configured label set (``_content_type_classify.validate_labels``)
so only allowed labels persist and a post the model can't classify gets zero
rows (no silent default — feedback_no_silent_defaults).

## Config (``plugin.job.classify_content_types``)

- ``batch_size`` (default 10) — posts to classify per run.
- ``excerpt_chars`` (default 1500) — post-body chars fed to the classifier.

## Settings (app_settings)

- ``classify_content_types_enabled`` (default ``true``) — master switch.
- ``content_type_labels`` (CSV) — the allowed label set.
- ``content_type_classifier_model`` (default ``""`` → structured_extraction_model).
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.jobs._content_type_classify import parse_labels_csv, validate_labels
from services.llm_text import ollama_chat_text, resolve_structured_model
from services.prompt_manager import get_prompt_manager

logger = logging.getLogger(__name__)

# Published posts with no content-type row yet, newest first. The tag join is a
# best-effort hint for the classifier (the LLM-sprawl folksonomy — noisy, never
# authoritative); a post with no tags simply gets an empty hint.
_UNLABELED_SQL = """
    SELECT p.id, p.title, p.content,
           COALESCE(string_agg(t.slug, ',') FILTER (WHERE t.slug IS NOT NULL), '') AS tags
    FROM posts p
    LEFT JOIN post_tags ptg ON ptg.post_id = p.id
    LEFT JOIN tags t ON t.id = ptg.tag_id
    WHERE p.status = 'published'
      AND NOT EXISTS (
          SELECT 1 FROM post_content_types pct WHERE pct.post_id = p.id
      )
    GROUP BY p.id, p.title, p.content, p.published_at
    ORDER BY p.published_at DESC NULLS LAST
    LIMIT $1
"""

_INSERT_SQL = """
    INSERT INTO post_content_types (post_id, content_type, confidence, source)
    VALUES ($1, $2, $3, 'classifier')
    ON CONFLICT (post_id, content_type) DO NOTHING
"""

_DISABLED_VALUES = {"false", "0", "no", "off"}


class ClassifyContentTypesJob:
    name = "classify_content_types"
    description = "Label published posts with content-types (post_content_types)"
    schedule = "every 6 hours"
    idempotent = True  # only touches posts with no content-type row yet

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        enabled = sc.get("classify_content_types_enabled", "true") if sc else "true"
        if str(enabled).strip().lower() in _DISABLED_VALUES:
            return JobResult(
                ok=True, detail="classify_content_types disabled", changes_made=0
            )

        labels = parse_labels_csv(sc.get("content_type_labels", "") if sc else "")
        if not labels:
            return JobResult(
                ok=True, detail="no content_type_labels configured", changes_made=0
            )

        batch = int(config.get("batch_size", 10))
        excerpt_chars = int(config.get("excerpt_chars", 1500))

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_UNLABELED_SQL, batch)
                if not rows:
                    return JobResult(
                        ok=True,
                        detail="all published posts already labeled",
                        changes_made=0,
                    )

                # Resolve the model only once we know there's work — an empty
                # sweep must not fail loud on a misconfigured model.
                model = resolve_structured_model(
                    (sc.get("content_type_classifier_model") if sc else None) or None,
                    site_config=sc,
                )

                labeled = 0
                failed = 0
                for row in rows:
                    # A single post's classification (prompt build, LLM call,
                    # validation, insert) must not abort the whole batch — a
                    # transient LLM connection blip (e.g. a stale pooled
                    # socket to a cold/evicted Ollama model — the sparse
                    # 6-hourly cadence is exactly the idle window that
                    # triggers it) would otherwise fail up to `batch_size`
                    # already-good classifications and page the operator.
                    # Every sibling LLM-calling job (affiliate_import,
                    # retention_summarize_to_table, retention_embeddings_
                    # collapse) already isolates per-item LLM failures this
                    # way. The skipped post simply stays unlabeled and is
                    # picked up again by _UNLABELED_SQL on the next run —
                    # safe because the job is idempotent by design.
                    try:
                        prompt = get_prompt_manager().get_prompt(
                            "content.classify_content_type",
                            labels=", ".join(labels),
                            title=row["title"] or "",
                            tags=row["tags"] or "",
                            excerpt=(row["content"] or "")[:excerpt_chars],
                        )
                        raw = await ollama_chat_text(
                            prompt,
                            model=model,
                            site_config=sc,
                            pool=pool,
                            tier="budget",
                            phase="classify_content_type",
                            # Disable the thinking channel: gemma-4-31B otherwise
                            # emits a long reasoning preamble and buries/truncates
                            # the JSON, so validate_labels sees [] for every post
                            # (empty post_content_types across the whole corpus).
                            # This is a structured-extraction call — we want the
                            # object, not the deliberation.
                            think=False,
                        )
                        picks = validate_labels(raw, labels)
                        for label, conf in picks:
                            await conn.execute(_INSERT_SQL, row["id"], label, conf)
                        if picks:
                            labeled += 1
                        logger.info(
                            "classify_content_types: %s → %s",
                            row["id"],
                            [p[0] for p in picks],
                        )
                    except Exception as exc:  # noqa: BLE001 — one bad post must not abort the batch
                        failed += 1
                        logger.warning(
                            "classify_content_types: %s failed (%s) — leaving "
                            "unlabeled for the next run",
                            row["id"], exc,
                        )
        except Exception as e:  # noqa: BLE001 — report as a failed JobResult
            logger.exception("ClassifyContentTypesJob failed: %s", e)
            return JobResult(ok=False, detail=f"failed: {e}", changes_made=0)

        detail = f"labeled {labeled} of {len(rows)} post(s)"
        if failed:
            detail += f", {failed} failed (retried next run)"
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=labeled,
            metrics={
                "posts_seen": len(rows),
                "posts_labeled": labeled,
                "posts_failed": failed,
            },
        )

"""``narrate_bundle`` atom — bundle in, narrative paragraphs out.

Phase 3 atom replacing the dev_diary template's ``generate_content``
node. Single LLM call with a tightly-scoped system prompt; produces
2-3 paragraphs of plain prose with PR references embedded inline as
markdown links. No headings, no bullet list, no "What you'll learn"
sections.

Why this exists:

The v1 dev_diary template reused the existing ``generate_content``
stage (which dispatched to ``DETERMINISTIC_COMPOSITOR`` writer mode
and produced "narrative + deterministic ## PRs and commits list"
output). Matt's verdict: "reads like a changelog." The bottom links
list was the offending element — it visually dominated the post and
made the narrative paragraphs feel like preamble.

This atom drops the separate links section entirely. Inline PR links
within the narrative prose are sufficient ground truth — and reading
"As shipped in [PR #231](url), the writer now sees..." reads like a
real post, not a release note.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#362 (Phase 3 atom granularity).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Atom metadata
# ---------------------------------------------------------------------------

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

ATOM_META = AtomMeta(
    name="atoms.narrate_bundle",
    type="atom",
    version="1.0.0",
    description=(
        "Render a context_bundle (merged_prs + notable_commits) as 2-3 "
        "paragraphs of narrative prose with inline PR markdown links. "
        "Single LLM call. No deterministic bullet list."
    ),
    inputs=(
        FieldSpec(
            name="context_bundle",
            type="dict",
            description="DevDiaryContext.to_dict() shape: {date, merged_prs[], notable_commits[]}",
            required=False,  # falls back to reading from pipeline_versions
        ),
        FieldSpec(name="task_id", type="str", description="task UUID — used to load the bundle when not in state", required=False),
        FieldSpec(name="database_service", type="DatabaseService", description="for the bundle fallback read", required=False),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="full post body (markdown)"),
        FieldSpec(name="model_used", type="str", description="resolved model name"),
        FieldSpec(name="_narrate_bundle_ran", type="bool", description="set to True once executed"),
    ),
    requires=("context_bundle",),
    produces=("content",),
    capability_tier="dev_diary_narrator",
    cost_class="compute",
    idempotent=False,  # stochastic LLM
    side_effects=("calls ollama via local_llm_api_url",),
    retry=RetryPolicy(
        max_attempts=2,
        backoff_s=5.0,
        retry_on=("httpx.ReadTimeout", "httpx.ConnectError", "httpx.HTTPError"),
    ),
    fallback=("dev_diary_narrator", "budget_writer", "free_writer"),
    parallelizable=False,
)


_HEADER_PREFIX = "# What we shipped — "
_FOOTER = "_Auto-compiled by Poindexter from today's commits and PRs._"


_NARRATIVE_SYSTEM_PROMPT = """\
You are writing a daily dev diary entry for Glad Labs — a one-person
indie shop building Poindexter, an AI-operated content business.
This is autobiographical: you ARE Glad Labs writing about today's
work for other indie builders who'll find the post on the blog.

Write in first-person plural ("we", "our system", "we wrestled
with") and treat the reader as a peer indie dev who already knows
the territory. Produce 2-3 short paragraphs (4-7 sentences each,
max ~280 words total) of plain prose that draws the reader into
the arc of today's work.

THE ARC:

1. Open with stakes. Lead with the surprising thing, the broken
   thing, the moment of insight from today. Match this shape:
   "Today's biggest fight was X." "We almost shipped Y until we
   caught Z." "We'd been telling ourselves W was fine — today we
   admitted it wasn't." Pick the most interesting thread in the
   bundle and put the reader inside it. Frame around the work
   itself, not around a duration claim.

2. Thread the bundle facts through the narrative. When you mention
   a change, name the underlying system that broke ("the validator
   was firing 8x per post — same regex matching prose AND
   markdown links interchangeably") and link the PR that fixed it
   inline as markdown ([PR #231](url-from-bundle)). Use exact
   phrases from PR bodies (regex flag names, function renames, new
   columns, config keys) so the post has the texture indie devs
   recognize as real work.

3. Close with reflection. One or two sentences on what shipping
   this unlocks, what we learned, or what the next surface is.
   Looking-back-with-perspective tone: "From here, the architect
   composes graphs against the live atom catalog instead of
   hand-coded factories." Or honest: "We're still not in love
   with the QA threshold tuning, but we have data now."

VOICE TEXTURES THAT WORK:

- Vulnerability where it's earned: "took us several attempts
  before we noticed Y was the actual bug."
- Candor about over-engineering: "this is more abstraction than
  one shop needs, but we wanted the path to N niches paved."
- Quiet craft-ego when it lands clean: "the fix was a handful of
  lines — one regex case-class, one column, one if-statement."
- Occasional one-sentence paragraph for weight.
- Real questions when honest: "Is N=3 the right clean-run window?
  We'll know once the data accumulates."

GROUNDING (every name, number, url, code reference, AND duration
is grounded in the bundle):

- Names: use names that appear verbatim in a bundle entry. "Glad
  Labs", "Poindexter", "gladlabs.io", PR/commit authors, and any
  component name from the bundle are fair game.
- Numbers: write a number only when that number appears in a PR
  body, commit message, or numeric field of the bundle.
- Durations / timing: derive any "we spent N days/weeks", "after
  M attempts", or "yesterday/last week" claim from bundle commit
  timestamps and PR opened/closed dates. Write a duration only
  when the bundle supports it. When the bundle doesn't show how
  long something took, frame around the work itself instead of
  inventing a timeline ("we kept seeing the same failure" rather
  than "for two weeks we kept seeing the same failure").
- Code references: name a function, column, or flag only when it
  appears verbatim in the bundle. Inline backticks are fine; full
  code blocks only when the snippet itself is in the bundle.
- URLs: every url comes from the bundle. The inline PR reference
  shape is "[PR #N](url-from-bundle's-pr-url-field)".

VOICE TIGHTENING (positive directives — what good looks like):

- Open with the surprising/broken/insight moment, not a date or
  PR count.
- Stay first-person plural through the whole post.
- Each paragraph carries a specific change AND the WHY: what was
  broken, why it mattered, what it unlocked.
- Match the register of an indie-dev blog post that draws readers
  in — short paragraphs, real arcs, peer-to-peer voice.

OUTPUT: emit only the narrative paragraphs. The caller appends a
deterministic header + footer. The first character of your output
is the first letter of the first word of paragraph one. Plain
markdown prose, no headings, no lists, no surrounding JSON.
"""


def _format_bundle_for_narrative(bundle: dict[str, Any]) -> str:
    """Render the bundle as plain text for the narrative LLM input.

    Drops everything but PR titles + bodies + commits. Strips the
    auto-generated Claude Code footer + Co-Authored-By trailers from
    PR bodies so the model doesn't echo them. Caps each PR body at
    1200 chars and the whole input at ~12K chars.
    """
    prs = bundle.get("merged_prs") or []
    commits = bundle.get("notable_commits") or []

    lines: list[str] = []
    lines.append(f"DATE: {bundle.get('date') or 'today'}")
    lines.append("")
    if prs:
        lines.append(f"MERGED PRs ({len(prs)}):")
        lines.append("")
        for p in prs[:30]:
            num = p.get("number") or "?"
            title = (p.get("title") or "").strip()
            url = (p.get("url") or "").strip()
            body = (p.get("body") or "").strip()
            for marker in (
                "🤖 Generated with",
                "Co-Authored-By:",
                "Generated with [Claude",
                "Generated-by: Claude",
            ):
                if marker in body:
                    body = body.split(marker, 1)[0].rstrip()
            body = body[:1200]
            lines.append(f"PR #{num}: {title}")
            if url:
                lines.append(f"  url: {url}")
            if body:
                for body_line in body.splitlines():
                    lines.append(f"  {body_line}")
            lines.append("")
    if commits:
        lines.append(f"NOTABLE COMMITS ({len(commits)}):")
        lines.append("")
        for c in commits[:30]:
            sha = (c.get("sha") or "").strip()
            subject = (c.get("subject") or "").strip()
            if sha and subject:
                lines.append(f"- {sha}: {subject}")
        lines.append("")
    text = "\n".join(lines)
    return text[:12000]


async def _ollama_chat_text(prompt: str, model: str) -> str:
    """Plain-text Ollama chat call (no JSON envelope).

    The codebase's ``services.topic_ranking._ollama_chat_json`` helper
    sets ``format=json`` and wraps prose in ``{"thought": "..."}``
    envelopes. This is a minimal text-mode equivalent that returns the
    raw assistant content.
    """
    import httpx
    from services.site_config import site_config

    base_url = (
        site_config.get("local_llm_api_url", "http://localhost:11434").rstrip("/")
    )
    timeout = site_config.get_float("niche_ollama_chat_timeout_seconds", 120.0)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return (data.get("message") or {}).get("content", "")


def _maybe_unwrap_json(prose: str) -> str:
    """Defensive: if the model wrapped its output in JSON, unwrap it.

    Even with no ``format=json`` arg, some models still produce
    ``{"thought": "..."}`` envelopes when asked for prose. Walk the
    common envelope keys and extract the inner string.
    """
    prose = (prose or "").strip()
    if not (prose.startswith("{") and prose.endswith("}")):
        return prose
    import json as _json
    try:
        parsed = _json.loads(prose)
    except Exception:
        return prose
    if not isinstance(parsed, dict):
        return prose
    for k in ("thought", "response", "content", "text",
              "output", "answer", "summary", "narrative"):
        v = parsed.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return prose


def _bundle_is_empty(bundle: dict[str, Any]) -> bool:
    return not (bundle.get("merged_prs") or bundle.get("notable_commits"))


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node entrypoint.

    Reads the dev_diary bundle from state (placed there by
    ``services.stages.generate_content._read_context_bundle`` upstream
    OR injected by an earlier node), produces narrative prose with
    inline PR links via a single LLM call, wraps it with a
    deterministic header + footer, writes back to ``state['content']``.

    Returns only the diff (content + model_used + a marker indicating
    this atom ran). Caller threads it into LangGraph state.
    """
    # The bundle lives at stage_data._dev_diary_bundle (preserved by
    # run_dev_diary_post.py outside the trigger-replaced
    # task_metadata key). We also accept state['context_bundle'] for
    # callers that pass it directly (architect-LLM compositions in
    # Phase 4).
    bundle = state.get("context_bundle")
    if not bundle:
        # Fall back to reading from pipeline_versions.stage_data.
        task_id = state.get("task_id")
        database_service = state.get("database_service")
        if task_id and database_service is not None:
            bundle = await _load_bundle_from_db(database_service.pool, task_id)
    bundle = bundle or {}

    date = (bundle.get("date") or "").strip() or "today"

    if _bundle_is_empty(bundle):
        body = (
            f"{_HEADER_PREFIX}{date}\n\n"
            f"Quiet day — no shipped work to report.\n\n"
            f"{_FOOTER}\n"
        )
        return {"content": body, "model_used": "none"}

    from services.site_config import site_config

    model = (
        site_config.get("pipeline_writer_model", "glm-4.7-5090:latest")
        or "glm-4.7-5090:latest"
    ).removeprefix("ollama/")

    bundle_text = _format_bundle_for_narrative(bundle)
    full_prompt = (
        f"{_NARRATIVE_SYSTEM_PROMPT}\n\n"
        f"---\n\n"
        f"BUNDLE:\n\n{bundle_text}\n\n"
        f"---\n\n"
        f"Now produce 2-3 short paragraphs (max ~280 words total) "
        f"summarizing what shipped, how, and why. Embed PR references "
        f"inline as markdown links using the urls from the bundle. "
        f"No headings, no bullets, no opening hook, no closing CTA, "
        f"no first-person, no external references outside the bundle, "
        f"no invented numbers. Output ONLY the prose."
    )

    try:
        raw = await _ollama_chat_text(full_prompt, model=model)
    except Exception as exc:
        logger.warning(
            "[atoms.narrate_bundle] LLM call failed: %s — falling back "
            "to one-line summary",
            exc,
        )
        raw = ""

    prose = _maybe_unwrap_json(raw).strip()
    if not prose:
        # Graceful fallback so the post still ships.
        pr_n = len(bundle.get("merged_prs") or [])
        commit_n = len(bundle.get("notable_commits") or [])
        parts: list[str] = []
        if pr_n:
            parts.append(f"{pr_n} PR{'s' if pr_n != 1 else ''}")
        if commit_n:
            parts.append(f"{commit_n} notable commit{'s' if commit_n != 1 else ''}")
        prose = (
            f"The team shipped {' and '.join(parts)} today. "
            f"See the linked PRs for details."
            if parts
            else "Quiet day — no shipped work to report."
        )

    body = (
        f"{_HEADER_PREFIX}{date}\n\n"
        f"{prose}\n\n"
        f"{_FOOTER}\n"
    )

    quality_score = _compute_quality_score(bundle, prose, body)

    return {
        "content": body,
        "model_used": model,
        "_narrate_bundle_ran": True,
        # Deterministic quality_score for downstream finalize_task +
        # auto_publish_gate. The dev_diary template skips the legacy
        # quality_evaluation / cross_model_qa stages because the post
        # is fact-narration grounded in real bundle data — but the
        # gate still needs SOMETHING to score against. This computes
        # a calculated score from bundle integrity + citation rate +
        # length, per feedback_calculated_vs_generated.
        "quality_score": quality_score,
    }


def _compute_quality_score(
    bundle: dict[str, Any], prose: str, full_body: str,
) -> float:
    """Deterministic 0-100 quality score for the rendered dev_diary.

    Scoring rubric (positive directives — what good looks like):

    - 100: starts at the ceiling.
    - Stays at ceiling when prose is real (not the quiet-day fallback)
      AND the post cites at least min(8, N_prs) PRs inline as
      markdown links AND length sits inside the target band.
    - Drops by deductions:
        - 50 deduction when the prose is empty or the quiet-day
          fallback (no real shipped work to narrate).
        - Citation deduction up to 30 points based on how short of
          target the inline citation count fell. Target is
          ``min(8, N_prs)`` because the writer prompt asks for
          thematic grouping, not enumeration — citing 8 representative
          PRs is sufficient even on busy days with 30+ merges.
        - 15 deduction when word count is outside the 80-450 band
          (matches the prompt's 2-3 paragraph / ~280 word target).
    - Floor 30 once any prose rendered (the post still ships, just
      with operator review).

    Returns the score rounded to one decimal. The auto_publish_gate
    compares this against ``dev_diary_auto_publish_threshold`` (default
    -1 = disabled), so even when scores hover at 100 nothing
    auto-publishes until the operator opts in.
    """
    score = 100.0
    prose_clean = (prose or "").strip().lower()
    word_count = len((prose or "").split())

    quiet_day = "quiet day" in prose_clean or len(prose_clean) < 40
    if quiet_day:
        score -= 50

    prs = bundle.get("merged_prs") or []
    if prs:
        cited = 0
        for pr in prs:
            url = (pr.get("url") or "").strip() if isinstance(pr, dict) else ""
            if url and url in full_body:
                cited += 1
        target = min(8, len(prs))
        miss_rate = max(0.0, 1 - (cited / max(1, target)))
        score -= round(miss_rate * 30, 1)

    if word_count < 80 or word_count > 450:
        score -= 15

    if not quiet_day and score < 30:
        score = 30.0
    score = max(0.0, min(100.0, score))
    return round(score, 1)


async def _load_bundle_from_db(pool: Any, task_id: Any) -> dict[str, Any]:
    """Read the preserved bundle from pipeline_versions.stage_data.

    The dev_diary job stashes it at ``stage_data._dev_diary_bundle``
    (top level) so the trigger that JSONB-merges task_metadata doesn't
    wipe it on the writer's first column update.
    """
    if pool is None or not task_id:
        return {}
    try:
        import json as _json
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT stage_data FROM pipeline_versions "
                "WHERE task_id = $1 ORDER BY version ASC LIMIT 1",
                str(task_id),
            )
        if not row:
            return {}
        sd = row["stage_data"]
        if isinstance(sd, str):
            sd = _json.loads(sd)
        if not isinstance(sd, dict):
            return {}
        cb = sd.get("_dev_diary_bundle")
        if isinstance(cb, str):
            cb = _json.loads(cb)
        return cb if isinstance(cb, dict) else {}
    except Exception as exc:
        logger.debug(
            "[atoms.narrate_bundle] bundle read failed for task %s: %s",
            task_id, exc,
        )
        return {}

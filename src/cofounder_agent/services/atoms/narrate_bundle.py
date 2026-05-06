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


# Prompt key in UnifiedPromptManager + prompt_templates table. The
# YAML default lives at prompts/atoms.yaml; runtime overrides come
# from the prompt_templates DB row with the matching key. Per
# feedback_prompts_must_be_db_configurable: every prompt is
# DB-configurable; inline constants are tech debt.
_PROMPT_KEY = "atoms.narrate_bundle.system_prompt"


def _resolve_system_prompt() -> str:
    """Pull the narrate-bundle system prompt from UnifiedPromptManager.

    Langfuse production label wins > YAML defaults > inline fallback.
    Inline fallback only fires when the prompt registry hasn't been
    initialized (early bootstrap, test paths) — production reads from
    YAML at minimum. Operators editing the prompt land their changes
    in the Langfuse UI; the next get_prompt call picks up the new
    version (60s SDK cache).
    """
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(_PROMPT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[atoms.narrate_bundle] prompt_manager lookup for %r failed (%s) — "
            "falling back to inline constant",
            _PROMPT_KEY, exc,
        )
        return _NARRATIVE_SYSTEM_PROMPT_FALLBACK


# Inline fallback — kept in the codebase as the "last resort" prompt for
# bootstrap / test / DB-unreachable paths. The canonical prompt lives in
# prompts/atoms.yaml under the key above. Update both when the prompt
# changes or remove this fallback once Langfuse + DB-only is the
# established norm.
_NARRATIVE_SYSTEM_PROMPT_FALLBACK = """\
You are writing a daily dev diary entry for Glad Labs — a one-person
indie shop building Poindexter, an AI-operated content business.
This is autobiographical: you ARE Glad Labs writing about today's
work for other indie builders who'll find the post on the blog.

Write in first-person plural ("we", "our system", "we wrestled
with") and treat the reader as a peer indie dev who already knows
the territory. Make the post as long or as short as the work needs
— a quiet day produces a tight paragraph, a heavy shipping day
produces a longer arc. Be concise: cut every sentence that doesn't
earn its place. Each paragraph carries weight.

OPERATOR NOTE — THE PERSONALITY ANCHOR:

When the BUNDLE includes an OPERATOR_NOTES section, those notes
are the operator's first-person words about today's work. They
ARE the post's voice and emotional through-line. Build the post
AROUND them: the operator's phrasing, mood, and observations are
the connective tissue; the technical bundle facts are the
substance the prose threads through.

- Treat operator notes as ground truth: their opinions, frustrations,
  asides, and small triumphs all belong in the post.
- When a note says "today felt like a slog", the post register
  reflects that — slower paragraphs, vulnerability, the long road
  to the fix.
- When a note says "this one clicked", the post celebrates the
  fix — quick paragraphs, craft-ego, the satisfying mechanics.
- The operator's phrasing is the seed for opening lines. When
  they wrote "the regex bug felt cursed", lead with that.

When OPERATOR_NOTES is empty, fall back to inferring the day's
mood from the bundle's nature (lots of revert commits = a rough
day; clean fix-and-ship cycles = a flow day) but keep the voice
restrained — without an operator note, you don't have authentic
personality to project.

THE ARC:

1. Open with stakes. Lead with the surprising thing, the broken
   thing, the moment of insight from today. When an operator note
   exists, lead with the operator's framing. Otherwise: "Today's
   biggest fight was X." "We almost shipped Y until we caught Z."
   "We'd been telling ourselves W was fine — today we admitted it
   wasn't." Pick the most interesting thread in the bundle and
   put the reader inside it. Frame around the work itself, not
   around a duration claim.

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

- The BUNDLE block in the user message is the only source of truth.
  Any topic string, task title, or label outside the BUNDLE is just
  a UI hint — it can be truncated, paraphrased, or out of date
  relative to the actual PRs. When the topic string and the BUNDLE
  disagree, the BUNDLE wins. Open the post by referencing a
  specific merged PR from the BUNDLE by its real title and number;
  do not lead with a generic riff on a topic phrase.
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
- URLs: every url comes from the bundle. When you describe a
  specific change that came from a particular PR, link that PR
  inline using its bundle url field — shape is
  "[PR #N](url-from-bundle's-pr-url-field)". Cite at least the
  PRs that anchor each section's main claim. Citations are how
  readers verify the work is real; aim for several inline links
  in the post.

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
    operator_notes = bundle.get("operator_notes") or []

    lines: list[str] = []
    lines.append(f"DATE: {bundle.get('date') or 'today'}")
    lines.append("")
    # Operator notes lead the bundle text — they're the personality
    # anchor the prompt directs the LLM to build the post around.
    if operator_notes:
        lines.append(f"OPERATOR_NOTES ({len(operator_notes)}):")
        lines.append("")
        for n in operator_notes:
            note_text = (n.get("note") or "").strip() if isinstance(n, dict) else str(n).strip()
            mood = (n.get("mood") or "").strip() if isinstance(n, dict) else ""
            if not note_text:
                continue
            if mood:
                lines.append(f'- [{mood}] "{note_text}"')
            else:
                lines.append(f'- "{note_text}"')
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
    task_id = state.get("task_id")
    bundle = state.get("context_bundle")
    bundle_source = "state.context_bundle"
    if not bundle:
        # Fall back to reading from pipeline_versions.stage_data.
        database_service = state.get("database_service")
        if task_id and database_service is not None:
            bundle = await _load_bundle_from_db(database_service.pool, task_id)
            bundle_source = "pipeline_versions.stage_data._dev_diary_bundle"
        else:
            bundle_source = (
                f"unavailable (task_id={'set' if task_id else 'missing'}, "
                f"database_service={'set' if database_service is not None else 'missing'})"
            )
    bundle = bundle or {}
    # Log loud when the bundle is missing or empty — this is the
    # condition that produces "writer riffs on the topic string"
    # output (Glad-Labs/poindexter#354). Warning level so it surfaces
    # in normal log review without being lost to debug noise; on a
    # real shipping day, an empty bundle here means the DB read
    # silently failed and the operator needs to investigate
    # pipeline_versions.stage_data for this task_id.
    pr_count = len(bundle.get("merged_prs") or []) if isinstance(bundle, dict) else 0
    commit_count = len(bundle.get("notable_commits") or []) if isinstance(bundle, dict) else 0
    if not bundle or _bundle_is_empty(bundle):
        logger.warning(
            "[atoms.narrate_bundle] task=%s no usable bundle "
            "(source=%s, prs=%d, commits=%d) — post will short-circuit "
            "to quiet-day text",
            task_id, bundle_source, pr_count, commit_count,
        )
    else:
        logger.info(
            "[atoms.narrate_bundle] task=%s bundle loaded "
            "(source=%s, prs=%d, commits=%d)",
            task_id, bundle_source, pr_count, commit_count,
        )

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
    system_prompt = _resolve_system_prompt()
    # The BUNDLE is the canonical source. Any "topic" string the caller
    # may have stamped on the task row is just a UI label — it can be
    # truncated, semantic-only, or stale relative to the actual PRs in
    # the bundle. The user-message portion repeats the grounding
    # contract here (in addition to the system prompt) so the LLM sees
    # it adjacent to the BUNDLE block, not 5K tokens earlier in the
    # system preamble. Closes Glad-Labs/poindexter#354.
    full_prompt = (
        f"{system_prompt}\n\n"
        f"---\n\n"
        f"BUNDLE (this is the only source of truth — the post is about "
        f"these specific PRs and commits, NOT about any title or topic "
        f"string outside this block):\n\n{bundle_text}\n\n"
        f"---\n\n"
        f"Now write the dev_diary post. Open by referencing a specific "
        f"merged PR from the BUNDLE above by its actual title and "
        f"number — quote the title verbatim or paraphrase tightly from "
        f"the PR body. Every claim about what shipped today comes from "
        f"a PR or commit in the BUNDLE. Cite PRs inline as "
        f"[PR #N](url-from-the-bundle's-url-field). Follow the system "
        f"prompt's voice + grounding rules. Output starts with the "
        f"first letter of paragraph one and ends with the last letter "
        f"of the closing paragraph."
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
      markdown links AND length is non-stub.
    - Drops by deductions:
        - 50 deduction when the prose is empty or the quiet-day
          fallback (no real shipped work to narrate).
        - Citation deduction up to 30 points based on how short of
          target the inline citation count fell. Target is
          ``min(8, N_prs)`` because the writer prompt asks for
          thematic grouping, not enumeration — citing 8 representative
          PRs is sufficient even on busy days with 30+ merges.
        - 20 deduction when word count is below 40 — that's a stub,
          not a post. Long posts are NOT penalized: per
          ``feedback_no_hardcoded_lengths_in_prompts`` length follows
          content. Short days produce short posts; busy days produce
          longer arcs. The score reflects citation + non-stub, not
          length-band conformity.
    - Floor 30 once non-stub prose rendered (the post still ships,
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

    # Stub-length penalty only — no upper bound. Length follows
    # content per feedback_no_hardcoded_lengths_in_prompts.
    if word_count < 40:
        score -= 20

    if not quiet_day and word_count >= 40 and score < 30:
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

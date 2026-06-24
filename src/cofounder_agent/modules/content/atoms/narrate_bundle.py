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

Issue: Glad-Labs/poindexter#362 (Phase 3 atom granularity).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Private-repo URL scrub — defense in depth. Only ``Glad-Labs/poindexter``
# is public; any OTHER repo under the ``Glad-Labs`` org is internal and must
# not leak into published content. We match "any Glad-Labs/<repo> that is NOT
# poindexter" with a negative lookahead instead of naming the private repo —
# that keeps this module free of the internal repo slug, so the public-mirror
# sync's cosmetic internal-slug → ``poindexter`` rewrite has nothing to clobber
# here and the scrub behaves IDENTICALLY on the source repo and the mirror.
# (Hard-coding the slug made the mirror rewrite flip these regexes to match
# ``poindexter`` and scrub the project's own public links — it was reddening
# the mirror's unit-tests run. poindexter#996 follow-up.) The bundle no longer
# carries PR/commit URLs to the LLM and the prompt directs ``(PR #N)`` plain
# citations, but the model can still echo URLs from training data — last line
# before publish.
_PRIV = r"Glad-Labs/(?!poindexter\b)[A-Za-z0-9._-]+"
_PRIVATE_REPO_PULL_INLINE = re.compile(
    r"\[([^]]+)\]\(https?://github\.com/" + _PRIV + r"/pull/(\d+)\)"
)
_PRIVATE_REPO_COMMIT_INLINE = re.compile(
    r"\[([^]]+)\]\(https?://github\.com/" + _PRIV + r"/commit/"
    r"([0-9a-fA-F]{7})[0-9a-fA-F]*\)"
)
_PRIVATE_REPO_PULL_AUTOLINK = re.compile(
    r"<https?://github\.com/" + _PRIV + r"/pull/(\d+)>"
)
_PRIVATE_REPO_COMMIT_AUTOLINK = re.compile(
    r"<https?://github\.com/" + _PRIV + r"/commit/"
    r"([0-9a-fA-F]{7})[0-9a-fA-F]*>"
)
_PRIVATE_REPO_PULL_BARE = re.compile(
    r"https?://github\.com/" + _PRIV + r"/pull/(\d+)"
)
_PRIVATE_REPO_COMMIT_BARE = re.compile(
    r"https?://github\.com/" + _PRIV + r"/commit/"
    r"([0-9a-fA-F]{7})[0-9a-fA-F]*"
)
_PRIVATE_REPO_MENTION = re.compile(r"\b" + _PRIV + r"\b")


def _scrub_private_repo_refs(text: str) -> str:
    """Strip private-repo URLs from generated content.

    Inline markdown links → ``text (PR #N)`` / ``text (`sha7`)``;
    autolinks + bare URLs → ``(PR #N)`` / ``(`sha7`)``; remaining text
    mentions of the private repo path → the public mirror path. Run
    after the LLM emits prose and before the post is published.
    """
    if not text:
        return text
    text = _PRIVATE_REPO_PULL_INLINE.sub(r"\1 (PR #\2)", text)
    text = _PRIVATE_REPO_COMMIT_INLINE.sub(r"\1 (`\2`)", text)
    text = _PRIVATE_REPO_PULL_AUTOLINK.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_AUTOLINK.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_PULL_BARE.sub(r"(PR #\1)", text)
    text = _PRIVATE_REPO_COMMIT_BARE.sub(r"(`\1`)", text)
    text = _PRIVATE_REPO_MENTION.sub("Glad-Labs/poindexter", text)
    return text


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
        FieldSpec(name="title", type="str", description="derived post headline (never a bare date)"),
        FieldSpec(name="model_used", type="str", description="resolved model name"),
        FieldSpec(name="_narrate_bundle_ran", type="bool", description="set to True once executed"),
    ),
    requires=("context_bundle",),
    produces=("content", "title"),
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


# The dev_diary body's H1 is the generated headline (set per render below),
# NOT a fixed string. publish_service.publish_post_from_task derives
# posts.title from the body's first H1 via extract_title_from_content, while
# the preview / approval queue shows the stored pipeline_versions.title — the
# two must agree or the published title reverts to a generic header even
# though preview shows the good headline. The "What we shipped on {date}"
# framing moves below the title as an italic subtitle, so it survives when
# publish strips the leading H1. Guarded by
# test_narrate_bundle.TestRunBodyH1MatchesTitle.
_SUBTITLE_PREFIX = "What we shipped on "
# Footer matches deterministic_compositor (PR #631, 2026-05-27): a
# single public-mirror link so readers who want commit-level detail
# can browse the public repo. That 2026-05-27 fix landed on the OLD
# compositor writer mode; the dev_diary template moved to THIS atom
# (per the file docstring) so the footer change never carried over —
# which is why today's post had 16 private-repo URLs in the body AND
# the old plaintext footer instead of the new public-link footer.
_FOOTER = (
    "_Auto-compiled by Poindexter from today's commits and PRs. "
    "[See the work: github.com/Glad-Labs/poindexter]"
    "(https://github.com/Glad-Labs/poindexter)._"
)


# Prompt key in UnifiedPromptManager + prompt_templates table. The
# default lives at skills/content/atoms/SKILL.md; runtime overrides come
# from the prompt_templates DB row with the matching key. Per
# feedback_prompts_must_be_db_configurable: every prompt is
# DB-configurable; inline constants are tech debt.
_PROMPT_KEY = "atoms.narrate_bundle.system_prompt"


def _resolve_system_prompt(site_config: Any = None) -> tuple[str, str | None, int | None]:
    """Pull the narrate-bundle system prompt + provenance metadata.

    Returns ``(prompt_text, prompt_template_key, prompt_template_version)``
    so the atom can stamp the provenance fields on the outcome row for
    the lab (Phase 0, 2026-05-28). Falls back to ``(_NARRATIVE_SYSTEM_
    PROMPT_FALLBACK, None, None)`` when the prompt registry isn't
    reachable (bootstrap / test) — the None key + version is the right
    signal that no resolved prompt was used.

    Langfuse production label wins > YAML defaults > inline fallback.
    Operators editing the prompt land their changes in the Langfuse
    UI; the next get_prompt call picks up the new version (60s SDK
    cache) and the new version int flows onto every subsequent
    outcome row.

    NOTE: the inline fallback was updated 2026-06-10 to require a
    ``TITLE:`` prefix on the first output line. If the Langfuse /
    YAML prompt still has the old ``OUTPUT: emit only the narrative
    paragraphs`` instruction, the title extraction will fall back to
    the heuristic (first sentence of prose). Update the DB prompt to
    match the new OUTPUT FORMAT section in _NARRATIVE_SYSTEM_PROMPT_FALLBACK.

    ``site_config`` is required to render ``{site_name}``/``{site_url}``
    placeholders via ``get_prompt_resolution``'s format pass. Without
    it, ``get_prompt_resolution`` raises ``KeyError`` on the missing
    vars and falls back to the inline constant. Passing it here avoids
    the error log and ensures the SKILL.md prompt (or a Langfuse
    override) is used rather than always falling back.
    """
    site_name = (site_config.get("site_name") if site_config else "") or ""
    site_url = (site_config.get("site_url") if site_config else "") or ""
    try:
        from services.prompt_manager import get_prompt_manager
        resolution = get_prompt_manager().get_prompt_resolution(
            _PROMPT_KEY, site_name=site_name, site_url=site_url,
        )
        return resolution.text, resolution.key, resolution.version
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[atoms.narrate_bundle] prompt_manager lookup for %r failed (%s) — "
            "falling back to inline constant",
            _PROMPT_KEY, exc,
        )
        return _NARRATIVE_SYSTEM_PROMPT_FALLBACK, None, None


# Inline fallback — kept in the codebase as the "last resort" prompt for
# bootstrap / test / DB-unreachable paths. The canonical prompt lives in
# skills/content/atoms/SKILL.md under the key above. The {site_name} /
# {site_url} placeholders are rendered from the run-bound site_config by
# the caller (see the .format() after _resolve_system_prompt). Update both
# when the prompt changes or remove this fallback once Langfuse + DB-only
# is the established norm.
_NARRATIVE_SYSTEM_PROMPT_FALLBACK = """\
You are writing a daily dev diary entry for {site_name} — a one-person
indie shop building Poindexter, an AI-operated content business.
This is autobiographical: you ARE {site_name} writing about today's
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
   markdown links interchangeably") and cite the PR that fixed it
   inline as plain text ``(PR #231)``. Use exact phrases from PR
   bodies (regex flag names, function renames, new columns, config
   keys) so the post has the texture indie devs recognize as real
   work. Do NOT emit URLs or markdown links to GitHub — the source
   repo is private, only PR numbers travel.

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
- Names: use names that appear verbatim in a bundle entry. "{site_name}",
  "Poindexter", "{site_url}", PR/commit authors, and any
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
- URLs: do NOT emit URLs or markdown links to GitHub. The source
  repo is private, so any github.com link would 404 for public
  readers. Cite PRs as plain text "(PR #N)" — the number alone is
  the provenance citation. Cite commits as plain text using the
  short SHA in backticks, e.g. "(`abc1234`)". Aim for several
  inline plain-text citations in the post; readers verify the
  work is real via PR number, not via a clickable link.

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
            body = (p.get("body") or "").strip()
            for marker in (
                "🤖 Generated with",
                "Co-Authored-By:",
                "Generated with [Claude",
                "Generated-by: Claude",
            ):
                if marker in body:
                    body = body.split(marker, 1)[0].rstrip()
            # Scrub private-repo URLs from the PR body before it
            # reaches the LLM — the source PRs are on the private
            # operator repo, but the public blog cites them as plain
            # text only. Public PR/commit numbers travel; URLs do not.
            body = _scrub_private_repo_refs(body)[:1200]
            lines.append(f"PR #{num}: {title}")
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


# 2026-05-16: the private ``_ollama_chat_text`` + ``_maybe_unwrap_json``
# duplicates were deleted in favor of the shared
# :mod:`services.llm_text` helpers. The shared helper routes through
# the LLM provider dispatcher (so this writer path honors
# ``plugin.llm_provider.primary.standard='litellm'``) AND keeps the
# maybe-unwrap-json defense at the result boundary. The module-level
# aliases keep test patches at the historical name working
# (tests patch ``modules.content.atoms.narrate_bundle._ollama_chat_text``).
from services.llm_text import maybe_unwrap_json as _maybe_unwrap_json
from services.llm_text import ollama_chat_text as _ollama_chat_text


def _bundle_is_empty(bundle: dict[str, Any]) -> bool:
    return not (bundle.get("merged_prs") or bundle.get("notable_commits"))


def _parse_title_and_prose(raw: str, bundle: dict[str, Any], date: str) -> tuple[str, str]:
    """Split the model's ``TITLE: ...\\n\\nprose`` output into (title, prose).

    The prompt asks for a ``TITLE:`` prefix on the first line. When the
    model follows the format, the title is extracted verbatim and the
    prose is everything after the first blank line. When the model ignores
    the format instruction, a heuristic title is derived from:
    1. First sentence of the prose (clipped to 80 chars), or
    2. A bundle-derived summary ("Shipped N PRs on {date}").

    The heuristic guarantees the structural gate's title check always has
    something to evaluate — the date-only pattern check in
    auto_publish_gate._check_structural_requirements will still block a
    title like "2026-06-09", but the heuristic title coming from real
    prose won't look like a date.
    """
    text = raw.strip()
    if not text:
        prs = bundle.get("merged_prs") or []
        n = len(prs)
        return (f"Shipped {n} PR{'s' if n != 1 else ''} today" if n else f"Dev notes — {date}"), ""

    first_line = text.splitlines()[0]
    if first_line.upper().startswith("TITLE:"):
        title = first_line[6:].strip()
        # Everything after the first line is the prose; strip leading blank lines.
        rest = text[len(first_line):].lstrip("\n").strip()
        if title:
            return title, rest
        # Model emitted "TITLE:" with nothing after it — fall through to heuristic.
        text = rest

    # Heuristic: first real sentence from the prose, clipped to 80 chars.
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("_"):
            continue
        # Clip at first sentence-ending punctuation within 80 chars.
        clip = line[:80]
        for i, ch in enumerate(clip):
            if ch in ".!?" and i > 15:
                clip = clip[:i + 1]
                break
        else:
            # No sentence end — clip at last space.
            if len(line) > 80:
                space = clip.rfind(" ")
                clip = clip[:space] if space > 20 else clip
        title = clip.strip().rstrip(".!?,:;")
        if title:
            return title, text
    # Last resort: bundle-derived.
    prs = bundle.get("merged_prs") or []
    n = len(prs)
    return (f"Shipped {n} PR{'s' if n != 1 else ''} today" if n else f"Dev notes — {date}"), text


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node entrypoint.

    Reads the dev_diary bundle from state (placed there by
    ``modules.content.stages.generate_content._read_context_bundle`` upstream
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
        quiet_title = f"Quiet day — {date}"
        body = (
            f"# {quiet_title}\n\n"
            f"Quiet day — no shipped work to report.\n\n"
            f"{_FOOTER}\n"
        )
        return {"content": body, "model_used": "none", "title": quiet_title}

    # DI seam (glad-labs-stack#330) — atoms read site_config from state.
    # 2026-05-12 (poindexter#485): replaced the three-place hardcoded
    # ``glm-4.7-5090:latest`` fallback with the shared resolver, which
    # reads the pipeline_writer_model pin (raise on unset).
    # If the setting doesn't resolve, the atom now fails loud instead of
    # silently trying a model the operator may not have.
    #
    # Phase 1 lab harness — when an experiment assigned a model-axis
    # variant, ``experiment_runner.apply_variant_to_state`` stamps
    # ``writer_model`` onto state. ``resolve_local_model`` accepts the
    # explicit string and returns it after the ``ollama/`` prefix strip
    # so no app_settings hit happens on the variant path. None = inherit
    # the niche default writer model (pipeline_writer_model).
    from services.llm_text import resolve_local_model
    site_config = state.get("site_config")
    model_override = state.get("writer_model")
    model = resolve_local_model(model=model_override, site_config=site_config)

    bundle_text = _format_bundle_for_narrative(bundle)
    # Pass site_config so {site_name}/{site_url} placeholders are rendered
    # inside get_prompt_resolution's format pass (avoids a KeyError that
    # caused every run to fall back to the inline constant instead of the
    # SKILL.md prompt). For the inline fallback path (exception case),
    # _resolve_system_prompt returns the unrendered constant; the .format()
    # call below then fills the same placeholders as before.
    system_prompt, prompt_template_key, prompt_template_version = (
        _resolve_system_prompt(site_config)
    )
    # Inline-fallback path: the returned text still contains {site_name} /
    # {site_url} and needs one more render. SKILL.md / Langfuse path: the
    # text was already rendered inside _resolve_system_prompt; the format()
    # call is a no-op (no remaining placeholders to substitute).
    system_prompt = system_prompt.format(
        site_name=(site_config.get("site_name") if site_config else "") or "",
        site_url=(site_config.get("site_url") if site_config else "") or "",
    )
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
        f"Now write the dev_diary post. First line must be:\n"
        f"TITLE: [specific headline — not a date, not 'Dev diary for ...']\n\n"
        f"Then a blank line, then the narrative. Open the narrative by "
        f"referencing a specific merged PR from the BUNDLE above by its "
        f"actual title and number — quote the title verbatim or "
        f"paraphrase tightly from the PR body. Every claim about what "
        f"shipped today comes from a PR or commit in the BUNDLE. Cite "
        f"PRs inline as plain text `(PR #N)` — do NOT emit URLs or "
        f"markdown links to GitHub, the source repo is private. Follow "
        f"the system prompt's voice + grounding rules."
    )

    # Route through the shared helper so this atom honors the
    # ``plugin.llm_provider.primary.standard`` setting (LiteLLM, etc.).
    # The atom's database_service exposes .pool — pass it through so the
    # call goes via the dispatcher; when running under bootstrap / tests
    # where no database_service is in state, the helper falls back to
    # direct httpx → local Ollama.
    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service is not None else None
    try:
        raw = await _ollama_chat_text(
            full_prompt,
            model=model,
            site_config=site_config,
            pool=pool,
            timeout_setting="niche_ollama_chat_timeout_seconds",
            task_id=task_id,
            phase="narrate_bundle",
        )
    except Exception as exc:
        logger.warning(
            "[atoms.narrate_bundle] LLM call failed: %s — falling back "
            "to one-line summary",
            exc,
        )
        raw = ""

    cleaned_raw = _scrub_private_repo_refs(_maybe_unwrap_json(raw).strip())
    post_title, prose = _parse_title_and_prose(cleaned_raw, bundle, date)
    if not prose:
        # Graceful fallback so the post still ships. Instead of a single
        # "we shipped N PRs" sentence (which produced visibly thin posts
        # whenever the LLM call timed out — see the 2026-05-17 vacation
        # observation), enumerate the actual PR titles and notable
        # commits as a deterministic list. The reader gets a real
        # changelog even when the narrative LLM is unavailable; the
        # quality_score still drops because this isn't founder-voice
        # prose, but the post is no longer just boilerplate.
        prs = bundle.get("merged_prs") or []
        commits = bundle.get("notable_commits") or []
        pr_n = len(prs)
        commit_n = len(commits)
        parts: list[str] = []
        if pr_n:
            parts.append(f"{pr_n} PR{'s' if pr_n != 1 else ''}")
        if commit_n:
            parts.append(f"{commit_n} notable commit{'s' if commit_n != 1 else ''}")
        if not parts:
            prose = "Quiet day — no shipped work to report."
            post_title = f"Quiet day — {date}"
        else:
            header_line = (
                f"The narrative writer was unavailable this run, so here's the "
                f"plain changelog. We shipped {' and '.join(parts)} today."
            )
            pr_lines: list[str] = []
            for p in prs[:25]:
                num = p.get("number") or "?"
                title = (p.get("title") or "").strip() or f"PR #{num}"
                # Plain text — source PRs live on the private operator
                # repo so URLs would 404 for public readers. The PR
                # number alone is enough provenance.
                pr_lines.append(f"- PR #{num}: {title}")
            commit_lines: list[str] = []
            for c in commits[:15]:
                sha = (c.get("sha") or "").strip()
                subject = (c.get("subject") or "").strip()
                if sha and subject:
                    commit_lines.append(f"- `{sha[:7]}` {subject}")
            sections: list[str] = [header_line, ""]
            if pr_lines:
                sections.append("**Merged PRs:**")
                sections.append("")
                sections.extend(pr_lines)
                sections.append("")
            if commit_lines:
                sections.append("**Other commits:**")
                sections.append("")
                sections.extend(commit_lines)
                sections.append("")
            prose = "\n".join(sections).rstrip()
            post_title = f"Shipped {' and '.join(parts)} — {date}"

    # H1 == the generated headline so publish's extract_title_from_content
    # yields the same title preview shows; the date framing drops to an
    # italic subtitle that survives the H1 strip at publish. See
    # _SUBTITLE_PREFIX note above.
    body = (
        f"# {post_title}\n\n"
        f"_{_SUBTITLE_PREFIX}{date}_\n\n"
        f"{prose}\n\n"
        f"{_FOOTER}\n"
    )

    quality_score = _compute_quality_score(bundle, prose, body)

    return {
        "content": body,
        "title": post_title,
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
        # Phase 0 lab observability stamps (2026-05-28). These flow
        # into capability_outcomes via the TemplateRunner's per-node
        # metrics dict — record_run reads them off the record's
        # metrics keys and writes them into the prompt_template_*
        # columns. None when the prompt registry was unreachable
        # (bootstrap / test paths), which is the right signal for
        # the lab view downstream.
        "prompt_template_key": prompt_template_key,
        "prompt_template_version": prompt_template_version,
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

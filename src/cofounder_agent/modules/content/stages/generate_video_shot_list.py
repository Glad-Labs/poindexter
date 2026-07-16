"""GenerateVideoShotListStage — director output for the post's video.

Runs after ``generate_media_scripts`` (which produces the podcast script
the shot list aligns its narration to) and before ``capture_training_data``
/ ``finalize_task``. Calls the LLM director, validates the result against
the Pydantic schema in ``schemas/video_shot_list.py``, persists to
context + audit_log.

This PR (1 of 3) only LANDS the shot list in context + audit_log so the
operator can review director output for a few real posts. PR 2 wires the
shot list into actual video rendering. Design doc:
``docs/architecture/video-composition.md`` (issue #649).

## Context reads

- ``task_id`` (str), ``title`` (str), ``content`` (str)
- ``podcast_script`` (str, produced by ``generate_media_scripts``)
- ``platform`` (capability handle — ``config`` for model/site_name reads +
  ``dispatch`` for the LLM call; Seam 1 Waves 3d/3e, #667)
- ``database_service`` (DI seam — pool for the LLM dispatcher)

## Context writes

- ``video_shot_list`` (dict) — JSON-serialized VideoShotList; absent on
  failure (downstream stages treat absent as "no director output, fall
  back to legacy renderer")
- ``stages["video_shot_list"]`` (bool)

## Audit log

Writes ``video_director.shot_list_produced`` on success or
``video_director.shot_list_failed`` on failure. Operator-visible in
the audit_log dashboard so they can monitor director quality without
grepping container logs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from plugins.stage import StageResult
from schemas.video_shot_list import VideoShotList

logger = logging.getLogger(__name__)


_DEFAULT_TARGET_DURATION_S = 60.0  # Fallback when podcast script length unknown
_WORDS_PER_SECOND = 2.5  # Rough TTS narration pace; ~150 WPM


# Per-call ceiling for the director LLM dispatch. Writer-grade director models
# (e.g. gemma-4-31B, the prod default) emit a full structured shot list
# (max_tokens 6144) and routinely need well over the old hardcoded 120s — the
# call was timing out at exactly 120.0s and leaving an empty shot list, so
# Stage-2 video never rendered. Tunable via the ``video_director_timeout_seconds``
# DB setting; this is just the seed default for operators on faster models.
_DIRECTOR_TIMEOUT_DEFAULT = 300


# Output-token ceiling for the director dispatch. With thinking disabled
# (_resolve_director_think) the whole budget goes to the JSON shot list, but a
# verbose director occasionally serializes a long list (30 shots × long
# intent/prompt strings) past the old hardcoded 6144, truncating the JSON
# mid-object → _extract_json_object finds no complete object → empty shot list →
# no video (2026-07-07: a real 300s post, d1979ebb, truncated this way even with
# think=False). 8192 gives headroom; DB-tunable via ``video_director_max_tokens``.
_DIRECTOR_MAX_TOKENS_DEFAULT = 8192

# Retries when the director emits no extractable JSON object (the truncation /
# empty-output symptom). A fresh generation almost always lands a complete list
# — cheaper than losing the whole post's video to one unlucky run. DB-tunable via
# ``video_director_max_retries``; 0 disables retrying. Dispatch *exceptions*
# (infra) never retry here — only a structurally-empty result does.
_DIRECTOR_MAX_RETRIES_DEFAULT = 1


def _resolve_director_think(cfg: Any) -> bool | None:
    """``think`` flag for the director's (and reviewer's) LLM dispatches.

    Returns ``False`` (disable the reasoning channel) when
    ``video_director_disable_thinking`` is on — the default. The director model
    (the gemma-4-31B-it-qat QAT default) is thinking-capable: with the channel
    live, its reasoning shares the ``num_predict`` (6144) output budget, and
    once the reasoning runs long it STARVES the JSON shot list — ``content``
    comes back empty, the dispatcher falls back to the reasoning text, and
    ``_extract_json_object`` finds no object, so the shot list is lost and
    Stage-2 video never renders (audit: ``shot_list_failed`` phase=json_extract
    with a reasoning-prose preview; 2026-07-07 investigation reproduced the
    empty-``content`` overflow in-container). Disabling thinking frees the whole
    budget for the structured output — the same fix the writer path took via
    ``writer_disable_thinking`` (#2163).

    Returns ``None`` (leave the backend default — thinking on) only when an
    operator explicitly sets the flag off. ``think`` is forwarded to Ollama for
    local models and dropped for cloud targets by the LiteLLM provider, so
    ``False`` is safe for a non-thinking or cloud director model too.
    """
    if cfg is None:
        return False
    try:
        raw = cfg.get("video_director_disable_thinking", "true")
        return False if str(raw).lower() in ("true", "1", "yes") else None
    except Exception:  # noqa: BLE001 — optional feature flag, never load-bearing
        # silent-ok: default to disabling thinking (the safe, reliable path)
        return False


def _estimate_target_duration(podcast_script: str) -> float:
    """Estimate the podcast's spoken duration from word count.

    The director needs a target duration. The actual podcast file may
    not exist yet at this stage (podcast gen is post-publish), so we
    estimate from the script: ~2.5 words/second is a reasonable TTS
    narration pace.

    Clamped to [20, 300] seconds — shorter than 20s isn't worth the
    director call; longer than 300s exceeds the renderer's practical
    output length.
    """
    if not podcast_script:
        return _DEFAULT_TARGET_DURATION_S
    word_count = len(podcast_script.split())
    estimated = word_count / _WORDS_PER_SECOND
    return max(20.0, min(estimated, 300.0))


_DEFAULT_SHORT_DURATION_S = 20.0  # Fallback when short script length unknown


def _estimate_short_duration(short_script: str, target_seconds: float = 45.0) -> float:
    """Estimate the short-form clip duration from word count.

    Same ~2.5 words/second estimate as the long-form director, but clamped to
    [15, ``target_seconds``] — the short-form retention window. Below 15s isn't
    enough to land a hook + payoff; above the target a "short" stops being short
    and viewers scroll. ``target_seconds`` is the operator-tunable short target
    (``video_short_target_seconds``, threaded in at the call site; issue #867) —
    the upper bound was a hardcoded 45. If empty, return the 20s default.
    """
    if not short_script:
        return _DEFAULT_SHORT_DURATION_S
    word_count = len(short_script.split())
    estimated = word_count / _WORDS_PER_SECOND
    return max(15.0, min(estimated, target_seconds))


def _extract_json_object(text: str) -> str | None:
    """Strip prose / code-fence wrappers and return the JSON object body.

    LLMs occasionally ignore the "no prose" instruction and emit
    ```json ... ``` or "Here is the shot list: { ... }". Trim those.
    Returns ``None`` when no plausible JSON object is found.
    """
    if not text:
        return None

    # Code-fence strip (poindexter#643 — canonical shared helper). The old
    # inline regex here was non-greedy, so it matched the SHORTEST span
    # between the opening fence and the FIRST later ``` — truncating at an
    # embedded triple-backtick inside a string value instead of the real
    # closing fence.
    from services.llm_text import strip_markdown_fence

    stripped = strip_markdown_fence(text.strip())

    # Find the first '{' and the matching '}' — naive bracket-counting.
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(stripped[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start:i + 1]
    return None


# Python-literal spellings the director leaks into JSON output.
_JSON_REPAIR_LITERALS = {"True": "true", "False": "false", "None": "null"}


def _repair_json_syntax(text: str) -> str:
    """Deterministically rewrite the near-JSON dialects local directors emit
    into strict JSON.

    Observed dialects (audit ``shot_list_failed`` phase=schema_validate,
    "Expecting property name enclosed in double quotes", raw preview
    ``{ sho…``): unquoted object keys. Also repaired while we're scanning:
    single-quoted strings, Python literals (``True``/``False``/``None``),
    bare-word string values, and trailing commas.

    A character scanner that tracks string context, so repairs never touch
    the inside of a double-quoted string value. Best-effort by design: the
    output goes straight back to ``json.loads`` — anything still malformed
    fails there exactly like an unrepaired parse does today. Deterministic
    repair beats an LLM retry burning writer-grade GPU time
    (``feedback_calculated_vs_generated``).
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            # Single-quoted string → double-quoted (escaping inner ``"``).
            j = i + 1
            buf: list[str] = []
            while j < n:
                cj = text[j]
                if cj == "\\" and j + 1 < n:
                    buf.append(cj)
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if cj == "'":
                    break
                buf.append('\\"' if cj == '"' else cj)
                j += 1
            out.append('"')
            out.extend(buf)
            out.append('"')
            i = j + 1
            continue
        if ch.isalpha() or ch == "_":
            # Bare identifier: an unquoted key, a Python/JSON literal, or a
            # bare-word string value.
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            k = j
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] == ":":
                out.append(f'"{word}"')  # unquoted key
            elif word in _JSON_REPAIR_LITERALS:
                out.append(_JSON_REPAIR_LITERALS[word])
            elif word in ("true", "false", "null"):
                out.append(word)
            else:
                # Bare word in value position (after ``:`` / ``[`` / ``,``)
                # is an unquoted string value — quote it. Anywhere else,
                # leave it for json.loads to reject.
                prev = next((c for c in reversed(out) if not c.isspace()), "")
                out.append(f'"{word}"' if prev in (":", "[", ",") else word)
            i = j
            continue
        if ch in "}]":
            # Trailing comma: drop a comma directly preceding the closer.
            k = len(out) - 1
            while k >= 0 and out[k].isspace():
                k -= 1
            if k >= 0 and out[k] == ",":
                del out[k]
        out.append(ch)
        i += 1
    return "".join(out)


def _tolerant_json_loads(body: str) -> Any:
    """``json.loads`` with a deterministic dialect-repair fallback.

    Strict parse first — well-formed output never passes through the
    rewriter. On a parse error, repair the known local-model dialects
    (see :func:`_repair_json_syntax`) and parse again, letting a still-
    broken result raise to the caller's existing failure path.
    """
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        parsed = json.loads(_repair_json_syntax(body))
        logger.info(
            "[VIDEO_DIRECTOR] tolerant JSON repair recovered a shot list "
            "the strict parser rejected (unquoted keys / trailing commas / "
            "Python literals)",
        )
        return parsed


_MAX_SHOTS = 30  # Mirror VideoShotList.shots max_length — cap before validation.
_MAX_SHOT_DURATION_S = 30.0  # Mirror Shot.duration_s le=30 — clamp before validation.


def _reconcile_shot_list(parsed: Any) -> Any:
    """Deterministically repair the director's mechanical slips before schema
    validation.

    The local "standard"-tier LLM reliably botches several pieces of
    bookkeeping the ``VideoShotList`` schema enforces as hard gates — and
    every botch rejected the WHOLE shot list, so Stage-2 video rendering
    silently no-opped on every post (audit: ``shot_list_failed`` 11 /
    ``shot_list_produced`` 0 over 10 days; the 2026-06/07 follow-up audit
    added ``duration_s > 30`` and missing ``narration_offset_s`` as the two
    dominant remaining reject classes). These constraints are *calculated*,
    not creative, so we compute them here rather than throw away the
    director's otherwise-usable shot choices
    (``feedback_calculated_vs_generated``). Creative fields (sources,
    prompts, queries, intents) are never touched.

    Repairs, in order:

    1. **Duration clamp** — cap ``duration_s`` at the schema ceiling (30s).
       The director emits 30.2–46s "hold" shots; the shot *choice* is
       creative, its length cap is bookkeeping.
    2. **Pacing** — break runs of >2 consecutive same-source non-holdover shots
       by inserting a ``holdover`` transition (a pure cross-fade, no asset).
    3. **Count** — cap to the schema max (30 shots); the director front-loads
       the hook, so dropping the tail is the least-bad truncation.
    4. **Index** — renormalize ``idx`` to a contiguous ``0..n-1`` (the renderer
       concats in idx order; the schema requires no gaps).
    5. **Narration offset** — a shot missing ``narration_offset_s`` (a
       required field the director drops on individual shots) gets it derived
       from the cumulative duration of all prior shots — the exact rule the
       director prompt states. Present offsets are never overwritten.
    6. **Total duration** — set ``total_duration_s`` to the sum of shot
       durations so the director can never disagree with its own arithmetic.

    A non-dict input, or a dict with no usable ``shots`` list, is returned
    untouched — so a genuinely-empty director output still flows to the
    stage's failure path rather than being fabricated into a fake success.
    """
    if not isinstance(parsed, dict):
        return parsed
    raw_shots = parsed.get("shots")
    if not isinstance(raw_shots, list):
        return parsed
    shots = [s for s in raw_shots if isinstance(s, dict)]
    if not shots:
        return parsed

    # 1. Clamp per-shot duration to the schema ceiling. Non-numeric values
    #    are left alone — the total loop below counts them as 0.0 and logs.
    for shot in shots:
        raw_duration = shot.get("duration_s")
        if (
            isinstance(raw_duration, (int, float))
            and not isinstance(raw_duration, bool)
            and raw_duration > _MAX_SHOT_DURATION_S
        ):
            shot["duration_s"] = _MAX_SHOT_DURATION_S

    # 2. Break same-source streaks with holdover transitions.
    paced: list[dict[str, Any]] = []
    streak_src: str | None = None
    streak = 0
    for shot in shots:
        src = shot.get("source")
        if src == "holdover":
            streak_src, streak = None, 0
            paced.append(shot)
            continue
        if src == streak_src:
            streak += 1
            if streak > 2:
                # No narration_offset_s here — the reindex loop below derives
                # it from cumulative prior durations, which is more accurate
                # than copying the following shot's (possibly absent) offset.
                paced.append({
                    "duration_s": 0.5,
                    "intent": "transition",
                    "source": "holdover",
                })
                streak = 1  # this shot opens a fresh run after the transition
        else:
            streak_src, streak = src, 1
        paced.append(shot)

    # 3. Cap to the schema max.
    paced = paced[:_MAX_SHOTS]

    # 4 + 5 + 6. Reindex contiguously, derive missing narration offsets from
    # the running duration sum, and recompute the total from the durations.
    total = 0.0
    for i, shot in enumerate(paced):
        shot["idx"] = i
        try:
            float(shot["narration_offset_s"])
        except (KeyError, TypeError, ValueError):
            # Required field the director dropped — derive it as the
            # cumulative duration of all prior shots (the prompt's own rule).
            shot["narration_offset_s"] = round(total, 2)
        raw_duration = shot.get("duration_s")
        try:
            total += float(raw_duration or 0.0)
        except (TypeError, ValueError):
            # A non-numeric duration_s contributes 0.0 to the total — the
            # per-shot schema validator rejects the shot downstream anyway, so
            # we don't fail reconciliation here. Log it so the bad director
            # output is diagnosable rather than silently swallowed.
            logger.debug(
                "[VIDEO_DIRECTOR] reconcile: non-numeric duration_s %r at "
                "idx=%d — counting as 0.0", raw_duration, i,
            )

    parsed["shots"] = paced
    parsed["total_duration_s"] = round(total, 1)
    return parsed


async def _log_audit(
    pool: Any,
    *,
    event_type: str,
    task_id: str | None,
    details: dict[str, Any],
    severity: str = "info",
) -> None:
    """Insert an audit_log row. Best-effort — a failure here MUST NOT
    take the stage down (the stage's whole point is logging+context;
    failing on the log itself would be the worst outcome).
    """
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, task_id, details, severity)
            VALUES ($1, 'video_director_stage', $2, $3::jsonb, $4)
            """,
            event_type, task_id, json.dumps(details), severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VIDEO_DIRECTOR] audit_log insert failed (%s): %s",
            event_type, exc,
        )


class GenerateVideoShotListStage:
    """Pipeline stage: produce a video shot list via LLM director."""

    name = "generate_video_shot_list"
    description = "Director — produce shot list (sources, prompts, durations) for the post's video"
    # Two lanes (long + short), each up to (1 + max_retries) LLM dispatches
    # capped at the ``video_director_timeout_seconds`` DB setting (default
    # _DIRECTOR_TIMEOUT_DEFAULT). This stage-level budget only bites on the
    # legacy template_runner path — canonical_blog runs on graph_def, which
    # enforces the per-call ceiling inside _produce_shot_list — so keep it
    # comfortably above the worst-case dispatch count (2 lanes × retry attempts)
    # so it never pre-empts the dispatches it wraps.
    timeout_seconds = (1 + _DIRECTOR_MAX_RETRIES_DEFAULT) * 2 * _DIRECTOR_TIMEOUT_DEFAULT + 80
    halts_on_failure = False  # Director failure shouldn't block the post

    async def _produce_shot_list(
        self,
        *,
        platform: Any,
        pool: Any,
        model: str,
        script: str,
        target_duration_s: float,
        prompt_key: str,
        task_id: str | None,
        now_iso: str,
        title: str,
        content_text: str,
        site_name: str,
        timeout_s: int,
        think: bool | None = None,
        max_tokens: int = _DIRECTOR_MAX_TOKENS_DEFAULT,
        max_retries: int = 0,
    ) -> dict[str, Any] | None:
        """Render the director prompt, dispatch the LLM, validate the result.

        Returns the validated ``shot_list.model_dump(mode="json")`` on
        success or ``None`` on any failure (logging the same audit events
        the inline flow used to). Shared by the long-form
        (``video.director_v1``, ``podcast_script=...``) and short-form
        (``video.director_short_v1``, ``short_script=...``) calls — the
        template variable the script binds to is keyed off ``prompt_key``.
        """
        # The two director prompts name the narration script differently:
        # the long prompt uses {podcast_script}, the short prompt uses
        # {short_script}. Bind the script under the right template var.
        script_param = (
            "short_script" if prompt_key == "video.director_short_v1"
            else "podcast_script"
        )

        # Render the director prompt via UnifiedPromptManager so edits
        # land in Langfuse + the YAML defaults stay in repo control.
        try:
            from services.prompt_manager import get_prompt_manager
            pm = get_prompt_manager()
            rendered_prompt = pm.get_prompt(
                prompt_key,
                title=title,
                content=content_text,
                target_duration_s=f"{target_duration_s:.1f}",
                model=model,
                now_iso=now_iso,
                # Operator brand templated into the director persona via
                # {site_name} (migrated to skills/content/video-director).
                site_name=site_name,
                **{script_param: script},
            )
        except Exception as exc:
            logger.warning(
                "[VIDEO_DIRECTOR] prompt render failed (%s, key=%s) — skipping",
                exc, prompt_key,
            )
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={"phase": "prompt_render", "prompt_key": prompt_key, "error": str(exc)},
                severity="warning",
            )
            return None

        # Dispatch the LLM call (Seam 1 Wave 3d, #667 — via the handle).
        from services.gpu_scheduler import gpu

        # Disable the director model's reasoning channel (default) — a
        # thinking-capable model (the gemma-4-31B-it-qat director) otherwise
        # spends the shared output budget reasoning and starves the JSON shot
        # list (empty content → json_extract failure; 2026-07-07). Only forward
        # ``think`` when resolved — skip on None so an operator opt-out leaves
        # the backend default rather than pinning it. Mirrors the writer path
        # (#2163).
        think_kwargs: dict[str, Any] = {} if think is None else {"think": think}

        # Retry loop: a director that emits no extractable JSON object — the
        # truncation symptom (a verbose list serialized past max_tokens, cut
        # mid-object) or a rare empty return — gets a fresh generation before we
        # give up. Losing the whole post's video to one unlucky run costs far
        # more than a second dispatch (2026-07-07: d1979ebb truncated its 300s
        # list even with think=False). Dispatch *exceptions* (infra) fail fast —
        # they are NOT retried here.
        director_output = ""
        json_body: str | None = None
        attempts = 1 + max(0, max_retries)
        for attempt in range(attempts):
            try:
                async with gpu.lock(
                    "ollama", model=model,
                    task_id=task_id, phase="video_director",
                ):
                    result = await platform.dispatch.complete(
                        pool=pool,
                        messages=[{"role": "user", "content": rendered_prompt}],
                        model=model,
                        tier="standard",
                        timeout_s=timeout_s,
                        temperature=0.4,  # Lower temp — director should be decisive
                        # With thinking disabled the whole budget is the JSON; a
                        # verbose 30-shot list can still serialize long, so this
                        # is DB-tunable (video_director_max_tokens) with headroom.
                        max_tokens=max_tokens,
                        **think_kwargs,
                    )
                director_output = (getattr(result, "text", "") or "").strip()
            except Exception as exc:
                logger.warning(
                    "[VIDEO_DIRECTOR] LLM dispatch failed (%s, key=%s) — skipping",
                    exc, prompt_key,
                )
                await _log_audit(
                    pool,
                    event_type="video_director.shot_list_failed",
                    task_id=task_id,
                    details={
                        "phase": "llm_dispatch",
                        "prompt_key": prompt_key,
                        "error": str(exc),
                        # The effective per-call ceiling — so a future timeout
                        # failure shows at a glance whether the DB setting
                        # actually reached the dispatch (2026-06-20 incident:
                        # "Timeout passed=120.0" rows from a stale worker still
                        # running the pre-#1750 hardcoded 120s).
                        "timeout_s": timeout_s,
                    },
                    severity="warning",
                )
                return None

            # Parse + validate. The schema enforces all the pacing rules
            # (no >2 consecutive same source, duration sum matches total,
            # idx contiguous from 0). No complete object means truncation /
            # empty output — retry with a fresh generation if attempts remain.
            json_body = _extract_json_object(director_output)
            if json_body:
                break
            if attempt < attempts - 1:
                logger.info(
                    "[VIDEO_DIRECTOR] no JSON object in output (%s, attempt "
                    "%d/%d, %d chars) — retrying",
                    prompt_key, attempt + 1, attempts, len(director_output),
                )

        if not json_body:
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={
                    "phase": "json_extract",
                    "prompt_key": prompt_key,
                    "attempts": attempts,
                    "raw_output_preview": director_output[:500],
                },
                severity="warning",
            )
            return None

        try:
            # Tolerant parse (strict first, then deterministic dialect
            # repair — unquoted keys were rejecting whole shot lists), then
            # repair the director's mechanical/arithmetic slips (duration
            # clamp, missing narration offsets, count cap, idx, total sum,
            # pacing streaks) BEFORE validation — the local LLM botches this
            # bookkeeping every run, and an unrepaired reject silently
            # no-ops Stage-2 video. Creative fields untouched.
            parsed = _tolerant_json_loads(json_body)
            parsed = _reconcile_shot_list(parsed)
            shot_list = VideoShotList.model_validate(parsed)
        except Exception as exc:
            await _log_audit(
                pool,
                event_type="video_director.shot_list_failed",
                task_id=task_id,
                details={
                    "phase": "schema_validate",
                    "prompt_key": prompt_key,
                    "error": str(exc),
                    "raw_output_preview": json_body[:500],
                },
                severity="warning",
            )
            return None

        await _log_audit(
            pool,
            event_type="video_director.shot_list_produced",
            task_id=task_id,
            details={
                "model": model,
                "prompt_key": prompt_key,
                "aspect": shot_list.aspect,
                "shot_count": len(shot_list.shots),
                "total_duration_s": shot_list.total_duration_s,
                "sources": [s.source for s in shot_list.shots],
            },
        )
        logger.info(
            "[VIDEO_DIRECTOR] %s: %d shots produced (aspect=%s, total=%.1fs, sources=%s)",
            prompt_key,
            len(shot_list.shots),
            shot_list.aspect,
            shot_list.total_duration_s,
            ",".join(s.source for s in shot_list.shots),
        )
        return shot_list.model_dump(mode="json")

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        title = context.get("title", "")
        content_text = context.get("content", "")
        podcast_script = context.get("podcast_script", "")
        task_id = context.get("task_id")

        if not content_text or not title:
            return StageResult(
                ok=True,
                detail="nothing to direct (missing content or title)",
                metrics={"skipped": True},
            )
        if not podcast_script:
            # The director needs the narration script to align shot
            # durations to. Without it, we'd produce a shot list that
            # didn't actually correspond to any audio.
            return StageResult(
                ok=True,
                detail="no podcast_script in context — director skipped",
                metrics={"skipped": True},
            )

        database_service = context.get("database_service")
        pool = getattr(database_service, "pool", None) if database_service else None
        # Seam 1 Wave 3d (#667): LLM completion via the capability handle.
        platform = context.get("platform")

        if pool is None or platform is None:
            # Tests / bootstrap path — no DB / no kernel handle → no LLM call.
            # The stage is non-critical so this is fine.
            return StageResult(
                ok=True,
                detail="no DB pool / Platform handle in context — director skipped",
                metrics={"skipped": True},
            )

        # Seam 1 Wave 3e (#667): config reads go through the handle. The guard
        # above guarantees ``platform`` is non-None here, so no None-tolerance
        # dance is needed — ``platform.config`` is the only config seam now.
        cfg = platform.config

        # Per-step model pin — the director uses video_director_model, falling
        # back to video_scene_model then default_ollama_model. "auto"/unset is
        # treated as "no model" → skip the (non-critical) director stage. The
        # cost_tier.standard.model fallback was removed.
        model = (
            cfg.get("video_director_model")
            or cfg.get("video_scene_model")
            or cfg.get("default_ollama_model")
        )
        if not model or model == "auto":
            logger.warning(
                "generate_video_shot_list: no director model configured "
                "(video_director_model / video_scene_model / "
                "default_ollama_model unset or 'auto') — director skipped",
            )
            return StageResult(
                ok=True,
                detail="no director model configured — director skipped",
                metrics={"skipped": True},
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        site_name = cfg.get("site_name") or ""
        # Per-call LLM ceiling — DB-tunable so writer-grade director models get
        # the headroom they need (see _DIRECTOR_TIMEOUT_DEFAULT). The old
        # hardcoded 120s timed out gemma-4-31B and left an empty shot list, so
        # Stage-2 video never rendered.
        director_timeout = cfg.get_int(
            "video_director_timeout_seconds", _DIRECTOR_TIMEOUT_DEFAULT
        )
        # Disable the reasoning channel (default) so the thinking-capable
        # director model doesn't starve its own JSON output — see
        # _resolve_director_think.
        director_think = _resolve_director_think(cfg)
        # Output-token ceiling + retry budget — DB-tunable so a verbose director
        # that truncates its JSON gets headroom + a fresh attempt rather than
        # silently losing the post's video (see the module constants).
        director_max_tokens = cfg.get_int(
            "video_director_max_tokens", _DIRECTOR_MAX_TOKENS_DEFAULT
        )
        director_max_retries = cfg.get_int(
            "video_director_max_retries", _DIRECTOR_MAX_RETRIES_DEFAULT
        )

        # LONG (unchanged behavior): the 16:9 director over podcast_script.
        long_shot_list = await self._produce_shot_list(
            platform=platform,
            pool=pool,
            model=model,
            script=podcast_script,
            target_duration_s=_estimate_target_duration(podcast_script),
            prompt_key="video.director_v1",
            task_id=task_id,
            now_iso=now_iso,
            title=title,
            content_text=content_text,
            site_name=site_name,
            timeout_s=director_timeout,
            think=director_think,
            max_tokens=director_max_tokens,
            max_retries=director_max_retries,
        )

        if long_shot_list is None:
            # The long-form director failed — the stage is non-critical, so
            # record + skip (per-phase audit was already logged in the helper).
            return StageResult(
                ok=True,
                detail="director did not produce a long-form shot list",
                metrics={"failed": True},
            )

        # SHORT (new, best-effort): the purpose-built 9:16 vertical director
        # over short_summary_script. A short failure must NOT affect the long
        # result or halt the stage — so we only add short_shot_list when the
        # short script is present AND the helper returns a validated dict.
        short_summary_script = context.get("short_summary_script", "")
        short_shot_list = None
        if short_summary_script:
            short_shot_list = await self._produce_shot_list(
                platform=platform,
                pool=pool,
                model=model,
                script=short_summary_script,
                target_duration_s=_estimate_short_duration(
                    short_summary_script,
                    cfg.get_int("video_short_target_seconds", 45),
                ),
                prompt_key="video.director_short_v1",
                task_id=task_id,
                now_iso=now_iso,
                title=title,
                content_text=content_text,
                site_name=site_name,
                timeout_s=director_timeout,
                think=director_think,
                max_tokens=director_max_tokens,
                max_retries=director_max_retries,
            )

        # Success — return via context_updates so it survives the graph_def
        # state merge (#674: direct context writes are dropped on graph_def).
        stages = context.setdefault("stages", {})
        stages["video_shot_list"] = True

        context_updates: dict[str, Any] = {
            "video_shot_list": long_shot_list,
            "stages": stages,
        }
        metrics: dict[str, Any] = {
            "shot_count": len(long_shot_list["shots"]),
            "total_duration_s": long_shot_list["total_duration_s"],
        }
        if short_shot_list is not None:
            stages["short_shot_list"] = True
            context_updates["short_shot_list"] = short_shot_list
            metrics["short_shot_count"] = len(short_shot_list["shots"])

        detail = (
            f"{len(long_shot_list['shots'])} shots, "
            f"{long_shot_list['total_duration_s']:.1f}s total"
        )
        if short_shot_list is not None:
            detail += f" (+ {len(short_shot_list['shots'])}-shot 9:16 short)"

        return StageResult(
            ok=True,
            detail=detail,
            context_updates=context_updates,
            metrics=metrics,
        )

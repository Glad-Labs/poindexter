"""Community draft assistant — subreddit profiles + draft store + Reddit
value-post generation (WS2). On-demand, draft-only, no auto-posting.

Profiles carry each community's rules/tone/post-type norms and the classifier
content_types it accepts; drafts are native founder-voice posts the operator
reviews and posts manually. See
docs/superpowers/specs/2026-07-13-community-draft-assistant-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from services.llm_text import ollama_chat_text, resolve_local_model

_PROFILE_COLS = (
    "subreddit, enabled, content_types, post_type, self_promo, flair, "
    "min_karma, min_account_age_days, rules_summary, tone_notes, cadence_cap_days"
)


@dataclass
class SubredditProfile:
    subreddit: str
    enabled: bool = True
    content_types: list[str] = field(default_factory=list)
    post_type: str = "text"          # 'text' | 'link' | 'either'
    self_promo: str = "strict"       # 'strict' | 'moderate' | 'ok'
    flair: str | None = None
    min_karma: int | None = None
    min_account_age_days: int | None = None
    rules_summary: str = ""
    tone_notes: str = ""
    cadence_cap_days: int | None = None


@dataclass
class CommunityDraft:
    id: int
    target: str                       # 'reddit:<sub>' | 'indiehackers'
    title: str | None
    body: str
    post_type: str
    source_post_id: str | None        # uuid as str
    warnings: list[str]
    status: str                       # 'draft' | 'posted' | 'discarded'
    posted_url: str | None
    model: str | None


def _row_to_profile(row: Any) -> SubredditProfile:
    return SubredditProfile(
        subreddit=row["subreddit"], enabled=row["enabled"],
        content_types=list(row["content_types"]), post_type=row["post_type"],
        self_promo=row["self_promo"], flair=row["flair"],
        min_karma=row["min_karma"], min_account_age_days=row["min_account_age_days"],
        rules_summary=row["rules_summary"], tone_notes=row["tone_notes"],
        cadence_cap_days=row["cadence_cap_days"],
    )


async def list_profiles(pool: Any, *, enabled_only: bool = False) -> list[SubredditProfile]:
    where = "WHERE enabled " if enabled_only else ""
    rows = await pool.fetch(
        f"SELECT {_PROFILE_COLS} FROM subreddit_profiles {where}ORDER BY subreddit"
    )
    return [_row_to_profile(r) for r in rows]


async def get_profile(pool: Any, subreddit: str) -> SubredditProfile | None:
    row = await pool.fetchrow(
        f"SELECT {_PROFILE_COLS} FROM subreddit_profiles WHERE subreddit = $1", subreddit
    )
    return _row_to_profile(row) if row else None


async def add_profile(pool: Any, profile: SubredditProfile) -> bool:
    """INSERT a new profile; returns True if created, False if the subreddit
    already existed (ON CONFLICT DO NOTHING — never clobbers a curated row)."""
    tag = await pool.execute(
        "INSERT INTO subreddit_profiles "
        "(subreddit, enabled, content_types, post_type, self_promo, flair, "
        " min_karma, min_account_age_days, rules_summary, tone_notes, cadence_cap_days) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
        "ON CONFLICT (subreddit) DO NOTHING",
        profile.subreddit, profile.enabled, profile.content_types, profile.post_type,
        profile.self_promo, profile.flair, profile.min_karma,
        profile.min_account_age_days, profile.rules_summary, profile.tone_notes,
        profile.cadence_cap_days,
    )
    return tag.endswith("1")


async def update_profile(pool: Any, profile: SubredditProfile) -> bool:
    """Full-row UPDATE keyed on subreddit; returns True if a row matched."""
    tag = await pool.execute(
        "UPDATE subreddit_profiles SET enabled=$2, content_types=$3, post_type=$4, "
        "self_promo=$5, flair=$6, min_karma=$7, min_account_age_days=$8, "
        "rules_summary=$9, tone_notes=$10, cadence_cap_days=$11, updated_at=now() "
        "WHERE subreddit=$1",
        profile.subreddit, profile.enabled, profile.content_types, profile.post_type,
        profile.self_promo, profile.flair, profile.min_karma,
        profile.min_account_age_days, profile.rules_summary, profile.tone_notes,
        profile.cadence_cap_days,
    )
    return tag.endswith("1")


async def edit_profile(pool: Any, subreddit: str, **changes: Any) -> SubredditProfile:
    """Load the existing profile, apply only the non-None ``changes``, and write
    it back. Raises ``KeyError`` when the subreddit has no profile."""
    existing = await get_profile(pool, subreddit)
    if existing is None:
        raise KeyError(subreddit)
    applied = {k: v for k, v in changes.items() if v is not None}
    merged = replace(existing, **applied)
    await update_profile(pool, merged)
    return merged


async def set_profile_enabled(pool: Any, subreddit: str, enabled: bool) -> bool:
    tag = await pool.execute(
        "UPDATE subreddit_profiles SET enabled=$2, updated_at=now() WHERE subreddit=$1",
        subreddit, enabled,
    )
    return tag.endswith("1")


async def remove_profile(pool: Any, subreddit: str) -> bool:
    tag = await pool.execute(
        "DELETE FROM subreddit_profiles WHERE subreddit=$1", subreddit
    )
    return tag.endswith("1")


# --------------------------------------------------------------- content match

async def suggest_subreddits_for_post(pool: Any, post_id: str) -> list[str]:
    """Enabled profiles whose accepted content_types overlap the post's
    classifier labels (WS1.5 ``post_content_types``). Deterministic routing —
    no LLM. A post the classifier hasn't labelled yet suggests nothing."""
    rows = await pool.fetch(
        "SELECT subreddit FROM subreddit_profiles "
        "WHERE enabled AND content_types && ("
        "  SELECT COALESCE(array_agg(content_type), '{}') "
        "  FROM post_content_types WHERE post_id = $1"
        ") ORDER BY subreddit",
        post_id,
    )
    return [r["subreddit"] for r in rows]


# ------------------------------------------------------------------- draft CRUD

_DRAFT_COLS = (
    "id, target, title, body, post_type, source_post_id, warnings, status, posted_url, model"
)


def _row_to_draft(row: Any) -> CommunityDraft:
    src = row["source_post_id"]
    return CommunityDraft(
        id=row["id"], target=row["target"], title=row["title"], body=row["body"],
        post_type=row["post_type"], source_post_id=str(src) if src else None,
        warnings=list(row["warnings"]), status=row["status"],
        posted_url=row["posted_url"], model=row["model"],
    )


async def create_draft(pool: Any, *, target: str, body: str, title: str | None = None,
                       post_type: str = "text", source_post_id: str | None = None,
                       warnings: list[str] | None = None, model: str | None = None) -> int:
    return await pool.fetchval(
        "INSERT INTO community_post_drafts "
        "(target, title, body, post_type, source_post_id, warnings, model) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
        target, title, body, post_type, source_post_id, warnings or [], model,
    )


async def list_drafts(pool: Any, *, status: str | None = None) -> list[CommunityDraft]:
    if status:
        rows = await pool.fetch(
            f"SELECT {_DRAFT_COLS} FROM community_post_drafts WHERE status = $1 ORDER BY id DESC",
            status,
        )
    else:
        rows = await pool.fetch(
            f"SELECT {_DRAFT_COLS} FROM community_post_drafts ORDER BY id DESC"
        )
    return [_row_to_draft(r) for r in rows]


async def get_draft(pool: Any, draft_id: int) -> CommunityDraft | None:
    row = await pool.fetchrow(
        f"SELECT {_DRAFT_COLS} FROM community_post_drafts WHERE id = $1", draft_id
    )
    return _row_to_draft(row) if row else None


async def edit_draft(pool: Any, draft_id: int, *, title: str | None = None,
                     body: str | None = None) -> bool:
    """Update title and/or body. COALESCE keeps the existing value when an arg is
    None, so a partial edit only touches what the operator passed."""
    if title is None and body is None:
        return False
    tag = await pool.execute(
        "UPDATE community_post_drafts "
        "SET title = COALESCE($2, title), body = COALESCE($3, body), updated_at = now() "
        "WHERE id = $1",
        draft_id, title, body,
    )
    return tag.endswith("1")


async def mark_posted(pool: Any, draft_id: int, *, url: str) -> bool:
    tag = await pool.execute(
        "UPDATE community_post_drafts SET status = 'posted', posted_url = $2, "
        "updated_at = now() WHERE id = $1",
        draft_id, url,
    )
    return tag.endswith("1")


async def discard_draft(pool: Any, draft_id: int) -> bool:
    tag = await pool.execute(
        "UPDATE community_post_drafts SET status = 'discarded', updated_at = now() "
        "WHERE id = $1",
        draft_id,
    )
    return tag.endswith("1")


# ----------------------------------------------------------- reddit generation

_REDDIT_PROMPT_KEY = "community.reddit_value_post"


def compute_warnings(profile: SubredditProfile) -> list[str]:
    """Deterministic advisory constraints surfaced to the operator (never
    enforced — the operator is the poster)."""
    out: list[str] = []
    if profile.self_promo == "strict":
        out.append("self-promo=strict: post as native text, no blog link")
    elif profile.self_promo == "moderate":
        out.append("self-promo=moderate: link only if it genuinely helps the reader")
    if profile.flair:
        out.append(f"set flair: {profile.flair}")
    if profile.min_karma is not None:
        out.append(f"min karma: {profile.min_karma}")
    if profile.min_account_age_days is not None:
        out.append(f"min account age: {profile.min_account_age_days} days")
    if profile.cadence_cap_days is not None:
        out.append(f"cadence: avoid posting here more than once per "
                   f"{profile.cadence_cap_days} days")
    return out


def _site_url(site_config: Any) -> str:
    return (site_config.get("site_url", "") or "").strip().rstrip("/")


def maybe_append_blog_link(body: str, *, post_type: str, slug: str,
                           site_config: Any) -> str:
    """Deterministically append the canonical blog link ONLY when the subreddit
    allows it (post_type link/either) AND a base URL is configured. Keeps the
    link decision out of the LLM's hands so it's testable and never a bare
    link-drop in a text-only sub."""
    if post_type not in ("link", "either"):
        return body
    base = _site_url(site_config)
    if not base:
        return body
    return f"{body}\n\n---\nFull write-up: {base}/posts/{slug}"


def _resolve_reddit_prompt(**kwargs: Any) -> str:
    from services.prompt_manager import get_prompt_manager
    return get_prompt_manager().get_prompt(_REDDIT_PROMPT_KEY, **kwargs)


async def generate_reddit_draft(pool: Any, *, post_id: str, subreddit: str,
                                site_config: Any) -> CommunityDraft:
    """Generate a native founder-voice Reddit value-post for a published post,
    store it as a draft, and return it. The LLM writes only the body; the
    blog-link decision and the advisory warnings are computed deterministically."""
    profile = await get_profile(pool, subreddit)
    if profile is None:
        raise KeyError(f"no subreddit profile for {subreddit!r}")
    post = await pool.fetchrow(
        "SELECT title, content, slug FROM posts WHERE id = $1 AND status = 'published'",
        post_id,
    )
    if post is None:
        raise ValueError(f"no published post {post_id!r}")
    ctype_rows = await pool.fetch(
        "SELECT content_type FROM post_content_types WHERE post_id = $1", post_id
    )
    content_types = ", ".join(r["content_type"] for r in ctype_rows) or "(unclassified)"

    prompt = _resolve_reddit_prompt(
        title=post["title"], content=post["content"], content_types=content_types,
        rules_summary=profile.rules_summary, tone_notes=profile.tone_notes,
        post_type=profile.post_type, self_promo=profile.self_promo,
        flair=profile.flair or "(none)",
    )
    pin = (site_config.get("community_draft_model", "") or "").strip() or None
    model = resolve_local_model(pin, site_config=site_config)
    raw = await ollama_chat_text(
        prompt, model=model, site_config=site_config, pool=pool, tier="standard",
        phase="community_reddit_draft",
        timeout_setting="community_draft_timeout_seconds", timeout_default=180.0,
        think=False,
    )
    body = maybe_append_blog_link(
        raw.strip(), post_type=profile.post_type, slug=post["slug"],
        site_config=site_config,
    )
    draft_id = await create_draft(
        pool, target=f"reddit:{subreddit}", body=body, title=post["title"],
        post_type=profile.post_type, source_post_id=post_id,
        warnings=compute_warnings(profile), model=model,
    )
    draft = await get_draft(pool, draft_id)
    if draft is None:  # pragma: no cover — the row was just inserted
        raise RuntimeError(f"community draft {draft_id} vanished after insert")
    return draft

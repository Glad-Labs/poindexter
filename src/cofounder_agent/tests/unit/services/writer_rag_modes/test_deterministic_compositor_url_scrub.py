"""Defense-in-depth: the dev_diary deterministic_compositor must never emit
URLs pointing at the private glad-labs-stack repo.

Why this matters: ``dev_diary_source`` reads PR metadata from
``app_settings.gh_repo`` which on production is set to
``Glad-Labs/glad-labs-stack`` (the public poindexter mirror is force-pushed
code only — it has no PRs/issues of its own). PR bodies in the bundle
context can contain stack URLs that leak into the writer narrative if
nothing scrubs them on the way out. Linking to the private repo from a
public post both 404s every reader AND advertises the private repo's
existence (see ``feedback_no_operator_info_to_public_repo``).

The compositor is the last seam before the post leaves the writer.
``_strip_private_repo_urls`` is the guard. These tests pin its
behavior — markdown-style links, autolink-style references, and commit
URLs in both formats all collapse to plain-text equivalents that
preserve the reference (``PR #N``, ``` `sha7` ```) without the link.
"""

from __future__ import annotations

import pytest

from services.writer_rag_modes.deterministic_compositor import (
    _FOOTER,
    _strip_private_repo_urls,
    compose_post,
)

pytestmark = pytest.mark.asyncio


def test_strip_markdown_pr_link() -> None:
    text = "We shipped [PR #602](https://github.com/Glad-Labs/glad-labs-stack/pull/602) today."
    out = _strip_private_repo_urls(text)
    assert out == "We shipped PR #602 today."
    assert "glad-labs-stack" not in out


def test_strip_autolink_pr_reference() -> None:
    text = "- <https://github.com/Glad-Labs/glad-labs-stack/pull/578>"
    out = _strip_private_repo_urls(text)
    assert out == "- PR #578"
    assert "github.com" not in out


def test_strip_markdown_commit_link() -> None:
    text = "Tweaked in [`abc1234`](https://github.com/Glad-Labs/glad-labs-stack/commit/abc1234) earlier."
    out = _strip_private_repo_urls(text)
    assert out == "Tweaked in `abc1234` earlier."
    assert "glad-labs-stack" not in out


def test_strip_autolink_commit_reference() -> None:
    text = "Commit <https://github.com/Glad-Labs/glad-labs-stack/commit/0123456789abcdef0123456789abcdef01234567> landed."
    out = _strip_private_repo_urls(text)
    assert "<http" not in out
    assert "glad-labs-stack" not in out
    assert "0123456789abcdef0123456789abcdef01234567" in out


def test_strip_preserves_unrelated_urls() -> None:
    text = (
        "We linked [the docs](https://gladlabs.io/blog/x) and "
        "[poindexter#481](https://github.com/Glad-Labs/poindexter/issues/481) — "
        "scrubbing only targets glad-labs-stack."
    )
    out = _strip_private_repo_urls(text)
    assert out == text  # No changes — neither URL is stack


def test_strip_handles_empty_and_none() -> None:
    assert _strip_private_repo_urls("") == ""
    # type: ignore[arg-type] — covering the defensive guard
    assert _strip_private_repo_urls(None) is None  # type: ignore[arg-type]


def test_footer_links_to_public_repo() -> None:
    """The footer is the only URL in a dev_diary post — points to the
    PUBLIC mirror (poindexter), never the private stack repo. Matt's
    direction 2026-05-27: drop the per-PR Sources list; readers who
    want commit-level detail follow this single link."""
    assert "github.com/Glad-Labs/poindexter" in _FOOTER
    assert "glad-labs-stack" not in _FOOTER
    # The footer is wrapped in italic markdown emphasis so it visually
    # separates from the narrative body.
    assert _FOOTER.startswith("_")
    assert _FOOTER.endswith("._")


async def test_compose_post_strips_narrative_url_leaks(monkeypatch) -> None:
    """If the narrative LLM hallucinates a stack URL, the final post still
    ships URL-free. Covers the writer-narrative leak path, not just the
    deterministic links section."""

    async def fake_generate_narrative(bundle, *, site_config=None, pool=None):
        # Simulate a model that copies a PR body URL into prose.
        return (
            "We shipped [PR #602](https://github.com/Glad-Labs/glad-labs-stack/pull/602) "
            "and noted commit <https://github.com/Glad-Labs/glad-labs-stack/commit/abc1234>."
        )

    monkeypatch.setattr(
        "services.writer_rag_modes.deterministic_compositor._generate_narrative",
        fake_generate_narrative,
    )

    bundle = {
        "date": "2026-05-27",
        "merged_prs": [
            {"number": 602, "title": "x", "url": "https://github.com/Glad-Labs/glad-labs-stack/pull/602"},
        ],
        "notable_commits": [],
    }
    post = await compose_post(bundle, site_config=None, pool=None)
    assert "glad-labs-stack" not in post
    assert "PR #602" in post
    # The header + footer survive intact.
    assert "What we shipped on 2026-05-27" in post
    assert "Auto-compiled by Poindexter" in post

"""Unit tests for the YouTube-attribution core (``_youtube_attribution``).

Pure, network-free primitives shared by ``content.reconcile_citations``'s
YouTube pass (Matt 2026-06-23: "we just want proper attribution and not a raw
youtube url"). The atom resolves channel names via the YouTube oEmbed endpoint
(``author_name``); these helpers do the deterministic detection + rewrite given
an already-resolved ``{url: author}`` map, so the logic is testable without a
network call.

Reference case (post 4a4b9054, "Undervolting your GPU"): the writer cited a
real video — https://www.youtube.com/watch?v=MhnVyMry9BU by the real channel
"ImWateringPSUs" — but the rewrite must turn a bare URL into a proper
``[ImWateringPSUs](url)`` attribution and never touch a link the writer already
attributed with human text.
"""

from __future__ import annotations

from modules.content.atoms._youtube_attribution import (
    apply_youtube_attribution,
    find_youtube_urls,
)

_URL = "https://www.youtube.com/watch?v=MhnVyMry9BU"
_SHORT = "https://youtu.be/MhnVyMry9BU"
_AUTHOR = "ImWateringPSUs"


# --- find_youtube_urls ------------------------------------------------------

def test_find_youtube_watch_url():
    urls = find_youtube_urls(f"See {_URL} for the tutorial.")
    assert _URL in urls


def test_find_youtube_short_and_shorts_urls():
    content = (
        "Clip: https://youtu.be/MhnVyMry9BU and "
        "a short https://www.youtube.com/shorts/abc12345678 here."
    )
    urls = find_youtube_urls(content)
    assert "https://youtu.be/MhnVyMry9BU" in urls
    assert "https://www.youtube.com/shorts/abc12345678" in urls


def test_find_youtube_dedupes_preserving_order():
    content = f"{_URL} ... again {_URL}"
    assert find_youtube_urls(content) == [_URL]


def test_find_youtube_ignores_non_video_urls():
    # A channel URL has no 11-char video id → not an oEmbeddable video → skipped.
    content = "Channel: https://www.youtube.com/@ImWateringPSUs and https://example.com/x"
    assert find_youtube_urls(content) == []


def test_find_youtube_empty():
    assert find_youtube_urls("") == []
    assert find_youtube_urls(None) == []  # type: ignore[arg-type]


# --- apply_youtube_attribution ----------------------------------------------

def test_apply_wraps_bare_watch_url():
    content = f"For the newest hardware, see {_URL} for the steps."
    new, changes = apply_youtube_attribution(content, {_URL: _AUTHOR})
    assert f"[{_AUTHOR}]({_URL})" in new
    assert "see https://" not in new  # the raw URL is gone
    assert changes and changes[0]["author"] == _AUTHOR


def test_apply_wraps_bare_short_url():
    content = f"Clip: {_SHORT}."
    new, changes = apply_youtube_attribution(content, {_SHORT: _AUTHOR})
    assert new == f"Clip: [{_AUTHOR}]({_SHORT})."


def test_apply_relabels_link_with_raw_url_text():
    content = f"[{_URL}]({_URL}) covers it."
    new, _ = apply_youtube_attribution(content, {_URL: _AUTHOR})
    assert new == f"[{_AUTHOR}]({_URL}) covers it."


def test_apply_relabels_link_with_video_id_text():
    content = f"[MhnVyMry9BU]({_URL}) explains undervolting."
    new, _ = apply_youtube_attribution(content, {_URL: _AUTHOR})
    assert new == f"[{_AUTHOR}]({_URL}) explains undervolting."


def test_apply_leaves_human_attributed_link_untouched():
    # The writer already gave proper human attribution → never overwrite it.
    content = f"[YouTube creator ImWateringPSUs]({_URL}) notes a 10C drop."
    new, changes = apply_youtube_attribution(content, {_URL: _AUTHOR})
    assert new == content
    assert changes == []


def test_apply_leaves_bare_url_when_author_unknown():
    # oEmbed failed for this URL (absent from the map) → fail-soft, leave as-is.
    content = f"See {_URL} for the steps."
    new, changes = apply_youtube_attribution(content, {})
    assert new == content
    assert changes == []


def test_apply_does_not_double_wrap_good_link_href():
    content = f"[great tutorial]({_URL}) is worth a watch."
    new, changes = apply_youtube_attribution(content, {_URL: _AUTHOR})
    # human text "great tutorial" stays; the href is not re-wrapped.
    assert new == content
    assert changes == []


def test_apply_handles_query_params_on_bare_url():
    url = "https://www.youtube.com/watch?v=MhnVyMry9BU&t=42s"
    content = f"Jump to {url} for the setting."
    new, _ = apply_youtube_attribution(content, {url: _AUTHOR})
    assert f"[{_AUTHOR}]({url})" in new


def test_apply_is_idempotent():
    content = f"See {_URL} now."
    once, first = apply_youtube_attribution(content, {_URL: _AUTHOR})
    twice, second = apply_youtube_attribution(once, {_URL: _AUTHOR})
    assert once == twice
    assert first and second == []


def test_apply_noop_without_authors():
    content = f"See {_URL}."
    assert apply_youtube_attribution(content, {}) == (content, [])
    assert apply_youtube_attribution("", {_URL: _AUTHOR}) == ("", [])

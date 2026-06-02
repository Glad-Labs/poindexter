"""sanitize_published_title strips test-harness batch/debug suffixes.

Audit 2026-06-02 (issue #4): 2 published posts had titles like
"... (2026-05-11 17:48 batch C #5)" — test-harness topic suffixes that
leaked to the live site (112-117 char titles). Strip them at publish time
so they can never reach a live title again. Conservative: only date / batch /
overnight markers are stripped; legit parentheticals are preserved.
"""
from services.publish_service import sanitize_published_title


def test_strips_date_overnight_suffix():
    assert (
        sanitize_published_title(
            "How embedding models rank similarity (2026-05-11 15:33 overnight B #1)"
        )
        == "How embedding models rank similarity"
    )


def test_strips_date_batch_suffix():
    assert (
        sanitize_published_title(
            "Why Your Favorite Indie Game Stopped Getting Updates (2026-05-11 17:48 batch C #5)"
        )
        == "Why Your Favorite Indie Game Stopped Getting Updates"
    )


def test_strips_bare_batch_suffix():
    assert sanitize_published_title("Indie Game Updates (batch C #5)") == "Indie Game Updates"


def test_leaves_clean_title_untouched():
    assert sanitize_published_title("FastAPI Async Patterns That Matter") == (
        "FastAPI Async Patterns That Matter"
    )


def test_preserves_legit_parenthetical():
    # No date / batch / overnight marker -> must NOT be stripped.
    assert (
        sanitize_published_title("Understanding Big-O (A Practical Guide)")
        == "Understanding Big-O (A Practical Guide)"
    )


def test_handles_none_and_empty():
    assert sanitize_published_title(None) == ""
    assert sanitize_published_title("") == ""

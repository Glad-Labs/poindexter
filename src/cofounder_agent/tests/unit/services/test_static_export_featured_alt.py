"""Static export emits featured_image_alt so the frontend can use it for
og:image:alt (issue A4 / inline+featured alt accuracy work)."""
import datetime

from services.static_export_service import _post_full, _post_summary

_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "title": "T",
    "slug": "t",
    "content": "body",
    "excerpt": "e",
    "featured_image_url": "https://r2/f.png",
    "cover_image_url": None,
    "author_id": None,
    "category_id": None,
    "status": "published",
    "seo_title": "T",
    "seo_description": "d",
    "seo_keywords": "k",
    "featured_image_alt": "Abstract cyan and navy geometric cover",
    "tags": [],
    "published_at": datetime.datetime(2026, 6, 2, tzinfo=datetime.timezone.utc),
    "created_at": datetime.datetime(2026, 6, 2, tzinfo=datetime.timezone.utc),
    "updated_at": datetime.datetime(2026, 6, 2, tzinfo=datetime.timezone.utc),
}


def test_post_summary_includes_featured_image_alt():
    out = _post_summary(_ROW)
    assert out["featured_image_alt"] == "Abstract cyan and navy geometric cover"


def test_post_full_includes_featured_image_alt():
    out = _post_full(_ROW)
    assert out["featured_image_alt"] == "Abstract cyan and navy geometric cover"


def test_featured_image_alt_absent_is_none():
    row = dict(_ROW)
    del row["featured_image_alt"]
    assert _post_summary(row)["featured_image_alt"] is None

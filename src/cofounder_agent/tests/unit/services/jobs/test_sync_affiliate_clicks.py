"""T9 — SyncAffiliateClicksJob row-mapper.

The AE → DB glue (CF SQL API, dedup insert, rollup, watermark) is edge-runtime
integration verified in staging. What has real logic worth locking down is
`_row_to_click`: it drops codeless rows and derives the source post slug from
the referrer's `/posts/<slug>` path.
"""

from services.jobs.sync_affiliate_clicks import _row_to_click


def test_row_to_click_maps_fields():
    raw = {
        "code": "mercury",
        "referrer": "https://gladlabs.io/posts/x",
        "country": "US",
        "user_agent": "UA",
        "created_at": "2026-07-11 10:00:00",
    }
    c = _row_to_click(raw)
    assert c is not None
    assert c["code"] == "mercury"
    assert c["post_slug"] == "x"  # derived from the referrer path
    assert c["country"] == "US"
    assert c["created_at_raw"] == "2026-07-11 10:00:00"


def test_row_to_click_skips_codeless():
    assert _row_to_click({"code": ""}) is None
    assert _row_to_click({}) is None


def test_row_to_click_slug_none_without_post_path():
    c = _row_to_click({"code": "mercury", "referrer": "https://gladlabs.io/about"})
    assert c is not None
    assert c["post_slug"] is None


def test_row_to_click_slug_ignores_querystring_and_fragment():
    c = _row_to_click({"code": "m", "referrer": "https://gladlabs.io/posts/my-slug?utm=x#top"})
    assert c is not None
    assert c["post_slug"] == "my-slug"


def test_row_to_click_empty_optional_fields_become_none():
    c = _row_to_click({"code": "m", "referrer": "", "country": "", "user_agent": ""})
    assert c is not None
    assert c["referrer"] is None
    assert c["country"] is None
    assert c["user_agent"] is None
    assert c["post_slug"] is None

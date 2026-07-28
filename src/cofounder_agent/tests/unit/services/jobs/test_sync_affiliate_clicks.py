"""T9 — SyncAffiliateClicksJob row-mapper.

The AE → DB glue (CF SQL API, dedup insert, rollup, watermark) is edge-runtime
integration verified in staging. What has real logic worth locking down is
`_row_to_click`: it drops codeless rows and derives the source post slug from
the referrer's `/posts/<slug>` path.
"""

from services.jobs.sync_affiliate_clicks import (
    _DEFAULT_BOT_UA_PATTERN,
    _classify_bot,
    _row_to_click,
)


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


def test_emit_bad_timestamp_finding_aggregates(monkeypatch):
    from services.jobs import sync_affiliate_clicks as m

    calls = []
    monkeypatch.setattr(m, "emit_finding", lambda **kw: calls.append(kw))

    m._emit_bad_timestamp_finding(0)
    assert calls == []

    m._emit_bad_timestamp_finding(2)
    assert len(calls) == 1
    assert calls[0]["kind"] == "affiliate_click_parse_skipped"
    assert calls[0]["severity"] == "info"
    assert calls[0]["extra"]["skipped"] == 2


# --- bot classification (poindexter#930) ------------------------------------
# Crawlers were 52% of /go clicks and were rolled into affiliate_links.clicks,
# roughly doubling every per-link total the operator saw.


def test_classify_bot_flags_real_crawlers_seen_in_prod():
    """Every UA here was pulled from the live affiliate_link_clicks table."""
    for ua in (
        "LinkupBot/1.0 (LinkupBot for web indexing; https://linkup.so)",
        "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
        "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot)",
        "Mozilla/5.0 (compatible; jscrawler/0.1; +https://github.com/x)",
        "curl/8.19.0",
    ):
        is_bot, reason = _classify_bot(ua, _DEFAULT_BOT_UA_PATTERN)
        assert is_bot is True, ua
        assert reason == "ua:pattern"


def test_classify_bot_passes_real_browsers():
    for ua in (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15",
    ):
        assert _classify_bot(ua, _DEFAULT_BOT_UA_PATTERN) == (False, None), ua


def test_classify_bot_treats_missing_ua_as_non_human():
    # A redirect endpoint hit with no UA is not a browser; labelled separately
    # from a pattern match so the two causes stay distinguishable.
    assert _classify_bot(None, _DEFAULT_BOT_UA_PATTERN) == (True, "ua:missing")
    assert _classify_bot("", _DEFAULT_BOT_UA_PATTERN) == (True, "ua:missing")


def test_classify_bot_falls_back_when_operator_regex_is_invalid():
    # An unparseable operator pattern must not let crawlers through as human.
    is_bot, reason = _classify_bot("AhrefsBot/7.0", "((((unclosed")
    assert is_bot is True
    assert reason == "ua:pattern"


def test_classify_bot_is_case_insensitive():
    assert _classify_bot("CURL/8.0", _DEFAULT_BOT_UA_PATTERN)[0] is True
    assert _classify_bot("SomeBOT/2", _DEFAULT_BOT_UA_PATTERN)[0] is True


def test_classify_bot_honours_a_widened_operator_pattern():
    # Operators widen the setting to exclude a newly-spotted crawler without
    # waiting for a release. "MysteryTool" carries none of the default tokens.
    ua = "MysteryTool/2.0"
    assert _classify_bot(ua, _DEFAULT_BOT_UA_PATTERN) == (False, None)
    assert _classify_bot(ua, r"(mysterytool|bot)")[0] is True


def test_classify_bot_default_pattern_biases_toward_undercounting():
    """Substring tokens are intentionally broad.

    "fetch" catches node-fetch and anything self-describing as a fetcher.
    Mislabelling a human as a bot undercounts clicks; letting a crawler
    through inflates them, which is what poindexter#930 was about — so the
    pattern errs toward exclusion, matching the bias the page_views sweep
    already documents.
    """
    assert _classify_bot("MysteryFetcher/2.0", _DEFAULT_BOT_UA_PATTERN)[0] is True
    assert _classify_bot("node-fetch/3.3", _DEFAULT_BOT_UA_PATTERN)[0] is True

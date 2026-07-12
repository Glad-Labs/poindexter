"""sync_cloudflare_analytics — best-effort visibility.

A batch that skips rows with unparseable timestamps must surface ONE aggregate
finding (not per-row noise) so a silent CF-AE timestamp-format drift is caught.
"""


def test_emit_bad_timestamp_finding_aggregates(monkeypatch):
    from services.jobs import sync_cloudflare_analytics as m

    calls = []
    monkeypatch.setattr(m, "emit_finding", lambda **kw: calls.append(kw))

    m._emit_bad_timestamp_finding(0)
    assert calls == []  # nothing skipped -> no finding

    m._emit_bad_timestamp_finding(3)
    assert len(calls) == 1  # ONE finding for the whole batch, not per row
    assert calls[0]["kind"] == "analytics_row_parse_skipped"
    assert calls[0]["severity"] == "info"
    assert calls[0]["extra"]["skipped"] == 3

"""Unit tests for subreddit_profiles CSV import/export."""
from __future__ import annotations

from services.community_drafts import SubredditProfile
from services.subreddit_import import (
    import_csv,
    parse_content_types_cell,
    profiles_to_csv,
    row_to_profile,
)

_HEADER = ("subreddit,enabled,content_types,post_type,self_promo,flair,"
           "min_karma,min_account_age_days,rules_summary,tone_notes,cadence_cap_days")


def test_parse_content_types_semicolon():
    assert parse_content_types_cell("ai-ml; pc-hardware ;") == ["ai-ml", "pc-hardware"]
    assert parse_content_types_cell("") == []


def test_row_to_profile_maps_all_columns():
    p = row_to_profile({
        "subreddit": "LocalLLaMA", "enabled": "true", "content_types": "ai-ml;pc-hardware",
        "post_type": "text", "self_promo": "strict", "flair": "Discussion",
        "min_karma": "100", "min_account_age_days": "30",
        "rules_summary": "No memes.", "tone_notes": "Technical.", "cadence_cap_days": "7",
    })
    assert p.subreddit == "LocalLLaMA" and p.enabled is True
    assert p.content_types == ["ai-ml", "pc-hardware"]
    assert p.min_karma == 100 and p.cadence_cap_days == 7


def test_row_to_profile_blank_ints_are_none():
    p = row_to_profile({"subreddit": "X", "min_karma": "", "cadence_cap_days": ""})
    assert p.min_karma is None and p.cadence_cap_days is None
    assert p.enabled is True                 # missing enabled defaults true


def test_profiles_to_csv_roundtrips_content_types():
    p = SubredditProfile(subreddit="X", content_types=["ai-ml", "gaming"], min_karma=50)
    text = profiles_to_csv([p])
    assert text.splitlines()[0] == _HEADER
    # content_types serialized ';'-joined so it survives a single CSV cell
    assert "ai-ml;gaming" in text
    reparsed = row_to_profile(
        dict(zip(_HEADER.split(","), text.splitlines()[1].split(","), strict=True))
    )
    assert reparsed.content_types == ["ai-ml", "gaming"] and reparsed.min_karma == 50


# --- import_csv against a fake pool ---
class _ImpPool:
    def __init__(self, existing=None):
        self.existing = set(existing or [])

    async def fetchval(self, sql, *a):        # existence check
        return 1 if a[0] in self.existing else None


async def test_import_creates_then_skips_without_force(tmp_path, monkeypatch):
    import services.subreddit_import as si
    added = []

    async def _add(pool, profile):
        added.append(profile.subreddit)
        return True
    monkeypatch.setattr(si, "add_profile", _add)

    csv_file = tmp_path / "subs.csv"
    csv_file.write_text(_HEADER + "\nLocalLLaMA,true,ai-ml,text,strict,,,,,,", encoding="utf-8")

    pool = _ImpPool(existing=set())
    rep = await import_csv(pool, str(csv_file))
    assert [r.status for r in rep.rows] == ["created"] and added == ["LocalLLaMA"]

    pool2 = _ImpPool(existing={"LocalLLaMA"})
    rep2 = await import_csv(pool2, str(csv_file))
    assert [r.status for r in rep2.rows] == ["skipped_exists"]


async def test_import_force_updates(tmp_path, monkeypatch):
    import services.subreddit_import as si
    updated = []

    async def _upd(pool, profile):
        updated.append(profile.subreddit)
        return True
    monkeypatch.setattr(si, "update_profile", _upd)

    csv_file = tmp_path / "subs.csv"
    csv_file.write_text(_HEADER + "\nLocalLLaMA,true,ai-ml,text,strict,,,,,,", encoding="utf-8")
    pool = _ImpPool(existing={"LocalLLaMA"})
    rep = await import_csv(pool, str(csv_file), force=True)
    assert [r.status for r in rep.rows] == ["updated"] and updated == ["LocalLLaMA"]


async def test_import_malformed_row_is_error_and_batch_continues(tmp_path, monkeypatch):
    import services.subreddit_import as si

    async def _add(pool, profile):
        if profile.subreddit == "bad":
            raise ValueError("boom")
        return True
    monkeypatch.setattr(si, "add_profile", _add)
    csv_file = tmp_path / "subs.csv"
    csv_file.write_text(
        _HEADER + "\nbad,true,ai-ml,text,strict,,,,,,\ngood,true,ai-ml,text,strict,,,,,,",
        encoding="utf-8",
    )
    rep = await import_csv(_ImpPool(), str(csv_file))
    statuses = {r.subreddit: r.status for r in rep.rows}
    assert statuses == {"bad": "error", "good": "created"}
    assert len(rep.errors) == 1

"""``poindexter auto-publish`` — wiring + niche-aware status payload.

The status surface was dev_diary-hardcoded until 2026-08-14; when the
glad-labs niche started its dry-run ramp the command showed only dev_diary
keys and summarised gate state from dev_diary alone. These tests pin the
niche-aware contract: every ``{niche}_auto_publish_*`` family is grouped and
summarised independently, and non-niche keys never masquerade as a niche.
"""

from poindexter.cli.app import main
from poindexter.cli.auto_publish import (
    _build_status_payload,
    _group_by_niche,
    _niche_state,
    auto_publish_group,
)


def test_group_registered():
    assert "auto-publish" in main.commands


def test_subcommands():
    assert {"status", "trend", "decisions", "veto"} <= set(
        auto_publish_group.commands
    )


def test_group_by_niche_splits_families():
    settings = {
        "dev_diary_auto_publish_threshold": "69",
        "dev_diary_auto_publish_dry_run": "false",
        "glad-labs_auto_publish_threshold": "85",
        "glad-labs_auto_publish_dry_run": "true",
        "glad-labs_auto_publish_min_clean_runs": "5",
    }
    niches = _group_by_niche(settings)
    assert set(niches) == {"dev_diary", "glad-labs"}
    assert niches["dev_diary"]["threshold"] == "69"
    assert niches["glad-labs"]["min_clean_runs"] == "5"


def test_group_by_niche_ignores_non_niche_keys():
    settings = {
        "auto_publish_threshold": "0",  # global legacy gate — no niche stem
        "seo.refresh.auto_publish_after_clean_runs": "5",  # dot infix, not _
        "dev_diary_auto_publish_threshold": "69",
    }
    assert set(_group_by_niche(settings)) == {"dev_diary"}


def test_niche_state_disabled_dry_run_live():
    assert _niche_state("x", {}).startswith("DISABLED")
    assert _niche_state("x", {"threshold": "-1"}).startswith("DISABLED")
    assert _niche_state("x", {"threshold": "85"}).startswith("DRY-RUN")
    assert _niche_state(
        "x", {"threshold": "85", "dry_run": "true"}
    ).startswith("DRY-RUN")
    assert _niche_state(
        "x", {"threshold": "69", "dry_run": "false"}
    ).startswith("LIVE")
    # Unparseable threshold fails closed to DISABLED.
    assert _niche_state("x", {"threshold": "nan-ish?"}).startswith("DISABLED")


def test_status_payload_summarises_every_niche():
    settings = {
        "dev_diary_auto_publish_threshold": "69",
        "dev_diary_auto_publish_dry_run": "false",
        "glad-labs_auto_publish_threshold": "85",
        "glad-labs_auto_publish_dry_run": "true",
        "auto_publish_threshold": "0",
    }
    payload = _build_status_payload(settings, {"total": 3}, 7)
    assert set(payload["niches"]) == {"dev_diary", "glad-labs"}
    assert payload["niches"]["dev_diary"]["state"].startswith("LIVE")
    assert payload["niches"]["glad-labs"]["state"].startswith("DRY-RUN")
    # Backcompat summary key mentions both niches.
    assert "dev_diary" in payload["live_or_dry_run"]
    assert "glad-labs" in payload["live_or_dry_run"]
    assert payload["publishes_last_7d"] == 7
    assert payload["last_24h_decisions"] == {"total": 3}


def test_status_payload_no_niches():
    payload = _build_status_payload({"auto_publish_threshold": "0"}, {}, 0)
    assert payload["niches"] == {}
    assert payload["live_or_dry_run"] == "no niche has auto-publish keys"

"""The settings audit's blind spots — four from poindexter#915, one found here.

A sweep over all 1,276 live keys intersected "no code reference" AND "never
read" and produced 107 candidates — of which **65 were live**. Deleting them
would have silently dropped operator alert routing to defaults.

Each miss had a different cause, and none of them is visible by reading the
tool's output: the give-away is a key it never mentions.

| blind spot | example | why it was missed |
| --- | --- | --- |
| non-Python readers | `offsite_backup_keep_daily` | read by `run.sh` via psql; `.sh` was not scanned |
| multi-segment dynamic suffix | `memory_stale_threshold_seconds_collapse_job` | prefix split on the LAST `_` only |
| bulk raw SQL | `findings.<kind>.delivery` (59) | read by `WHERE key LIKE 'findings.%.%'` |
| seeded short-circuit | the `daily_spend_limit` class (#913) | seeded keys never had a reference computed |

The last one is structural rather than a missing pattern: the tool bucketed any
seeded key as LIVE-SEEDED and stopped, so it *could not* find another #913.

A fifth turned up while verifying those four, and it points the other way:
`plugin.job.<name>.<attr>` keys were matched whole against the job set, so
config for LIVE jobs was reported DEAD. A false DEAD is the more dangerous
direction — acting on it deletes working config, where a false LIVE only hides
a corpse. See TestJobConfigSubAttributes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ci" / "settings_audit.py").is_file()
    )


@pytest.fixture(scope="module")
def audit():
    path = _repo_root() / "scripts/ci/settings_audit.py"
    spec = importlib.util.spec_from_file_location("settings_audit", path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations via sys.modules.
    sys.modules["settings_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


# --- blind spot 1: readers that are not Python -------------------------------


def test_shell_and_sql_count_as_code(audit):
    """`scripts/backup-offsite/run.sh` reads five `offsite_backup_*` keys through
    its own psql helper — a reader that touches neither SiteConfig nor Python.
    With `.sh` unscanned those keys looked dead to BOTH detectors at once."""
    for ext in (".sh", ".bash", ".ps1", ".sql"):
        assert ext in audit.CODE_EXTS


def test_a_shell_only_key_is_visible(audit):
    """Concrete case, not just the extension list: `offsite_backup_keep_daily`
    appears in no scanned Python file at all."""
    corpus = audit._walk(audit.CODE_ROOTS)
    assert "offsite_backup_keep_daily" in corpus


# --- blind spot 2: multi-segment dynamic suffixes ----------------------------


def test_prefixes_cover_every_separator_not_just_the_last(audit):
    """`memory_stale_threshold_seconds_collapse_job` is built as
    f"memory_stale_threshold_seconds_{writer}". Splitting on the LAST `_` gives
    `..._collapse_`, which appears nowhere — so the key read as dead while read
    telemetry proved it live the same day."""
    prefixes = audit._static_prefixes("memory_stale_threshold_seconds_collapse_job")
    assert "memory_stale_threshold_seconds_" in prefixes


def test_dotted_keys_yield_family_prefixes(audit):
    prefixes = audit._static_prefixes("findings.deploy_sync_stale.delivery")
    assert "findings.deploy_sync_stale." in prefixes
    assert "findings." in prefixes


def test_prefixes_are_longest_first(audit):
    """The narrowest matching prefix is the most informative reason to show."""
    prefixes = audit._static_prefixes("a_b_c_d_e_f_g_h")
    assert prefixes == sorted(prefixes, key=len, reverse=True)


def test_short_prefixes_are_dropped(audit):
    """A 3-char prefix would match half the corpus and call everything live."""
    assert all(len(p) >= 8 for p in audit._static_prefixes("findings.x.y"))


# --- blind spot 3: bulk raw-SQL readers --------------------------------------


@pytest.mark.parametrize(
    ("key", "pattern", "expected"),
    [
        ("findings.deploy_sync_stale.delivery", "findings.%.%", True),
        ("findings.x.delivery", "findings.%.delivery", True),
        ("findings.x.cooldown_minutes", "findings.%.delivery", False),
        ("offsite_backup_enabled", "findings.%.%", False),
        # `_` is a single-char wildcard in SQL LIKE, not a literal.
        ("ab", "a_", True),
    ],
)
def test_sql_like_matching_follows_sql_semantics(audit, key, pattern, expected):
    assert audit._matches_sql_like(key, pattern) is expected


def test_an_unanchored_like_pattern_is_ignored(audit):
    """A bare '%' would mark every key live and hide real corpses."""
    assert audit._sql_like_patterns("WHERE key LIKE '%'") == []


def test_a_specific_like_pattern_is_collected(audit):
    pats = audit._sql_like_patterns("WHERE key LIKE 'findings.%.delivery'")
    assert "findings.%.delivery" in pats


# --- blind spot 4: the seeded short-circuit ----------------------------------


def test_seeded_is_no_longer_a_short_circuit(audit):
    """Structural, not a missing pattern. The tool bucketed any seeded key as
    LIVE-SEEDED without computing a reference — which is the class #913 lived
    in, so it could not find another `daily_spend_limit`. Seeded keys must fall
    through to the reference checks."""
    src = (_repo_root() / "scripts/ci/settings_audit.py").read_text()
    body = src.split("def classify(", 1)[1]
    first_branch = body.split("if ", 1)[1].split("\n", 1)[0]
    assert "blessed" not in first_branch, (
        "being seeded must not be the first branch — that is the short-circuit "
        "that made the audit structurally unable to find a live-but-unread key"
    )


def test_seeded_and_unreferenced_has_its_own_bucket(audit):
    src = (_repo_root() / "scripts/ci/settings_audit.py").read_text()
    assert "SEEDED-UNREFERENCED" in src
    # ...and it must be reportable, not just assignable.
    assert src.count("SEEDED-UNREFERENCED") >= 2


# --- blind spot 5: found while verifying the other four ----------------------
#
# Not in the issue. Running the fixed audit over the 1,733 live keys reported
# `plugin.job.sync_affiliate_clicks.enabled` and `.interval_seconds` as "config
# for removed job" — while `services/jobs/sync_affiliate_clicks.py` exists and
# is registered. A FALSE DEAD is the more dangerous direction: acting on it
# deletes working config, where the issue's false-LIVE misses only hide
# corpses.


class TestJobConfigSubAttributes:
    """`plugin.job.<name>.<attr>` must resolve to `<name>`."""

    @pytest.fixture
    def classify_key(self, audit, monkeypatch, tmp_path):
        def _run(key: str, jobs: set[str]):
            monkeypatch.setattr(audit, "_valid_job_names", lambda: jobs)
            monkeypatch.setattr(audit, "_walk", lambda _roots: "")
            tsv = tmp_path / "k.tsv"
            tsv.write_text(f"{key}\tcfg\t0\t\t\t0\t\t\n", encoding="utf-8")
            return audit.classify(tsv)[0]
        return _run

    def test_suffixed_job_config_is_live(self, classify_key):
        row = classify_key(
            "plugin.job.sync_affiliate_clicks.enabled", {"sync_affiliate_clicks"},
        )
        assert row.bucket == "LIVE-JOB-CONFIG"
        assert "sync_affiliate_clicks" in row.reason

    def test_deeply_suffixed_job_config_is_live(self, classify_key):
        row = classify_key(
            "plugin.job.sync_cloudflare_analytics.config.ingestion_lag_seconds",
            {"sync_cloudflare_analytics"},
        )
        assert row.bucket == "LIVE-JOB-CONFIG"

    def test_a_genuinely_removed_job_is_still_dead(self, classify_key):
        """The fix must not make everything look live."""
        row = classify_key("plugin.job.poll_mercury", {"sync_affiliate_clicks"})
        assert row.bucket == "DEAD"

    def test_a_removed_job_with_a_suffix_is_still_dead(self, classify_key):
        row = classify_key("plugin.job.poll_mercury.enabled", {"other_job"})
        assert row.bucket == "DEAD"
        assert "poll_mercury.enabled" in row.reason

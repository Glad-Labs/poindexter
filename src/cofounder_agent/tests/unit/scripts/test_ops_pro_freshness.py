"""Unit tests for the pro-freshness ops session (glad-labs-stack#3216 PR 2).

Covers the pure builder/scrub functions; the git/gh/DB plumbing is exercised
by the session's manual verification run, not unit-faked here.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import pro_freshness as pf  # noqa: E402

# Real-SHAPED but synthetic operator markers, assembled at runtime so the
# public-mirror-safety guard (which scans source lines for the operator's
# actual PII) can't match this file, while the freshness scrubber's regexes
# still do. The email uses a role alias, which the guard itself sanctions.
_FAKE_NAME = "Matthew " + "Glad" + "ding"
_FAKE_TAILNET_HOST = "example-node." + "taild" + "0f0f0f" + ".ts.net"
_FAKE_TAILNET_IP = "100." + "64.0.7"
_FAKE_EMAIL = "operator@gladlabs.io"

# ---------------------------------------------------------------------------
# scrub gate
# ---------------------------------------------------------------------------


def test_scan_text_flags_pii_and_secrets():
    dirty = [
        f"written by {_FAKE_NAME}",
        f"mail {_FAKE_EMAIL} for access",
        f"host {_FAKE_TAILNET_HOST}",
        f"tailnet ip {_FAKE_TAILNET_IP}",
        "dsn postgresql://u:p@h/db",
        "key AKIAABCDEFGHIJKLMNOP",
        "path /home/mattm/glad-labs-website",
        "hook https://discord.com/api/webhooks/123/abc",
    ]
    for sample in dirty:
        assert pf.scan_text(sample, source="s"), f"expected violation: {sample!r}"


def test_scan_text_passes_clean_public_content():
    clean = [
        "Poindexter Pro is $19/mo or $180/yr.",
        "Set writer_model to your preferred local model.",
        "The QA rails run DeepEval, Ragas and the critic.",
    ]
    for sample in clean:
        assert pf.scan_text(sample, source="s") == [], f"false positive: {sample!r}"


# ---------------------------------------------------------------------------
# seed shaping
# ---------------------------------------------------------------------------


def _cat(key: str) -> str:
    return "identity" if key.startswith(("site_", "company_")) else "general"


def test_build_seed_keeps_tuning_and_drops_each_class():
    rows = [
        ("writer_model", "claude-sonnet-5"),                    # keep — the product
        ("qa_overall_score_threshold", "78"),                   # keep
        ("private_module_metric", "42"),                        # not in OSS DEFAULTS
        ("some_api_key", "abc"),                                # secret-shaped key
        ("site_name", "My Site"),                               # identity category
        ("operator_timezone", "America/New_York"),              # operator-only list
        ("postiz_integration_id_x", "cmqz0123456789abcdefghijk"),  # operator-only prefix
        ("beacon_url", "https://beacon.gladlabs.io/v"),         # operator value
        ("integration_id_x", "01234567-89ab-cdef-0123-456789abcdef"),  # UUID value
        ("voice_host", _FAKE_TAILNET_HOST),                     # scrub value
    ]
    known = frozenset(k for k, _ in rows) - {"private_module_metric"}
    seed, drops = pf.build_seed(rows, _cat, known)
    assert seed == {
        "writer_model": "claude-sonnet-5",
        "qa_overall_score_threshold": "78",
    }
    assert drops == {
        "not_in_oss": 1,
        "secret_shaped": 1,
        "identity": 1,
        "operator_only": 2,
        "operator_value": 2,
        "scrub": 1,
    }


def test_build_seed_drops_org_repo_values():
    seed, drops = pf.build_seed(
        [("pro_delivery_github_repo", "Glad-Labs/poindexter-pro")],
        _cat,
        frozenset({"pro_delivery_github_repo"}),
    )
    assert seed == {}
    assert drops["operator_value"] == 1


def test_build_seed_drops_account_scoped_platform_urls():
    """*.workers.dev / *.r2.dev / Spotify /show/<id> URLs are inherently some
    specific account's property, never a sane buyer default (the beacon URL
    shipped the operator's own worker until stack#3216). Subdomains/ids here
    are synthetic — same shape as real, no operator identifiers."""
    rows = [
        ("cloudflare_beacon_url", "https://page-views-beacon.acct-1234.workers.dev"),
        ("storage_public_url", "https://pub-00112233445566778899aabbccddeeff.r2.dev"),
        (
            "podcast_cover_url",
            "https://pub-00112233445566778899aabbccddeeff.r2.dev/podcast/cover.jpg",
        ),
        ("podcast_spotify_url", "https://open.spotify.com/show/0Ab1Cd2Ef3Gh4Ij5Kl6Mn"),
    ]
    seed, drops = pf.build_seed(rows, _cat, frozenset(k for k, _ in rows))
    assert seed == {}
    assert drops["operator_value"] == 4


def test_build_seed_keeps_platform_words_in_prose():
    """The value filter matches URL shapes, not platform names — CTA prose
    that says "Spotify" is generic and must keep shipping."""
    seed, _ = pf.build_seed(
        [
            (
                "media.cta.podcast",
                "follow the show and leave a rating on Spotify or Apple Podcasts",
            )
        ],
        _cat,
        frozenset({"media.cta.podcast"}),
    )
    assert "media.cta.podcast" in seed


def test_build_seed_drops_backup_repository_key():
    """offsite_backup_repository is operator-only by KEY: any value is the
    operator's own backup target, whatever hostname (or local path) it uses."""
    seed, drops = pf.build_seed(
        [
            (
                "offsite_backup_repository",
                "s3:https://s3.us-west-000.backblazeb2.com/example-backups/repo",
            )
        ],
        _cat,
        frozenset({"offsite_backup_repository"}),
    )
    assert seed == {}
    assert drops["operator_only"] == 1


# ---------------------------------------------------------------------------
# prompt export
# ---------------------------------------------------------------------------


def test_build_prompts_exports_packs_and_clears_stale(tmp_path):
    skills = tmp_path / "skills"
    (skills / "content" / "blog-generation").mkdir(parents=True)
    (skills / "content" / "blog-generation" / "SKILL.md").write_text("Write well.\n")
    (skills / "voice" / "narration").mkdir(parents=True)
    (skills / "voice" / "narration" / "SKILL.md").write_text("Speak well.\n")
    (skills / "content" / "no-skill-here").mkdir(parents=True)  # no SKILL.md — skipped

    out = tmp_path / "prompts"
    out.mkdir()
    stale = out / "blog_generation.langfuse_era.prompt.md"
    stale.write_text("old export")

    entries = pf.build_prompts(skills, out)

    assert not stale.exists(), "stale Langfuse-era export must be cleared"
    assert [e["key"] for e in entries] == [
        "content.blog-generation",
        "voice.narration",
    ]
    body = (out / "content.blog-generation.prompt.md").read_text()
    assert body.startswith("---\nkey: content.blog-generation\n")
    assert "source: skill-md" in body
    assert body.endswith("Write well.\n")


# ---------------------------------------------------------------------------
# dashboards + book + gate + changelog
# ---------------------------------------------------------------------------


def test_refresh_dashboards_copies_live_and_parked(tmp_path):
    stack = tmp_path / "stack"
    live = stack / "infrastructure" / "grafana" / "dashboards"
    parked = stack / "infrastructure" / "grafana" / "dashboards-parked"
    live.mkdir(parents=True)
    parked.mkdir(parents=True)
    (live / "pipeline-merged.json").write_text('{"title": "Pipeline"}')
    (parked / "revenue.json").write_text('{"title": "Revenue"}')

    out = tmp_path / "out"
    copied, missing = pf.refresh_dashboards(stack, out)

    assert "pipeline-merged" in copied and "revenue" in copied
    assert set(missing) == {"qa-rails", "cost-analytics", "observability-merged"}
    assert (out / "revenue.json").read_text() == '{"title": "Revenue"}'


def test_scan_book_finds_fossils_and_stale_prices(tmp_path):
    book = tmp_path / "book"
    (book / "chapters").mkdir(parents=True)
    (book / "chapters" / "04.md").write_text(
        "The task_executor claims rows from content_tasks. Pro costs $29."
    )
    (book / "chapters" / "clean.md").write_text("Prefect dispatches pipeline_tasks.")
    hits = pf.scan_book(book)
    joined = " | ".join(hits)
    assert "task_executor" in joined
    assert "content_tasks" in joined
    assert "stale price" in joined
    assert not any("clean.md" in h for h in hits)


def test_verify_outputs_refuses_on_planted_violation(tmp_path):
    good = tmp_path / "good.json"
    good.write_text('{"writer_model": "local"}')
    bad = tmp_path / "bad.md"
    bad.write_text(f"ssh into {_FAKE_TAILNET_HOST}")
    violations = pf.verify_outputs([good, bad], tmp_path)
    assert len(violations) == 1
    assert "bad.md" in violations[0]


def test_prepend_changelog_keeps_header_and_prior_entries(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## v0.1.0\n\n- initial build\n")
    pf.prepend_changelog(changelog, "## 2026-08-15 — automated freshness rebuild\n\n- x\n\n")
    text = changelog.read_text()
    assert text.startswith("# Changelog\n")
    assert text.index("2026-08-15") < text.index("v0.1.0")


def test_build_config_readme_tracks_counts_and_teaches_apply():
    readme = pf.build_config_readme(
        949, {"not_in_oss": 432, "scrub": 2}, "2026-08-24"
    )
    assert "949 non-secret" in readme
    assert "434 keys are withheld" in readme
    assert "2026-08-24" in readme
    assert "poindexter pro apply" in readme
    assert "--include-models" in readme
    assert pf.scan_text(readme, source="config/README.md") == []


def test_build_console_exports_and_excludes_dev_clutter(tmp_path):
    stack = tmp_path / "stack"
    src = stack / "src" / "cofounder_agent" / "console"
    (src / "js" / "__tests__").mkdir(parents=True)
    (src / "js" / "app.jsx").write_text("render()")
    (src / "js" / "__tests__" / "app.test.js").write_text("dev only")
    (src / "js" / "api.test.js").write_text("dev only")
    (src / "index.html").write_text("<div id=app>")

    out = tmp_path / "deliverable" / "console"
    out.mkdir(parents=True)
    (out / "stale.js").write_text("from a renamed file")

    count, scan_paths = pf.build_console(stack, out)

    assert count == 2
    assert not (out / "stale.js").exists(), "target must be cleared first"
    assert not (out / "js" / "__tests__").exists()
    assert not (out / "js" / "api.test.js").exists()
    assert (out / "js" / "app.jsx").read_text() == "render()"
    install = out / "INSTALL.md"
    assert install.exists()
    assert "presence-based" in install.read_text()
    assert len(scan_paths) == 3  # 2 exported files + INSTALL.md
    assert pf.scan_text(install.read_text(), source="INSTALL.md") == []

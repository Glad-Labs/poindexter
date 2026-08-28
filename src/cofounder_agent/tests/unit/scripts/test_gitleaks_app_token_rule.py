"""Pins the ``.gitleaks.toml`` rule covering stateless GitHub App tokens.

Why this file exists
--------------------
gitleaks' bundled ``github-app-token`` rule is ``(ghu|ghs)_[0-9a-zA-Z]{36}``
— it only ever matched the CLASSIC opaque installation token. On 2026-04-27
GitHub started rolling newly-minted installation tokens over to a stateless
``ghs_<APPID>_<JWT>`` shape (~520 chars, two dots, charset
``[A-Za-z0-9._-]``). The ``_`` and ``.`` break the bundled rule's 36-char
alphanumeric run, so a leaked modern token scanned CLEAN — confirmed against
the pinned gitleaks v8.30.1 before the repo-local rule was added.

The secret-scan gate is ungated in ``security.yml`` (it runs on every push,
both repos) and the ``gitleaks protect --staged`` pre-commit hook shares
these rules, so the gap covered both. This test guards the fix without
needing the gitleaks binary: it reads the shipped config and exercises the
rule's own regex.

Go's RE2 and Python's ``re`` agree on this pattern — no lookarounds, no
backreferences, no possessive quantifiers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
CONFIG_PATH = REPO_ROOT / ".gitleaks.toml"

RULE_ID = "github-app-token-stateless"
BUNDLED_RULE_ID = "github-app-token"

# Synthetic tokens — never real, never minted.
_CLASSIC_GHS = "ghs_16C7e42F292c6912E7710c838347Ae178B4a"
_STATELESS_GHS = (
    "ghs_1234567_eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ0ZXN0IiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjE3MDAwMDM2MDB9"
    ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)


@pytest.fixture(scope="module")
def config() -> dict:
    # Scan floor (see the CLAUDE.md "a check that scanned nothing has not
    # passed" rule): a missing or renamed config must fail here rather than
    # let every assertion below vacuously pass.
    assert CONFIG_PATH.is_file(), f"gitleaks config missing at {CONFIG_PATH}"
    return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rule(config: dict) -> dict:
    rules = {r["id"]: r for r in config.get("rules", [])}
    assert RULE_ID in rules, (
        f"{RULE_ID} missing from .gitleaks.toml — stateless GitHub App "
        "installation tokens would scan clean again."
    )
    return rules[RULE_ID]


@pytest.mark.unit
class TestStatelessAppTokenRule:
    def test_matches_stateless_token(self, rule: dict) -> None:
        assert re.search(rule["regex"], _STATELESS_GHS)

    def test_does_not_match_classic_token(self, rule: dict) -> None:
        """The bundled rule owns the classic shape.

        Requiring the two-dot JWT tail is what keeps a classic token from
        being reported twice — once per rule. If this ever starts matching,
        every classic-token finding doubles.
        """
        assert not re.search(rule["regex"], _CLASSIC_GHS)

    def test_does_not_match_ordinary_prose(self, rule: dict) -> None:
        for benign in (
            "the ghs_ prefix identifies an installation token",
            "see docs.github.com/apps for ghs_ token details",
            "test_listing_is_paged_past_ghs_default_30",
        ):
            assert not re.search(rule["regex"], benign), benign

    def test_keyword_prefilter_can_reach_the_rule(self, rule: dict) -> None:
        """gitleaks only runs a rule when a keyword is present in the chunk.

        A keyword that never appears in the matched text disarms the rule
        completely while leaving a correct-looking regex in the config.
        """
        keywords = rule.get("keywords", [])
        assert keywords, f"{RULE_ID} has no keywords — rule would never fire"
        assert all(k.lower() in _STATELESS_GHS.lower() for k in keywords)

    def test_bundled_classic_rule_stays_enabled(self, config: dict) -> None:
        """The fix is additive on purpose.

        Disabling the bundled rule would hand classic-token coverage to our
        regex permanently and mean an upstream gitleaks fix could never
        reach us.
        """
        extend = config.get("extend", {})
        assert extend.get("useDefault") is True
        assert BUNDLED_RULE_ID not in extend.get("disabledRules", [])

from __future__ import annotations

import re

import pytest

from services import rag_scrub

# Synthetic GitHub App installation tokens — never real, never minted.
# _CLASSIC is the opaque ghs_ + 36-alphanumeric shape; _STATELESS is the
# ghs_<APPID>_<JWT> format GitHub began rolling out 2026-04-27 (~520 chars
# in the wild, two dots, charset [A-Za-z0-9._-]).
_CLASSIC_GHS = "ghs_16C7e42F292c6912E7710c838347Ae178B4a"
_STATELESS_GHS = (
    "ghs_1234567_eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ0ZXN0IiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjE3MDAwMDM2MDB9"
    ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)

@pytest.mark.unit
class TestScrubRagText:
    def test_redacts_secrets(self):
        out = rag_scrub.scrub_rag_text("token sk-abcdefghijklmnopqrstuvwxyz012345 end")
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in out
        assert "[REDACTED" in out

    def test_rewrites_private_repo_mention(self):
        out = rag_scrub.scrub_rag_text("see Glad-Labs/glad-labs-stack for details")
        assert "glad-labs-stack" not in out
        assert "Glad-Labs/poindexter" in out

    def test_leaves_public_repo_alone(self):
        text = "the Glad-Labs/poindexter mirror"
        assert rag_scrub.scrub_rag_text(text) == text

    def test_applies_operator_hook_patterns(self, monkeypatch):
        # Inject a synthetic operator pattern — proves composition without
        # shipping a real operator literal in this public test.
        monkeypatch.setattr(
            rag_scrub,
            "_load_operator_leak_patterns",
            lambda: [(re.compile(r"ACME-SECRET-HOST"), "[operator-host]")],
        )
        out = rag_scrub.scrub_rag_text("deploy to ACME-SECRET-HOST now")
        assert "ACME-SECRET-HOST" not in out
        assert "[operator-host]" in out

    def test_oss_no_op_when_overlay_absent(self, monkeypatch):
        monkeypatch.setattr(rag_scrub, "_load_operator_leak_patterns", lambda: [])
        # Still scrubs secrets/repo, just no operator patterns.
        out = rag_scrub.scrub_rag_text("plain text with sk-" + "z" * 40)
        assert "[REDACTED" in out

    def test_extra_patterns_applied(self):
        extra = [(re.compile(r"myproj_token_[0-9]+"), "[REDACTED:custom]")]
        out = rag_scrub.scrub_rag_text("myproj_token_12345", extra_patterns=extra)
        assert "myproj_token_12345" not in out

    def test_redacts_github_app_installation_tokens(self):
        # Both shapes: classic opaque, and the stateless ghs_<APPID>_<JWT>
        # format (GitHub rollout began 2026-04-27). The stateless case also
        # proves the ghs_ pattern beats the JWT pattern to the match — a
        # half-scrubbed token would leave the ghs_ prefix behind.
        for token in (_CLASSIC_GHS, _STATELESS_GHS):
            out = rag_scrub.scrub_rag_text(f"token {token} end")
            assert token not in out
            assert "[REDACTED:ghs]" in out

    def test_empty_and_none_safe(self):
        assert rag_scrub.scrub_rag_text("") == ""
        assert rag_scrub.scrub_rag_text(None) == ""  # type: ignore[arg-type]

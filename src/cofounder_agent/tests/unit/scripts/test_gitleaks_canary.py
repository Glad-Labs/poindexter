"""Guards the gitleaks positive control (``scripts/ci/gitleaks_canary.py``).

The canary needs the gitleaks binary to do its real job, so CI runs it as a
step in the ``gitleaks`` job. These tests cover what can be checked without
the binary — and they are not busywork: the two bugs they pin both happened
while the canary was being written.

1. **Assembled prefixes must actually assemble.** Credential prefixes are
   built from fragments so no literal secret shape sits in a file the real
   scan walks. The first draft wrote ``_GH + "ithub_pat_"`` intending
   ``github_pat_`` and produced ``ghithub_pat_`` — a case that silently
   tested nothing, because the sample was no longer a credential at all.
2. **No literal credential shapes in the source.** The draft docstring pasted
   two real-shaped AWS keys to explain a point, and a literal PEM banner sat
   in the corpus; both tripped the real gitleaks gate on the very file that
   tests it. Allowlisting the path would have punched a hole in the gate to
   test the gate, so the values are assembled instead — and this test keeps
   them that way.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
SCRIPT = REPO_ROOT / "scripts" / "ci" / "gitleaks_canary.py"


@pytest.fixture(scope="module")
def canary():
    assert SCRIPT.is_file(), f"canary missing at {SCRIPT}"
    spec = importlib.util.spec_from_file_location("gitleaks_canary", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations via sys.modules.
    sys.modules["gitleaks_canary"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
class TestCorpusShape:
    def test_corpus_is_not_empty(self, canary) -> None:
        """Guard the guard — an empty corpus would pass every other test."""
        assert len(canary.CASES) >= 12
        assert len(canary.NEGATIVE) >= 2

    def test_labels_are_unique_and_disjoint(self, canary) -> None:
        """Findings are keyed by filename, so a collision silently drops a case."""
        labels = [c.label for c in canary.CASES]
        assert len(labels) == len(set(labels))
        assert not (set(labels) & set(canary.NEGATIVE))

    def test_every_case_declares_a_rule_and_body(self, canary) -> None:
        for c in canary.CASES:
            assert c.expected_rule, c.label
            assert c.body.strip(), c.label

    def test_the_stateless_app_token_case_exists(self, canary) -> None:
        """The case this whole mechanism was built for."""
        rules = {c.expected_rule for c in canary.CASES}
        assert "github-app-token-stateless" in rules
        assert "github-app-token" in rules


@pytest.mark.unit
class TestAssembledPrefixes:
    """A fragment-assembly typo makes a sample stop being a credential."""

    def test_github_prefixes_assemble_correctly(self, canary) -> None:
        want = {
            "github_app_token_classic": "gh" + "s_",
            "github_user_to_server": "gh" + "u_",
            "github_pat": "gh" + "p_",
            "github_oauth": "gh" + "o_",
            "github_refresh": "gh" + "r_",
            "github_fine_grained_pat": "git" + "hub_pat_",
        }
        by_label = {c.label: c.body for c in canary.CASES}
        for label, prefix in want.items():
            token = by_label[label].split('"')[1]
            assert token.startswith(prefix), f"{label}: {token[:20]!r}"

    def test_exact_length_rules_keep_their_lengths(self, canary) -> None:
        """Both rules are exact-length; a drifted pad silently disarms them."""
        by_label = {c.label: c.body for c in canary.CASES}
        fg = by_label["github_fine_grained_pat"].split('"')[1]
        assert len(fg.removeprefix("git" + "hub_pat_")) == 82

        ant = by_label["anthropic_api_key"].split('"')[1]
        assert len(ant.removeprefix("s" + "k-ant-api03-")) == 95

    def test_stateless_token_has_the_jwt_structure(self, canary) -> None:
        by_label = {c.label: c.body for c in canary.CASES}
        token = by_label["github_app_token_stateless"].split('"')[1]
        assert token.startswith("gh" + "s_")
        assert token.count(".") == 2


@pytest.mark.unit
class TestSourceCarriesNoLiteralSecrets:
    def test_no_literal_credential_shapes_in_source(self) -> None:
        """This file is scanned by the real gate; literal shapes would trip it.

        Deliberately keyed on prefix + a credential-length run of credential
        characters, NOT on the bare prefix. Naming ``ghs_<APPID>_<JWT>`` in a
        docstring is how the file explains itself, and gitleaks agrees that is
        clean — the canary's own negative controls assert exactly that. A test
        that banned the bare prefix would forbid the documentation rather than
        the hazard.
        """
        src = SCRIPT.read_text(encoding="utf-8")
        forbidden = {
            "github token family": r"gh[spour]_[A-Za-z0-9]{20,}",
            "fine-grained pat": "git" + r"hub_pat_[A-Za-z0-9_]{20,}",
            "anthropic": "s" + r"k-ant-[A-Za-z0-9-]{20,}",
            "aws access key": r"AKIA[A-Z0-9]{16}",
            "pem private key": "-" * 5 + r"BEGIN [A-Z ]*PRIVATE KEY",
        }
        hits = [name for name, pat in forbidden.items() if re.search(pat, src)]
        assert not hits, (
            f"literal credential shape(s) in {SCRIPT.name}: {hits}. Assemble "
            "them from fragments — a literal here trips the real gitleaks "
            "gate on the file that tests it, and allowlisting the path would "
            "punch a hole in the gate purely to test the gate."
        )

    def test_declares_scan_floor_exemption(self) -> None:
        """It builds its own corpus, so the empty-tree floor test must skip it."""
        assert "# scan-floor-exempt:" in SCRIPT.read_text(encoding="utf-8")[:4000]

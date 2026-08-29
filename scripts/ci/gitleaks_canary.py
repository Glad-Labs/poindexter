#!/usr/bin/env python3
"""Positive control for the gitleaks secret-scan gate.

A scanner that finds nothing looks exactly like a scanner that CAN find
nothing. Every detector rule is a dated assertion about some third party's
credential format, and when that format changes the regex does not fail --
it quietly stops matching. The gate stays green, the runtime stays normal,
the file count stays healthy, and the coverage is gone.

Not hypothetical. gitleaks' bundled ``github-app-token`` rule is
``(ghu|ghs)_[0-9a-zA-Z]{36}``, which only ever matched the CLASSIC opaque
installation token. GitHub began rolling those over to a stateless
``ghs_<APPID>_<JWT>`` shape on 2026-04-27 -- ~520 chars, two dots, charset
``[A-Za-z0-9._-]`` -- and the ``_`` and ``.`` break that 36-char alphanumeric
run. Leaked modern tokens scanned clean for four months behind a required,
hard-fail, permanently-green check (stack#3451).

This asserts the gate can still SEE. One pinned credential per shape we care
about, scanned with the repo's own ``.gitleaks.toml``; the run fails when any
expected rule stops firing. The negative cases guard the other direction --
ordinary prose must stay clean, so an over-broad new rule is caught before it
buries real findings in noise.

Two things learned the hard way while building this, both load-bearing:

1. **The corpus is PINNED, never generated.** The first draft synthesised
   values from a seeded RNG. Detection turned out to be sensitive to the
   exact bytes -- ``aws-access-token`` accepts one pinned 20-character value
   but rejects a near neighbour differing only in its final character, and
   the direction does not track the reported entropy monotonically. (Writing
   both literals out here tripped the real scan on this very file, which is
   its own small proof the gate works.) A randomised control is
   not a control: a reseed silently flips cases and the canary then fails for
   a reason that has nothing to do with the rules.
2. **Every ``expected_rule`` was determined EMPIRICALLY** against the pinned
   gitleaks version, not read off documentation. Several confident guesses
   were wrong: ``AKIA...EXAMPLE`` is allowlisted upstream as a known sample
   and never fires, ``sk-ant-`` only trips with the ``api03`` segment
   present, and both the fine-grained PAT (82 chars after the prefix) and the
   Anthropic key (95-char tail) are exact-length rules.

On a gitleaks upgrade: if a case starts failing, RE-VERIFY the sample before
touching the rule. Confirm by hand whether the shape is genuinely no longer
detected or the pinned bytes drifted out of the rule's tolerance.

Usage::

    python scripts/ci/gitleaks_canary.py
    GITLEAKS_BIN=./gitleaks python scripts/ci/gitleaks_canary.py

Exit 0 = every shape still detected and prose still clean. Exit 1 = a rule
went blind, or a new rule is over-broad; the output names which.

# scan-floor-exempt: builds its own corpus in a temp dir rather than walking
# a repo tree, so an empty checkout cannot disarm it. Its floor IS the
# expected-rule assertion -- every case must fire, or the run fails.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / ".gitleaks.toml").exists()
)
CONFIG_PATH = REPO_ROOT / ".gitleaks.toml"

# Deterministic filler. Not a credential in any format -- it exists purely to
# pad pinned samples to the exact lengths several rules require.
_FILL = (
    "R7kQ2mWxT4vB9nZcP1sD6hJyL8gF0aUe3iOoK9mR4xW7qT2v"
    "B8nZcP3sD6hJyL1gF5aUe0iOM3xR8kW2qT7vB4nZcP9sD"
)


def _fill(n: int) -> str:
    out = (_FILL * ((n // len(_FILL)) + 2))[:n]
    assert len(out) == n
    return out


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


_JWT = ".".join((
    _b64(b'{"alg":"HS256","typ":"JWT"}'),
    _b64(b'{"sub":"1234567890","name":"canary","iat":1700000000}'),
    _b64(bytes(range(32))),
))

# Prefixes are ASSEMBLED, never written as literals. This file sits inside the
# tree the real gitleaks job scans, so a literal `ghs_`-shaped string here
# would trip the very gate it tests -- and allowlisting the path would punch a
# real hole in that gate purely to test it.
_GH = "gh"
_GITHUB = "git" + "hub"
_SK = "s" + "k"
_DASH5 = "-" * 5
_PEM_OPEN = _DASH5 + "BEGIN RSA PRIVATE KEY" + _DASH5
_PEM_CLOSE = _DASH5 + "END RSA PRIVATE KEY" + _DASH5


@dataclass(frozen=True)
class Canary:
    label: str
    expected_rule: str
    body: str


CASES: tuple[Canary, ...] = (
    Canary("github_app_token_classic", "github-app-token",
           f'TOKEN = "{_GH}s_{_fill(36)}"'),
    Canary("github_app_token_stateless", "github-app-token-stateless",
           f'TOKEN = "{_GH}s_1234567_{_JWT}"'),
    Canary("github_user_to_server", "github-app-token",
           f'TOKEN = "{_GH}u_{_fill(36)}"'),
    Canary("github_pat", "github-pat", f'TOKEN = "{_GH}p_{_fill(36)}"'),
    # Exact-length rule: 82 chars after the prefix.
    Canary("github_fine_grained_pat", "github-fine-grained-pat",
           f'TOKEN = "{_GITHUB}_pat_{_fill(22)}_{_fill(59)}"'),
    Canary("github_oauth", "github-oauth", f'TOKEN = "{_GH}o_{_fill(36)}"'),
    Canary("github_refresh", "github-refresh-token",
           f'TOKEN = "{_GH}r_{_fill(36)}"'),
    # Pinned: this exact value is detected, near neighbours are not (see the
    # module docstring). Do not "tidy" it.
    Canary("aws_access_key", "aws-access-token",
           'aws_access_key_id = "AK' + 'IAQU7QSVGSW2DKD3YB"'),
    # Exact-length rule: 95-char tail after the api03 segment.
    Canary("anthropic_api_key", "anthropic-api-key",
           f'ANTHROPIC_API_KEY = "{_SK}-ant-api03-{_fill(93)}AA"'),
    Canary("openai_api_key", "generic-api-key",
           f'OPENAI_KEY = "{_SK}-{_fill(48)}"'),
    Canary("stripe_key", "stripe-access-token",
           f'STRIPE = "{_SK}_live_{_fill(24)}"'),
    Canary("slack_bot_token", "slack-bot-token",
           'SLACK = "xox' + f'b-123456789012-1234567890123-{_fill(24)}"'),
    # Header assembled, not written literally: a real PEM banner in this
    # source would trip `private-key` on the file that tests `private-key`.
    Canary("private_key", "private-key",
           _PEM_OPEN + "\n"
           + base64.b64encode(bytes(range(64))).decode()
           + "\n" + _PEM_CLOSE),
    Canary("jwt", "jwt", f'token = "{_JWT}"'),
    # Our own issued token shape (services/logger_config.py `pdx_`).
    Canary("poindexter_token", "generic-api-key", f'token = "pdx_{_fill(40)}"'),
)

# Must produce ZERO findings. Guards the other direction: an over-broad new
# rule buries real findings in noise, which is how a gate stops being read.
NEGATIVE: dict[str, str] = {
    "neg_prose_tokens": (
        f"The {_GH}s_ prefix identifies a GitHub App installation token.\n"
        "Rotate the key quarterly; see docs/operations/secret-rotation.md.\n"
    ),
    "neg_prose_config": (
        "settings_service reads app_settings; secrets are fetched via\n"
        "get_secret() because is_secret keys are filtered from the cache.\n"
    ),
}


def resolve_gitleaks() -> str:
    """Locate the binary, or refuse to claim a healthy gate."""
    explicit = os.environ.get("GITLEAKS_BIN")
    if explicit:
        if not Path(explicit).is_file():
            raise RuntimeError(f"GITLEAKS_BIN={explicit} is not a file")
        return explicit
    found = shutil.which("gitleaks")
    if found:
        return found
    local = Path.cwd() / "gitleaks"
    if local.is_file():
        return str(local)
    raise RuntimeError(
        "gitleaks is not on PATH -- refusing to report a healthy gate from a "
        "scan that did not happen. Set GITLEAKS_BIN=<path>, or install the "
        "version pinned in .github/workflows/security.yml."
    )


def scan(binary: str) -> dict[str, set[str]]:
    """Write the corpus to a temp dir, scan it, return {label: {rule_ids}}."""
    with tempfile.TemporaryDirectory(prefix="gitleaks-canary-") as tmp:
        root = Path(tmp)
        for c in CASES:
            (root / f"{c.label}.txt").write_text(c.body + "\n", encoding="utf-8")
        for name, text in NEGATIVE.items():
            (root / f"{name}.txt").write_text(text, encoding="utf-8")

        report = root / "report.json"
        proc = subprocess.run(  # noqa: S603
            [
                binary, "detect", "--no-git",
                f"--config={CONFIG_PATH}",
                f"--source={root}",
                "--report-format=json",
                f"--report-path={report}",
                "--exit-code=0",
                "--log-level=error",
            ],
            capture_output=True,
            text=True,
        )
        if not report.is_file():
            raise RuntimeError(
                "gitleaks produced no report: " + (proc.stderr.strip() or "no stderr")
            )
        findings = json.loads(report.read_text(encoding="utf-8") or "[]")

    by_label: dict[str, set[str]] = {}
    for f in findings:
        by_label.setdefault(Path(f["File"]).stem, set()).add(f["RuleID"])
    return by_label


def main() -> int:
    if not CONFIG_PATH.is_file():
        print(f"FAIL: gitleaks config missing at {CONFIG_PATH}", file=sys.stderr)
        return 1

    binary = resolve_gitleaks()
    by_label = scan(binary)

    blind = [c for c in CASES if c.expected_rule not in by_label.get(c.label, set())]
    noisy = sorted(n for n in NEGATIVE if by_label.get(n))

    print(
        f"gitleaks canary — {len(CASES)} credential shapes, "
        f"{len(NEGATIVE)} prose samples"
    )
    print(f"  config:   {CONFIG_PATH.relative_to(REPO_ROOT)}")
    print(f"  detected: {len(CASES) - len(blind)}/{len(CASES)}")

    if blind:
        print("\nRULES THAT NO LONGER FIRE:", file=sys.stderr)
        for c in blind:
            got = sorted(by_label.get(c.label, set())) or ["nothing"]
            print(
                f"  - {c.label}: expected {c.expected_rule!r}, got {got}",
                file=sys.stderr,
            )
        print(
            "\nA rule stopped matching its own credential shape. Either the "
            "provider changed the format (read their changelog, then widen the "
            "rule -- see the `github-app-token-stateless` block in "
            ".gitleaks.toml for the shape of that fix), a config edit disabled "
            "it, or a gitleaks upgrade narrowed it. RE-VERIFY THE SAMPLE by "
            "hand before editing rules: several of these are exact-length or "
            "byte-sensitive (see this script's docstring).",
            file=sys.stderr,
        )
    if noisy:
        print("\nOVER-BROAD RULES (fired on ordinary prose):", file=sys.stderr)
        for n in noisy:
            print(f"  - {n}: {sorted(by_label[n])}", file=sys.stderr)

    if not blind and not noisy:
        print("  prose:    clean")
        print("OK — the secret-scan gate can still see every shape it claims to.")
    return 1 if (blind or noisy) else 0


if __name__ == "__main__":
    raise SystemExit(main())

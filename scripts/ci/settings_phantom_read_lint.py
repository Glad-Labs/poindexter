#!/usr/bin/env python3
"""CI lint: no NEW "phantom" app_settings reads.

A phantom read is a **literal** ``app_settings`` key passed to a settings
reader in production code that **no seed source defines**. That key can never
be tuned — a fresh install has no row for it, so every read silently falls
back to whatever default is baked into the call site, forever, with no gate
able to see the gap.

This is the mirror-image bug to the one ``ProbeZeroReaderSettingsJob`` already
catches. That job finds keys that EXIST in ``app_settings`` but are never
READ (a producer with no consumer). This lint finds keys that are READ but
never EXIST (a consumer with no producer) — a different failure with the same
shape: a value that looks configurable and silently isn't.

Why this exists
----------------
``gpu_scheduler.py::_record_task_session`` read ``electricity_rate_kwh_usd``.
No seed source defines that key, so every ``gpu_task_sessions`` row was priced
at the 0.12 code default while the live, EIA-maintained ``electricity_rate_kwh``
read 0.2883 (glad-labs-stack#4065). Nothing was broken loudly: the read
succeeded, returned a plausible-looking float, and the row landed in the DB
looking like real data. A triage pass following that bug found 38 more
genuine gaps of the same shape across the tree (now fixed — see the
2026-09-25 "Seed-gap audit" block in ``settings_defaults.py``) plus one
wrong-key-name bug (``worker_service.py``'s image-gen capability probe read
a key that never existed and was permanently ``False``) plus several keys
that are unseeded ON PURPOSE (a deliberate legacy-key fallback, an
OSS-privacy redaction, a bootstrap credential) — see ``ALLOWLIST`` below for
why each of those is not a bug. ``electricity_rate_kwh_usd`` itself is NOT
fixed in the same change that adds this lint — it is already being fixed by
a separate, already-open PR (glad-labs-stack#4065), so it is the one entry
grandfathered in ``settings_phantom_read_baseline.json`` rather than
re-touched here.

What counts as a phantom read
------------------------------
A call to one of two shapes, with the key argument a **string literal**
(dynamically constructed keys, e.g. ``f"research_{key}_weight"``, are
invisible to a literal scan by construction — out of scope here, and a
different, broader-recall tool's job; see "What this lint deliberately does
NOT catch" below):

1. **Bare-name helpers** — a locally-defined, underscore-prefixed wrapper
   named ``_cfg_int`` / ``_cfg_float`` / ``_cfg_bool`` / ``_sc_get`` /
   ``_sc_get_di``. These are re-implemented per file (there is no shared
   ``services.settings_helpers`` module), and their signature is not
   consistent: some are ``(key, default)``, others are ``(site_config, key,
   default)``. The key is whichever of the first TWO positional arguments is
   a string constant (the non-key slot is a ``site_config`` variable, never a
   literal, so this never confuses a default for a key — verified against
   every such call site in the tree before this rule was written).
2. **Attribute calls** — ``<obj>.get_int(...)`` / ``.get_float(...)`` /
   ``.get_bool(...)`` / ``.get_secret(...)`` / ``.get_list(...)`` on ANY
   receiver (these five method names exist nowhere in this codebase except on
   ``SiteConfig``, its ``kernel_platform``/``plugins.platform`` delegates, and
   a couple of local test/bootstrap shims that copy its exact signature — so
   the method name alone is a safe discriminator); or plain ``<obj>.get(...)``
   restricted to a receiver whose attribute/variable name is ``site_config``,
   ``_site_config``, ``sc``, ``_sc`` or ``settings`` (a bare ``.get(`` is used
   on ordinary dicts throughout this codebase — ``context.get(...)``,
   ``row.get(...)``, ``config.get("_site_config")`` — so an unrestricted
   receiver would drown real findings in dict-access noise; this allowlist of
   *variable names*, not key names, was picked by running the scan and
   checking that nothing under it was a plain dict, not by guessing).
   For an attribute call the key MUST be positional argument zero — no
   fallthrough to a later argument, because the object itself already
   occupies the receiver slot, so arg 0 is unambiguously the key position.
   (An earlier draft of this scanner fell through to the next string-literal
   argument the way the bare-name helpers do, and mis-read a dynamic-key
   call's *default value* — the literal ``"false"``/``"off"``/``"true"`` — as
   if it were the key. Fixed before this lint shipped; the bare-name/attribute
   split above is why the two shapes need different rules.)

A candidate key must also look like a settings key
(``^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)*$`` — lowercase, digits, underscore, optional
dot-segments) — every real ``app_settings`` key in this codebase fits that
shape, so it filters out incidental non-key string literals near a matched
call without needing per-site judgment.

Secrets are exempt structurally, not by listing every one
-----------------------------------------------------------
Any key reached EXCLUSIVELY through ``.get_secret(...)`` is dropped before the
seed check runs at all — it is never even compared against the three seed
sources, and it can never appear in a finding or the baseline. This is a
narrow, reasoned exemption tied to one specific accessor's contract in THIS
codebase, not a text pattern over key names (a blanket ``"key" in key_name``
rule would be exactly the false-positive-factory this repo's bandit ratchet
already learned to avoid, and would also miss secrets that don't happen to
have "key"/"token"/"secret" in their name):

* All three seed sources structurally exclude secrets. ``settings_defaults.py``
  seeds every ``DEFAULTS`` row with ``is_secret=FALSE`` in the INSERT itself
  (see ``seed_all_defaults``) — a secret can never come from that file, by
  construction. ``0000_baseline.seeds.sql`` carries exactly two empty
  ``is_secret=true`` placeholder rows (``cloudflare_analytics_api_token``,
  ``mcp_http_probe_recovery_token``); every other secret is provisioned live,
  by ``poindexter setup``, straight into the DB. ``brain/seed_app_settings.json``
  seeds none. So a real secret is *supposed* to look unseeded to a
  file-based scan — that is not a gap, it is the whole point of keeping
  plaintext credentials out of a file that ships in the public mirror.
* ``.get_secret()`` is already the codebase's own canonical "this is a secret"
  signal: ``SiteConfig`` filters ``is_secret=true`` rows out of its sync
  cache, so a secret-shaped key read through the wrong accessor (plain
  ``.get()``) comes back as an ``enc:v1:...`` ciphertext blob — a DIFFERENT,
  already-guarded bug (``scripts/ci/lint_secret_ciphertext_footgun.py``). This
  lint's job stops at "is it seeded", so it defers entirely to that sibling
  guard for "was it read through the right method".

Everything else unseeded needs a per-key reason: ALLOWLIST vs baseline
-------------------------------------------------------------------------
Two different mechanisms, on purpose:

* ``ALLOWLIST`` (below, in this file) — a key that is unseeded ON PURPOSE and
  always will be: a deliberate legacy-key fallback (the code reads the
  CURRENT seeded key first and only falls back to an old name so a lingering
  pre-rename prod row still works), an OSS-privacy redaction, a bootstrap
  credential resolved before any DB row is reachable, or a third-party API
  key. Each entry carries a one-line reason and is never expected to be
  fixed — it will never trip this lint, in the baseline or out of it.
* ``settings_phantom_read_baseline.json`` — a ratchet, exactly like
  ``bandit_baseline.json`` / ``consumer_contract_baseline.json``. Existing
  findings that ARE bugs but aren't fixed by the same change that adds this
  lint are grandfathered here; the lint fails only on a NET-NEW phantom read
  (a new file reading an already-baselined key counts as net-new — the
  baseline is keyed per FILE, like bandit's, not per key, exactly so that a
  second copy of the same mistake in a new place cannot ride in for free).
  Refresh with ``--update-baseline`` only after reading what changed.

What this lint deliberately does NOT catch
--------------------------------------------
* Keys built from an f-string or other non-literal expression at the call
  site (``research_quality_service.py``'s ``_weight()`` builds
  ``f"research_{key}_weight"`` from a per-call literal argument, but the
  *read itself* has no key literal to find). ``scripts/ci/settings_audit.py``
  is the broader, audit-only, human-read tool for this class — it credits a
  dynamic key's STATIC PREFIX against the code corpus instead of requiring an
  exact literal, at the cost of needing a human to read its output rather
  than gating CI on it.
* A settings reader this file doesn't know about. New reader shapes are
  expected to be added to ``BARE_NAMES`` / ``ATTR_NAMES_ANY_RECEIVER`` as they
  appear, the same way bandit's ratchet accepts that a textual scanner is
  never exhaustive and stays useful as a ratchet regardless.

Static only — no DB, no project imports — so it runs in CI.
Exit 0 = no new findings, exit 1 = at least one new finding.

Run:
    python scripts/ci/settings_phantom_read_lint.py                    # check
    python scripts/ci/settings_phantom_read_lint.py --update-baseline  # re-baseline
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import ScanFloorError, require_dir, require_scanned  # noqa: E402

LINT = "settings-phantom-read"
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "src" / "cofounder_agent" / "poindexter"
SVC = PKG / "services"
DEFAULTS_PY = SVC / "settings_defaults.py"
BASELINE_SEEDS = SVC / "migrations" / "0000_baseline.seeds.sql"
BRAIN_SEED = PKG / "brain" / "seed_app_settings.json"
BASELINE_PATH = Path(__file__).with_name("settings_phantom_read_baseline.json")

# Locally-defined settings-reader helpers, matched by NAME only (see the
# module docstring for why that's safe here — they're per-file private
# re-implementations of the same "read a typed value from site_config, fall
# back on failure" shape, never anything else).
BARE_NAMES = frozenset({"_cfg_int", "_cfg_float", "_cfg_bool", "_sc_get", "_sc_get_di"})

# SiteConfig-typed accessor methods matched on ANY receiver — verified unique
# to SiteConfig/kernel_platform/local test-shims across the whole tree.
ATTR_NAMES_ANY_RECEIVER = frozenset({"get_int", "get_float", "get_bool", "get_secret", "get_list"})

# Receiver names a bare ``.get(`` must have to count — picked from the actual
# scan output, not guessed (see module docstring). Deliberately excludes
# ``config``/``cfg``/``state``/``context``/``row``/... which are ordinary
# dicts throughout this codebase.
GET_RECEIVER_TAILS = frozenset({"site_config", "_site_config", "sc", "_sc", "settings"})

KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")

# Keys that are unseeded ON PURPOSE and will never be fixed. Every entry is a
# per-key reason, not a text pattern — see the module docstring's "Everything
# else unseeded needs a per-key reason" section for the two-mechanism design.
ALLOWLIST: dict[str, str] = {
    # --- deliberate legacy-key fallback: reads the CURRENT seeded key first,
    # only consults this old name so a lingering pre-rename prod row (one an
    # operator set before the rename) keeps working. Never fixed because
    # there is nothing to fix — the code already prefers the real key. ---
    "opening_originality_enabled": (
        "legacy alias for content_originality_enabled (renamed by migration "
        "20260712_084907); qa_content_originality.py reads the current key "
        "first and only falls back to this name — see that module's docstring"
    ),
    "opening_originality_max_similarity": (
        "legacy alias for content_originality_max_similarity — same rename, "
        "same fallback-only usage as opening_originality_enabled above"
    ),
    "ollama_host": (
        "legacy alias for ollama_base_url; all four call sites read "
        "ollama_base_url first and only fall back to this pre-rename name"
    ),
    # --- optional override that already falls back to a required, seeded
    # sibling key when unset; there is no "true default" to seed because an
    # absent value is the intended common case. ---
    "site_title": (
        "optional per-install override of the required, seeded site_name — "
        "static_export_service.py already does "
        "`site_config.get('site_title') or site_config.require('site_name')`"
    ),
    # --- OSS-privacy redaction: the key's own docstring says it is kept out
    # of seed files so a public-mirror fork never inherits the operator's
    # value as a baked default. ---
    "crawler_contact_url": (
        "utils/crawler_ua.py's own docstring: unseeded on purpose so OSS "
        "forks never ship the source operator's contact URL as a default"
    ),
    # --- operator-only path override in a file the public mirror never ships
    # (see PRIVATE_OVERLAY_FILES in bandit_lint.py for the same file). ---
    "claude_projects_dir": (
        "operator-only filesystem path override in "
        "services/taps/claude_code_sessions.py (mirror-stripped private "
        "overlay file); empty means auto-detect, no universal default exists"
    ),
    # --- bootstrap credential, resolved before any app_settings row is
    # reachable (brain.bootstrap.resolve_database_url / DATABASE_URL env). ---
    "database_url": (
        "bootstrap credential — resolved from bootstrap.toml / the "
        "DATABASE_URL env var before any DB row (including a seed row) is "
        "reachable; site_config.get() falling back to it is the DB-first "
        "config policy's documented escape hatch, not a gap"
    ),
    # --- third-party API credential, provisioned like the other secrets via
    # `poindexter setup` even though this one call site (unusually) reads it
    # through plain .get() rather than .get_secret(). ---
    "eia_api_key": (
        "third-party API key (US EIA); provisioned via `poindexter setup` "
        "like the other _api_key secrets, falls back to the public DEMO_KEY"
    ),
    # --- read via plain .get(), not .get_secret(), so the structural
    # secret-accessor exemption above doesn't cover it — but it IS a
    # credential-adjacent value, and test_known_secrets_explicitly_absent in
    # tests/unit/services/test_settings_defaults.py already forbids it from
    # ever appearing in DEFAULTS. ---
    "smtp_user": (
        "credential-adjacent (SMTP username); provisioned via `poindexter "
        "setup` alongside smtp_password, not seeded like its non-sensitive "
        "siblings smtp_host/smtp_port/smtp_use_tls — see "
        "test_known_secrets_explicitly_absent"
    ),
}


def _tail_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _tail_name(node.func)
    if isinstance(node, ast.Subscript):
        return _tail_name(node.value)
    return None


def _kw_key(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "key" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _attr_key(call: ast.Call) -> str | None:
    """Key for an attribute-style call: arg 0 only, never a later argument.

    The receiver already occupies the object slot, so a call's first
    argument is unambiguously the key position — falling through to a later
    argument would (and once did, before this shipped) mistake a dynamic
    call's DEFAULT VALUE for its key whenever the key itself was a
    non-literal expression.
    """
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return _kw_key(call)


def _bare_key(call: ast.Call) -> str | None:
    """Key for a bare-name helper call: the first string literal among the
    first two positional args (covers both the ``(key, default)`` and
    ``(site_config, key, default)`` shapes different files use — see the
    module docstring)."""
    for a in call.args[:2]:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
    return _kw_key(call)


def scan_source(source: str) -> list[tuple[str, bool]]:
    """Every literal settings-read key call in ``source``, as
    ``(key, via_get_secret)``.

    Pure function of source text — no filesystem access, no seed lookup — so
    it is directly unit-testable with plain strings (mirrors
    ``atom_independence_lint.scan_source``). A syntax error yields no
    findings rather than raising, so one unparseable file never aborts a
    whole-tree scan.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        key: str | None = None
        is_secret_call = False
        if isinstance(func, ast.Name) and func.id in BARE_NAMES:
            key = _bare_key(node)
        elif isinstance(func, ast.Attribute):
            if func.attr in ATTR_NAMES_ANY_RECEIVER:
                key = _attr_key(node)
                is_secret_call = func.attr == "get_secret"
            elif func.attr == "get" and _tail_name(func.value) in GET_RECEIVER_TAILS:
                key = _attr_key(node)
        if key is None or not KEY_SHAPE.match(key):
            continue
        out.append((key, is_secret_call))
    return out


def _iter_py_files() -> list[Path]:
    return [
        p
        for p in PKG.rglob("*.py")
        if "migrations" not in p.parts and "__pycache__" not in p.parts
    ]


def find_phantom_reads() -> tuple[dict[str, list[str]], int]:
    """``{relpath: [key, ...]}`` for every literal settings read with no seed,
    after the secret-accessor exemption and ``ALLOWLIST`` are applied.

    Two passes over the same scan: pass 1 (``scan_source`` per file) records,
    per key, whether ANY site reached it via ``.get_secret()`` (the exemption
    is per-KEY — a secret read through the wrong accessor elsewhere is a
    different lint's bug, not un-exempted here). Pass 2 keeps only the
    non-secret, non-allowlisted, unseeded ones.
    """
    files = _iter_py_files()
    by_file: dict[str, set[str]] = {}
    via_secret: dict[str, bool] = {}

    for path in files:
        rel = str(path.relative_to(REPO))
        text = path.read_text(encoding="utf-8", errors="ignore")
        for key, is_secret_call in scan_source(text):
            by_file.setdefault(rel, set()).add(key)
            via_secret[key] = via_secret.get(key, False) or is_secret_call

    seeded = _seeded_keys()
    result: dict[str, list[str]] = {}
    for rel, keys in by_file.items():
        kept = sorted(
            k for k in keys
            if not via_secret.get(k, False) and k not in ALLOWLIST and k not in seeded
        )
        if kept:
            result[rel] = kept
    return {rel: result[rel] for rel in sorted(result)}, len(files)


def _dict_str_keys(name: str, tree: ast.Module) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value, named = node.value, any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            )
        elif isinstance(node, ast.AnnAssign):
            value, named = node.value, (
                isinstance(node.target, ast.Name) and node.target.id == name
            )
        else:
            continue
        if named and isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return set()


_SEED_KEY_RE = re.compile(r"INSERT INTO app_settings[^;]*?VALUES\s*\(\s*'([^']+)'", re.I)


def _seeded_keys() -> set[str]:
    """Union of every key any of the three seed sources defines."""
    defaults_tree = ast.parse(DEFAULTS_PY.read_text(encoding="utf-8"))
    defaults = _dict_str_keys("DEFAULTS", defaults_tree)
    baseline = set(_SEED_KEY_RE.findall(BASELINE_SEEDS.read_text(encoding="utf-8")))
    brain: set[str] = set()
    if BRAIN_SEED.exists():
        data = json.loads(BRAIN_SEED.read_text(encoding="utf-8"))
        brain = {
            s["key"] for s in data.get("settings", []) if isinstance(s, dict) and "key" in s
        }
    return defaults | baseline | brain


def load_baseline() -> dict[str, list[str]]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def find_regressions(
    current: dict[str, list[str]], baseline: dict[str, list[str]]
) -> list[tuple[str, str]]:
    """``(relpath, key)`` for every phantom read not already baselined at
    that file — a new file reading an already-known-bad key counts, same as
    bandit's per-file-per-rule shape."""
    out: list[tuple[str, str]] = []
    for rel, keys in current.items():
        known = set(baseline.get(rel, []))
        for key in keys:
            if key not in known:
                out.append((rel, key))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Settings phantom-read ratchet lint.")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate settings_phantom_read_baseline.json from the current tree.",
    )
    args = parser.parse_args()

    require_dir(PKG, lint=LINT)
    require_dir(SVC, lint=LINT)
    require_dir(SVC / "migrations", lint=LINT)

    current, n_files = find_phantom_reads()
    require_scanned(n_files, lint=LINT, what="python files", roots=(PKG,))

    total = sum(len(v) for v in current.values())

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"{LINT}: baseline written — {total} phantom read(s) across "
            f"{len(current)} file(s) grandfathered."
        )
        return 0

    regressions = find_regressions(current, load_baseline())
    if regressions:
        print("NEW PHANTOM SETTINGS READ (not in baseline):")
        for rel, key in regressions:
            print(f"  {rel}: {key!r}")
        print(
            "\nThis key is read from app_settings but no seed source "
            "(settings_defaults.py / 0000_baseline.seeds.sql / "
            "brain/seed_app_settings.json) defines it, so it can never be "
            "tuned — every read silently falls back to whatever default is "
            "baked into the call site.\n"
            "  * genuine bug (wrong/renamed key name) -> point the reader at "
            "the real, already-seeded key.\n"
            "  * genuine gap (a real tunable nobody seeded) -> add it to "
            "settings_defaults.py's DEFAULTS with the exact value the code "
            "already falls back to (behavior-neutral), and to METADATA "
            "(owner + value_type). If the same key is ALSO in "
            "0000_baseline.seeds.sql, they must agree — see "
            "settings_seed_value_drift_lint.py.\n"
            "  * unseeded ON PURPOSE (legacy alias, OSS-privacy redaction, "
            "bootstrap credential, third-party key) -> add it to ALLOWLIST "
            "in this file with the reason.\n"
            "If you intentionally fixed a BASELINED entry, re-run with "
            "--update-baseline to lock the win in."
        )
        return 1

    print(
        f"{LINT}: clean — no new phantom reads "
        f"({total} baselined across {len(current)} file(s), "
        f"{len(ALLOWLIST)} by-design key(s) allowlisted; ratchet only shrinks)."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScanFloorError as exc:
        print(f"{LINT}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

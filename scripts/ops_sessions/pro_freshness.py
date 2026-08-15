"""pro-freshness — weekly rebuild of the Poindexter Pro deliverable repo.

The "continuously updated" half of the Pro promise (glad-labs-stack#3216,
spec §7.8). The June 2026 build shipped `Glad-Labs/poindexter-pro` as a
one-shot artifact and nothing regenerated it since; this session rebuilds
the machine-derived artifacts from the LIVE system every week and pushes
the diff, so `git pull` on the buyer's side actually delivers freshness.

What gets rebuilt (and from where):

- ``config/seed-settings.json`` — **live prod ``app_settings`` values**
  (non-secret, non-empty), NOT ``settings_defaults.py``. The tuned seed IS
  the paid product; the OSS defaults are free by definition. Keys are
  dropped when they are secret-shaped, identity-category (the buyer wants
  their own site name, not ours), on the explicit operator-only list, or
  when the VALUE embeds operator-specific content (gladlabs URLs, org
  refs) — every drop is counted and reported, never silent.
- ``prompts/*.prompt.md`` + ``manifest.json`` — the live SKILL.md packs
  (poindexter#825 made them the prompt source of truth; the June build's
  Langfuse export is the era this replaces). Regenerated from scratch so
  stale files can't linger.
- ``dashboards/*.json`` — the premium Grafana boards, re-copied from the
  live provisioning tree (``revenue`` comes from ``dashboards-parked/``
  until monetization writes real events).
- ``book/`` — NOT edited (prose needs judgment); scanned for deleted-code
  fossils + stale prices, and drift is reported in the push note.

Safety: every generated file passes the PII/secret scrub gate before
anything is committed; a violation aborts the push and pages the operator.
The session pushes straight to the artifact repo's main (it is a build
output, not reviewed source), using the operator's local gh/git auth.

Runs under ``run-session.sh pro-freshness`` (no stack worktree needed — it
never commits to glad-labs-stack; its write target is its own clone under
``~/.poindexter/build/poindexter-pro``).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

from _common import asyncio_run, fetch_all, get_logger, notify_fail, run

PRO_REPO = "Glad-Labs/poindexter-pro"
CLONE_DIR = Path.home() / ".poindexter" / "build" / "poindexter-pro"

# Premium board set (June build's selection; revenue rides along parked).
PREMIUM_BOARDS = (
    "pipeline-merged",
    "qa-rails",
    "cost-analytics",
    "observability-merged",
    "revenue",
)

# ---------------------------------------------------------------------------
# scrub gate — ported from the 2026-06-10 deliverable plan (the build scripts
# it specified never landed in the pro repo, so the filter lives here now,
# versioned next to the system it protects).
# ---------------------------------------------------------------------------

_PII_PATTERNS = [
    r"Matthew(\s+M\.?)?\s+Gladding",
    r"\bMatt\s+Gladding\b",
    r"\bmattm\b",
    r"/home/mattm",
    r"[A-Za-z0-9._%+-]+@gladlabs\.io",
    r"linkedin\.com/in/[A-Za-z0-9\-]+",
    r"discord\.com/api/webhooks/",
    # Dead-Gitea port EXACTLY — the June plan's broader localhost:30\d\d also
    # matched dashboard deeplinks to localhost:3010 (Langfuse), which are
    # CORRECT for a self-hosted buyer (same port on their own stack).
    r"localhost:3001\b",
    r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",  # tailnet IPs
    r"[a-z0-9-]+\.taild[a-z0-9]+\.ts\.net",  # tailnet hosts
]

_SECRET_VALUE_PATTERNS = [
    r"postgres(ql)?://[^\s\"']+",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bsk-[A-Za-z0-9][A-Za-z0-9\-]{10,}\b",
    r"\bwhsec_[A-Za-z0-9+/=]{8,}",
    r"\bghp_[A-Za-z0-9]{20,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    r"\benc:v1:[A-Za-z0-9+/=]+",
]

_SCRUB_RES = [re.compile(p, re.IGNORECASE) for p in (_PII_PATTERNS + _SECRET_VALUE_PATTERNS)]

# Secret-shaped KEY names never ship in the seed even when is_secret=false.
# Deliberately suffix-only: naming specific private-overlay keys here would
# itself leak overlay vocabulary into the public mirror (the mirror-safety
# guard rejects exactly that) — overlay keys are instead excluded wholesale
# by the OSS-membership filter in build_seed().
_SECRET_KEY_RE = re.compile(
    r"(_api_key|_secret_key|_secret|_password|_token|_access_key|_webhook_url)$"
)

# Bare-UUID values are operator account/integration identifiers (e.g. the
# Postiz integration ids) — meaningless on a buyer's stack, so they never
# belong in a shipped default.
_UUID_VALUE_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# Operator-specific VALUES that are wrong as a buyer's starting config even
# though they aren't PII (our URLs, our org's repos, our brand).
_OPERATOR_VALUE_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (r"gladlabs\.(io|ai)", r"\bGlad[- ]Labs\b", r"\bpoindexter-pro\b")
]

# Keys that are per-operator by nature, harmless but useless to a buyer.
_OPERATOR_ONLY_KEYS = frozenset(
    {"operator_timezone", "social_x_handle", "social_x_url"}
)
# ...and per-operator key FAMILIES: Postiz integration ids are cuid row ids
# of the operator's own connected social accounts (not UUIDs, so the value
# heuristic can't catch them; the vocabulary is public OSS, so naming the
# prefix here is mirror-safe).
_OPERATOR_ONLY_KEY_PREFIXES = ("postiz_integration_id_",)

# Deleted-code fossils + retired prices the book must not teach (plan §5.3).
_BOOK_BANNED = (
    "content_tasks",
    "task_executor",
    "cross_model_qa",
    "workflow_executor",
    "localhost:3001",
)
_BOOK_BANNED_RES = [re.compile(rf"\${n}\b") for n in ("9", "89", "39", "29")]


def scan_text(text: str, *, source: str) -> list[str]:
    """Return 'source: pattern -> match' violation lines (empty = clean)."""
    out: list[str] = []
    for rx in _SCRUB_RES:
        for m in rx.finditer(text):
            out.append(f"{source}: {rx.pattern} -> {m.group(0)!r}")
    return out


# ---------------------------------------------------------------------------
# builders (pure where possible — unit-tested in tests/unit/scripts/)
# ---------------------------------------------------------------------------


def build_seed(
    rows: list[tuple[str, str]], resolve_category, known_keys: frozenset[str]
) -> tuple[dict[str, str], dict[str, int]]:
    """Shape live app_settings rows into the shippable tuned seed.

    ``known_keys`` is the OSS engine's own key universe (``DEFAULTS`` from
    ``settings_defaults.py``): a live key the public engine never reads is
    either private-overlay config or garbage, and shipping it would leak
    overlay vocabulary to buyers — so membership is the FIRST filter, which
    is also what lets the drop rules below stay generic instead of naming
    private keys in public source.

    Returns ``(seed, drop_counts)``. Every exclusion bumps a named counter
    so the CHANGELOG can say what was withheld and why — a scrub that
    silently shrinks the product would be worse than one that reports.
    """
    seed: dict[str, str] = {}
    drops = {
        "not_in_oss": 0,
        "secret_shaped": 0,
        "identity": 0,
        "operator_only": 0,
        "operator_value": 0,
        "scrub": 0,
    }
    for key, value in rows:
        if key not in known_keys:
            drops["not_in_oss"] += 1
            continue
        if _SECRET_KEY_RE.search(key):
            drops["secret_shaped"] += 1
            continue
        if key in _OPERATOR_ONLY_KEYS or key.startswith(_OPERATOR_ONLY_KEY_PREFIXES):
            drops["operator_only"] += 1
            continue
        if resolve_category(key) == "identity":
            drops["identity"] += 1
            continue
        if any(rx.search(value) for rx in _OPERATOR_VALUE_RES) or _UUID_VALUE_RE.match(
            value.strip()
        ):
            drops["operator_value"] += 1
            continue
        if scan_text(value, source=key):
            drops["scrub"] += 1
            continue
        seed[key] = value
    return seed, drops


def build_prompts(skills_root: Path, out_dir: Path) -> list[dict[str, str]]:
    """Export every live SKILL.md pack as prompts/<group>.<skill>.prompt.md.

    The output dir is cleared of ``*.prompt.md`` first so the Langfuse-era
    export (or any renamed pack) can't linger as a stale file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.prompt.md"):
        stale.unlink()
    entries: list[dict[str, str]] = []
    for skill_md in sorted(skills_root.glob("*/*/SKILL.md")):
        group = skill_md.parent.parent.name
        skill = skill_md.parent.name
        key = f"{group}.{skill}"
        fname = f"{key}.prompt.md"
        front = (
            f"---\nkey: {key}\ngroup: {group}\nskill: {skill}\n"
            f"source: skill-md\n---\n\n"
        )
        (out_dir / fname).write_text(front + skill_md.read_text(encoding="utf-8"), encoding="utf-8")
        entries.append({"key": key, "skill": skill, "file": fname, "source": "skill-md"})
    return entries


def refresh_dashboards(stack_root: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """Re-copy the premium boards from the live provisioning tree.

    Returns ``(copied, missing)``; a missing source board is reported, not
    fatal — a renamed live board shouldn't strand the whole rebuild.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    grafana = stack_root / "infrastructure" / "grafana"
    copied: list[str] = []
    missing: list[str] = []
    for name in PREMIUM_BOARDS:
        src = grafana / "dashboards" / f"{name}.json"
        if not src.exists():
            src = grafana / "dashboards-parked" / f"{name}.json"
        if not src.exists():
            missing.append(name)
            continue
        (out_dir / f"{name}.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(name)
    return copied, missing


def scan_book(book_dir: Path) -> list[str]:
    """Report (never edit) deleted-code fossils + stale prices in the book."""
    hits: list[str] = []
    for md in sorted(book_dir.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        rel = md.relative_to(book_dir)
        for token in _BOOK_BANNED:
            if token in text:
                hits.append(f"{rel}: {token}")
        for rx in _BOOK_BANNED_RES:
            if rx.search(text):
                hits.append(f"{rel}: stale price {rx.pattern}")
    return hits


def verify_outputs(paths: list[Path], base: Path) -> list[str]:
    """Whole-output scrub gate — the last line of defense before a push."""
    violations: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        violations.extend(scan_text(text, source=str(path.relative_to(base))))
    return violations


def prepend_changelog(changelog: Path, entry: str) -> None:
    existing = changelog.read_text(encoding="utf-8") if changelog.exists() else "# Changelog\n"
    lines = existing.splitlines(keepends=True)
    head, rest = (lines[:1], lines[1:]) if lines and lines[0].startswith("#") else ([], lines)
    changelog.write_text("".join(head) + "\n" + entry + "".join(rest), encoding="utf-8")


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


def _stack_root() -> Path:
    return Path(os.environ.get("POINDEXTER_REPO", str(Path.home() / "glad-labs-website")))


def _ensure_clone(log) -> bool:
    if not (CLONE_DIR / ".git").exists():
        CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
        proc = run(["gh", "repo", "clone", PRO_REPO, str(CLONE_DIR)])
        if proc.returncode != 0:
            notify_fail(
                "pro-freshness: clone failed",
                f"gh repo clone {PRO_REPO} exited {proc.returncode}: {proc.stderr[:500]}",
                "pro-freshness",
            )
            return False
        return True
    for step in (["git", "fetch", "origin", "main", "--quiet"],
                 ["git", "checkout", "main", "--quiet"],
                 ["git", "reset", "--hard", "origin/main", "--quiet"],
                 ["git", "clean", "-fd", "--quiet"]):
        proc = run(step, cwd=str(CLONE_DIR))
        if proc.returncode != 0:
            notify_fail(
                "pro-freshness: clone refresh failed",
                f"{' '.join(step)} exited {proc.returncode}: {proc.stderr[:500]}",
                "pro-freshness",
            )
            return False
    log.info("clone ready at %s", CLONE_DIR)
    return True


def notify_info(title: str, detail: str) -> None:
    try:
        from brain.operator_notifier import notify_operator

        notify_operator(title, detail, source="pro-freshness", severity="info")
    except Exception:  # noqa: BLE001 — a note must never mask the build result
        get_logger("pro-freshness").warning("notify_operator failed: %s", title)


def main() -> int:
    log = get_logger("pro-freshness")
    stack = _stack_root()
    skills_root = stack / "src" / "cofounder_agent" / "skills"
    if not skills_root.exists():
        notify_fail(
            "pro-freshness: stack checkout not found",
            f"{skills_root} does not exist (POINDEXTER_REPO={stack})",
            "pro-freshness",
        )
        return 2

    # resolve_category comes from the live stack tree so identity-key policy
    # can never drift from the taxonomy the app itself uses.
    sys.path.insert(0, str(stack / "src" / "cofounder_agent"))
    from services.settings_categories import resolve_category  # noqa: E402

    if not _ensure_clone(log):
        return 2

    rows = [
        (r["key"], r["value"])
        for r in asyncio_run(
            fetch_all(
                "SELECT key, value FROM app_settings "
                "WHERE is_secret = false AND value <> '' ORDER BY key"
            )
        )
    ]
    from services.settings_defaults import DEFAULTS  # noqa: E402

    seed, drops = build_seed(rows, resolve_category, frozenset(DEFAULTS))
    seed_path = CLONE_DIR / "config" / "seed-settings.json"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        json.dumps(seed, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("seed: %d keys shipped, drops=%s", len(seed), drops)

    prompt_entries = build_prompts(skills_root, CLONE_DIR / "prompts")
    manifest = {
        "generated_from": "src/cofounder_agent/skills/*/*/SKILL.md",
        "mode": "skill-md",
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "keys": prompt_entries,
    }
    (CLONE_DIR / "prompts" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info("prompts: %d packs exported", len(prompt_entries))

    copied, missing_boards = refresh_dashboards(stack, CLONE_DIR / "dashboards")
    log.info("dashboards: copied=%s missing=%s", copied, missing_boards)

    book_drift = scan_book(CLONE_DIR / "book") if (CLONE_DIR / "book").exists() else []

    outputs = [seed_path, CLONE_DIR / "prompts" / "manifest.json"]
    outputs += sorted((CLONE_DIR / "prompts").glob("*.prompt.md"))
    outputs += [CLONE_DIR / "dashboards" / f"{n}.json" for n in copied]
    violations = verify_outputs(outputs, CLONE_DIR)
    if violations:
        detail = "PII/secret patterns matched in generated output:\n" + "\n".join(
            violations[:15]
        )
        if os.environ.get("PRO_FRESHNESS_DRY_RUN"):
            # Manual dry-run: the console IS the operator — don't page them.
            log.error("DRY RUN — scrub gate would refuse:\n%s", detail)
        else:
            notify_fail(
                "pro-freshness: scrub gate REFUSED the rebuild — nothing pushed",
                detail,
                "pro-freshness",
            )
        return 2

    status = run(["git", "status", "--porcelain"], cwd=str(CLONE_DIR))
    if not status.stdout.strip():
        log.info("no drift — deliverable already matches the live system")
        return 0

    if os.environ.get("PRO_FRESHNESS_DRY_RUN"):
        diff = run(["git", "diff", "--stat"], cwd=str(CLONE_DIR))
        log.info(
            "DRY RUN — would push:\n%s\nuntracked/status:\n%s",
            diff.stdout.strip(), status.stdout.strip()[:2000],
        )
        if book_drift:
            log.info("book drift (%d): %s", len(book_drift), "; ".join(book_drift[:10]))
        return 0

    today = _dt.date.today().isoformat()
    summary = (
        f"seed {len(seed)} keys (dropped: {sum(drops.values())} — {drops}), "
        f"{len(prompt_entries)} prompt packs, dashboards {', '.join(copied) or 'none'}"
        + (f"; MISSING boards: {', '.join(missing_boards)}" if missing_boards else "")
    )
    entry = (
        f"## {today} — automated freshness rebuild\n\n"
        f"- {summary}\n"
        + (
            "- book drift (needs a judgment pass, not auto-edited): "
            + "; ".join(book_drift[:8]) + "\n"
            if book_drift
            else ""
        )
        + "\n"
    )
    prepend_changelog(CLONE_DIR / "CHANGELOG.md", entry)

    for step in (
        ["git", "add", "-A", "config", "prompts", "dashboards", "CHANGELOG.md"],
        ["git", "commit", "-m", f"chore(freshness): rebuild from live system {today} — {summary}",
         "-m", "Automated by the pro-freshness ops session (glad-labs-stack#3216)."],
        ["git", "push", "origin", "main"],
    ):
        proc = run(step, cwd=str(CLONE_DIR))
        if proc.returncode != 0:
            notify_fail(
                f"pro-freshness: {step[1]} failed — rebuild not published",
                f"{' '.join(step)} exited {proc.returncode}: {(proc.stderr or proc.stdout)[:800]}",
                "pro-freshness",
            )
            return 2

    detail = f"Pushed to {PRO_REPO}: {summary}"
    if book_drift:
        detail += f"\nBook drift ({len(book_drift)} hits): " + "; ".join(book_drift[:8])
    if missing_boards:
        detail += f"\nMissing live boards: {', '.join(missing_boards)}"
    notify_info("pro-freshness: deliverable rebuilt", detail)
    log.info("pushed freshness rebuild: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

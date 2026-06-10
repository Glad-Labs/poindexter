# Poindexter Pro Deliverable Repo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `Glad-Labs/poindexter-pro` (private GitHub repo) containing the complete, scrubbed, accurate Poindexter Pro deliverable — premium prompt pack, config seed, premium dashboards, memory-system doc, a corrected operator book — built as a reproducible artifact from the live system, with **zero operator PII or secrets in history**.

**Architecture:** The repo is a **build artifact**, not a hand-maintained snapshot. Build scripts in `scripts/build/` regenerate the prompt pack (← Langfuse), the config seed (← `settings_defaults.py` minus secrets), and validate the book against the live codebase. A reusable **scrub filter** runs over every generated file before it lands, stripping operator identity + secret-adjacent values (the same posture as the poindexter public-mirror filter). Each phase is independently verifiable; Phases 0–4 produce a shippable machine-product, Phase 5 completes the book, Phase 6 is the final gate.

**Tech Stack:** Python 3 (build scripts, scrub filter, seed/prompt export), GitHub CLI (`gh`), Langfuse SDK (via `prompt_manager` client), pytest (script tests + scrub gates), grep/SQL verification gates.

**Source-of-truth paths (referenced, never copied wholesale):**

- Poindexter source repo: `C:\Users\mattm\glad-labs-website` (passed to build scripts as `--poindexter-root`)
- Old prompts/book repo (salvage source only): `C:\Users\mattm\glad-labs-prompts`
- New deliverable build root: `C:\Users\mattm\poindexter-pro`
- Secret-key blocklist generator: `glad-labs-website/src/cofounder_agent/scripts/extract_secret_keys.py`
- Seed source: `glad-labs-website/src/cofounder_agent/services/settings_defaults.py`
- Langfuse fetch path: `prompt_manager.py::_fetch_from_langfuse_with_meta(key)`; live at `localhost:3010`
- Premium prompt key set: the 14 content packs under `src/cofounder_agent/skills/content/*/SKILL.md`

**Out of scope (explicitly NOT this plan — these remain before `CHECKOUT_LIVE=true`):**

- Pay → deliver automation (LS webhook → GitHub collaborator-invite + Discord VIP role) — spec §7.3
- Freshness CI gate / 10th scheduled agent — spec §7.8
- Re-publishing the LS product + rotating the R2 key — operator manual actions (spec §10)

---

## File Structure (poindexter-pro repo layout)

```
poindexter-pro/
  README.md                      # product framing, $19/$180, git-pull activation, links gladlabs.ai
  LICENSE                        # proprietary — licensed to active Pro subscribers
  TERMS.md                       # subscription terms, cancellation, what "keep what you downloaded" means
  CHANGELOG.md                   # seeded; build scripts append on each regen
  .gitignore
  prompts/
    manifest.json                # key -> {skill, langfuse_version, source} map
    <skill>.prompt.md            # one per premium key (blog-generation, content-qa, research, ...)
  config/
    seed-settings.json           # scrubbed app_settings defaults (no secrets, no PII)
    README.md                    # how to apply the seed
  dashboards/
    <board>.json                 # premium live Grafana boards, datasource-verified
    README.md                    # import instructions
  book/
    README.md                    # reading order
    chapters/01..15-*.md
    appendix-c-model-recommendations.md
    appendix-d-troubleshooting.md
  memory-system.md               # salvaged + scrubbed from bonus-memory-system.md
  scripts/
    build/
      scrub_filter.py            # reusable PII/secret scrubber (Phase 0)
      build_seed.py              # settings_defaults.py -> config/seed-settings.json (Phase 1)
      build_prompts.py           # Langfuse export -> prompts/ (Phase 2)
      verify_deliverable.py      # full-repo gate: PII/secret/price/deleted-code grep (Phase 6)
    tests/
      test_scrub_filter.py
      test_build_seed.py
      test_build_prompts.py
```

---

## Phase 0 — Repo + scrub-filter foundation

### Task 0.1: Create the empty private GitHub repo

**Files:** none (GitHub + local clone)

- [ ] **Step 1: Create the private repo**

Run:

```bash
gh repo create Glad-Labs/poindexter-pro --private \
  --description "Poindexter Pro — premium tuned prompts, config, dashboards & operator book for the Poindexter publishing engine. Licensed to active Pro subscribers." \
  --gitignore Python
```

Expected: `✓ Created repository Glad-Labs/poindexter-pro on GitHub`

- [ ] **Step 2: Clone to the build root**

Run:

```bash
git -C /c/Users/mattm clone https://github.com/Glad-Labs/poindexter-pro.git
```

Expected: clones into `C:\Users\mattm\poindexter-pro` with the Python `.gitignore`.

- [ ] **Step 3: Confirm no Gitea remote, only GitHub origin**

Run: `git -C /c/Users/mattm/poindexter-pro remote -v`
Expected: only `origin https://github.com/Glad-Labs/poindexter-pro.git` (no `localhost:3001`).

- [ ] **Step 4: Add directory skeleton + .gitignore additions**

Create `C:\Users\mattm\poindexter-pro\.gitignore` (append to the generated one):

```
# build intermediates / never-ship
*.pyc
__pycache__/
.env
*.local
/_staging/
```

Create empty dirs with `.gitkeep`: `prompts/`, `config/`, `dashboards/`, `book/chapters/`, `scripts/build/`, `scripts/tests/`.

- [ ] **Step 5: Commit the skeleton**

```bash
cd /c/Users/mattm/poindexter-pro
git add -A && git commit -m "chore: repo skeleton + gitignore"
```

### Task 0.2: Reusable scrub filter (TDD)

**Files:**

- Create: `poindexter-pro/scripts/build/scrub_filter.py`
- Test: `poindexter-pro/scripts/tests/test_scrub_filter.py`

The scrub filter is the safety backbone: every generated file passes through it before commit. It detects and rejects (or redacts) operator PII + secret-adjacent content. Blocklist patterns derive from the audit (§6 blocker 2) + `extract_secret_keys.py`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_scrub_filter.py
import pytest
from scripts.build.scrub_filter import scan_text, ScrubViolation, scrub_settings

PII_SAMPLES = [
    "Matthew Gladding",
    "Matthew M. Gladding",
    "mattg@gladlabs.io",
    "linkedin.com/in/matthew-gladding",
    "telegram_chat_id = 123456789",
    "localhost:3001",                # dead Gitea
    "100.81.93.12",                  # tailnet IP
    "nightrider.taild4f626.ts.net",  # tailnet host
]

SECRET_SAMPLES = [
    "storage_access_key = AKIAEXAMPLE1234567890",
    "cloudflare_account_id = abcd1234abcd1234abcd1234abcd1234",
    "langfuse_secret_key = sk-lf-deadbeef",
    "database_url = postgresql://u:p@h/db",
]

CLEAN_SAMPLES = [
    "Glad Labs builds the Poindexter engine.",       # public brand OK
    "Visit https://www.gladlabs.ai to subscribe.",   # public URL OK
    "Poindexter Pro is $19/mo or $180/yr.",          # public price OK
]

@pytest.mark.parametrize("s", PII_SAMPLES + SECRET_SAMPLES)
def test_scan_flags_pii_and_secrets(s):
    violations = scan_text(s, source="sample")
    assert violations, f"expected a violation for: {s!r}"

@pytest.mark.parametrize("s", CLEAN_SAMPLES)
def test_scan_passes_clean_public_content(s):
    assert scan_text(s, source="sample") == []

def test_scrub_settings_drops_secret_keys():
    rows = {"writer_model": "gemma3:27b", "langfuse_secret_key": "sk-lf-x", "telegram_chat_id": "999"}
    out = scrub_settings(rows)
    assert "writer_model" in out
    assert "langfuse_secret_key" not in out
    assert "telegram_chat_id" not in out
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd /c/Users/mattm/poindexter-pro && python -m pytest scripts/tests/test_scrub_filter.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.build.scrub_filter`.

- [ ] **Step 3: Implement the scrub filter**

```python
# scripts/build/scrub_filter.py
"""Reusable PII/secret scrubber for the Poindexter Pro deliverable.

Every generated file passes through scan_text() before commit; scrub_settings()
filters an app_settings dict down to ship-safe, non-secret keys. Public brand
facts (the company name, gladlabs.ai/.io, the $19/$180 price) are explicitly
allowed — only OPERATOR identity + secret-adjacent values are flagged.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

# Operator PII + dead-infra identifiers that must never ship.
_PII_PATTERNS = [
    r"Matthew(\s+M\.?)?\s+Gladding",
    r"\bMatt\s+Gladding\b",
    r"[A-Za-z0-9._%+-]+@gladlabs\.io",         # operator email (brand domain, personal mailbox)
    r"linkedin\.com/in/[A-Za-z0-9\-]+",
    r"\btelegram_chat_id\b",
    r"\bdiscord_ops_webhook_url\b",
    r"localhost:30\d\d",                         # dead Gitea / internal services
    r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",       # tailnet IPs
    r"[a-z0-9-]+\.taild[a-z0-9]+\.ts\.net",      # tailnet hosts
    r"\bmercury_balance\b",
]

# Secret-adjacent key/value shapes.
_SECRET_PATTERNS = [
    r"\b\w*_api_key\b",
    r"\b\w*_secret(_key)?\b",
    r"\b\w*_password\b",
    r"\bstorage_access_key\b",
    r"\bcloudflare_account_id\b",
    r"\bdatabase_url\b",
    r"\boperator_id\b",
    r"postgres(ql)?://[^\s\"']+",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bsk-[A-Za-z0-9\-]{8,}\b",
]

# Secret key-name suffixes for settings filtering (mirrors extract_secret_keys.py).
_SECRET_KEY_RE = re.compile(
    r"(_api_key|_secret_key|_secret|_password|_token|_access_key|_webhook_url)$"
    r"|^(database_url|operator_id|telegram_chat_id|cloudflare_account_id|mercury_balance)$"
)

_ALL = [(re.compile(p, re.IGNORECASE), p) for p in (_PII_PATTERNS + _SECRET_PATTERNS)]


@dataclass
class ScrubViolation:
    source: str
    pattern: str
    match: str


def scan_text(text: str, *, source: str) -> list[ScrubViolation]:
    out: list[ScrubViolation] = []
    for rx, pat in _ALL:
        for m in rx.finditer(text):
            out.append(ScrubViolation(source=source, pattern=pat, match=m.group(0)))
    return out


def scrub_settings(rows: dict[str, str]) -> dict[str, str]:
    """Drop secret keys; keep non-secret defaults verbatim."""
    return {k: v for k, v in rows.items() if not _SECRET_KEY_RE.search(k)}
```

Create empty `scripts/__init__.py`, `scripts/build/__init__.py`, `scripts/tests/__init__.py` so imports resolve.

- [ ] **Step 4: Run test, verify it passes**

Run: `python -m pytest scripts/tests/test_scrub_filter.py -v`
Expected: PASS (all parametrized cases green).

- [ ] **Step 5: Commit**

```bash
git add scripts/ && git commit -m "feat(build): reusable PII/secret scrub filter with tests"
```

### Task 0.3: LICENSE + TERMS (proprietary)

**Files:** Create `poindexter-pro/LICENSE`, `poindexter-pro/TERMS.md`

- [ ] **Step 1: Write LICENSE** (proprietary — NOT Apache-2.0; the free engine is Apache-2.0, this content is not)

```
Poindexter Pro — Proprietary License

Copyright (c) 2026 Glad Labs LLC. All rights reserved.

This repository and its contents (the "Materials") are licensed, not sold, to
individuals and organizations with an active Poindexter Pro subscription.

You MAY: use the Materials to operate your own Poindexter deployment; modify
them for your own use.

You MAY NOT: redistribute, resell, sublicense, or publish the Materials, in
whole or in part, to any third party; share repository access with anyone who
does not hold an active subscription.

Access is granted while your subscription is active. On cancellation you may
retain copies already downloaded for your own use, but lose access to future
updates. The free Poindexter engine is licensed separately under Apache-2.0 and
is unaffected by this license.
```

- [ ] **Step 2: Write TERMS.md** — short: what Pro includes, $19/mo · $180/yr Founding rate, cancellation keeps downloaded copies, updates stop on cancel, support via VIP Discord. No operator PII. Link gladlabs.ai.

- [ ] **Step 3: Scrub-check + commit**

Run: `python -c "from scripts.build.scrub_filter import scan_text; import pathlib; [print(v) for f in ['LICENSE','TERMS.md'] for v in scan_text(pathlib.Path(f).read_text(encoding='utf-8'), source=f)]"`
Expected: no output (clean).

```bash
git add LICENSE TERMS.md && git commit -m "docs: proprietary Pro license + terms"
```

---

## Phase 1 — Scrubbed config seed (build artifact)

### Task 1.1: build_seed.py (TDD)

**Files:**

- Create: `poindexter-pro/scripts/build/build_seed.py`
- Test: `poindexter-pro/scripts/tests/test_build_seed.py`
- Output: `poindexter-pro/config/seed-settings.json`

`build_seed.py` imports the `DEFAULTS` dict from the poindexter source `settings_defaults.py`, runs it through `scrub_settings()`, and writes a sorted JSON seed. It must exclude every secret key and emit no PII.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_build_seed.py
import json, pathlib, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parents[2]
POINDEXTER_ROOT = pathlib.Path("C:/Users/mattm/glad-labs-website")

def test_build_seed_produces_clean_json(tmp_path):
    out = tmp_path / "seed-settings.json"
    rc = subprocess.run(
        [sys.executable, str(REPO / "scripts/build/build_seed.py"),
         "--poindexter-root", str(POINDEXTER_ROOT), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert rc.returncode == 0, rc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) > 200                      # the bulk of non-secret defaults
    # No secret keys leaked:
    for k in data:
        assert not k.endswith(("_api_key", "_secret", "_secret_key", "_password", "_token", "_access_key", "_webhook_url"))
        assert k not in ("database_url", "operator_id", "telegram_chat_id", "mercury_balance")
    # No PII anywhere in serialized form:
    from scripts.build.scrub_filter import scan_text
    assert scan_text(out.read_text(encoding="utf-8"), source="seed") == []
```

- [ ] **Step 2: Run test, verify it fails** — `python -m pytest scripts/tests/test_build_seed.py -v` → FAIL (no `build_seed.py`).

- [ ] **Step 3: Implement build_seed.py**

```python
# scripts/build/build_seed.py
"""Regenerate config/seed-settings.json from the live poindexter settings_defaults.

Usage:
  python scripts/build/build_seed.py --poindexter-root <path> --out config/seed-settings.json
"""
from __future__ import annotations
import argparse, importlib.util, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from scripts.build.scrub_filter import scrub_settings, scan_text  # noqa: E402


def load_defaults(poindexter_root: pathlib.Path) -> dict[str, str]:
    path = poindexter_root / "src/cofounder_agent/services/settings_defaults.py"
    spec = importlib.util.spec_from_file_location("settings_defaults", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    defaults = getattr(mod, "DEFAULTS", None)
    if not isinstance(defaults, dict):
        raise SystemExit("settings_defaults.DEFAULTS not found or not a dict")
    return dict(defaults)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poindexter-root", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()

    scrubbed = scrub_settings(load_defaults(args.poindexter_root))
    payload = json.dumps(scrubbed, indent=2, sort_keys=True, ensure_ascii=False)

    violations = scan_text(payload, source="seed-settings.json")
    if violations:
        for v in violations:
            print(f"SCRUB VIOLATION: {v.pattern} -> {v.match!r}", file=sys.stderr)
        raise SystemExit("refusing to write seed: PII/secret detected")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload + "\n", encoding="utf-8")
    print(f"wrote {len(scrubbed)} keys -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test, verify it passes** — `python -m pytest scripts/tests/test_build_seed.py -v` → PASS.

- [ ] **Step 5: Generate the actual seed + write config/README.md**

Run:

```bash
python scripts/build/build_seed.py --poindexter-root /c/Users/mattm/glad-labs-website --out config/seed-settings.json
```

Expected: `wrote <N> keys -> config/seed-settings.json`.
Write `config/README.md`: how to apply (`poindexter setup` reads `settings_defaults`; this seed is the known-good Pro baseline to diff/import), note secrets are intentionally absent (operator sets via `poindexter` secret API).

- [ ] **Step 6: Commit**

```bash
git add scripts/ config/ && git commit -m "feat(config): scrubbed seed-settings build from live defaults"
```

---

## Phase 2 — Premium prompt pack (Langfuse export, build artifact)

### Task 2.1: Resolve the premium key set + Langfuse reachability

**Files:** none (investigation, recorded in `prompts/manifest.json` next task)

- [ ] **Step 1: Enumerate the 14 content skill keys**

Run: `ls -1 /c/Users/mattm/glad-labs-website/src/cofounder_agent/skills/content/`
Record the skill dirs. For each, read its `SKILL.md` front-matter/key to learn the Langfuse prompt key(s) it loads (grep `prompt_manager` / `get(` calls in the corresponding `modules/content` code if the key name isn't literal in SKILL.md).

- [ ] **Step 2: Probe Langfuse reachability**

Run (from poindexter source, its venv): a one-off that constructs the `prompt_manager` Langfuse client using `app_settings` creds and calls `list`/`get` for one known key (e.g. via `_fetch_from_langfuse_with_meta`). If it returns a body+version → live path. If unreachable → **fallback mode** (Step 3).
Expected: a JSON dump of `{key, version, body_len}` for the probed key.

- [ ] **Step 3: Decide source mode**

- **Live (preferred):** Langfuse returns bodies → export those.
- **Fallback:** Langfuse unreachable → use `src/cofounder_agent/skills/content/*/SKILL.md` bodies as the baseline and `glad-labs-prompts/db_prompt_templates.json` as the historical archive overlay; mark `source` accordingly in the manifest and `log()` the downgrade. (Per spec salvage note, `db_prompt_templates.json` is archive/fallback, NOT the primary source.)

### Task 2.2: build_prompts.py (TDD)

**Files:**

- Create: `poindexter-pro/scripts/build/build_prompts.py`
- Test: `poindexter-pro/scripts/tests/test_build_prompts.py`
- Output: `poindexter-pro/prompts/*.prompt.md` + `prompts/manifest.json`

- [ ] **Step 1: Write the failing test** (mode-agnostic: assert structure + scrub-cleanliness, not exact prose)

```python
# scripts/tests/test_build_prompts.py
import json, pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

def test_manifest_covers_all_keys_and_is_clean():
    manifest = REPO / "prompts/manifest.json"
    assert manifest.exists(), "run build_prompts.py first"
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["keys"], "manifest has no keys"
    from scripts.build.scrub_filter import scan_text
    for entry in m["keys"]:
        body = (REPO / "prompts" / entry["file"]).read_text(encoding="utf-8")
        assert body.strip(), f"empty prompt body: {entry['file']}"
        assert scan_text(body, source=entry["file"]) == []
        assert entry["source"] in ("langfuse", "skill-md", "db-archive")
```

- [ ] **Step 2: Run test, verify it fails** — FAIL (no manifest).

- [ ] **Step 3: Implement build_prompts.py**

Real code: accept `--poindexter-root`, `--mode {langfuse,fallback}`, `--out-dir prompts`. In `langfuse` mode, import the poindexter `prompt_manager`, build the client from `app_settings` creds, and for each resolved key call `_fetch_from_langfuse_with_meta(key)` → `(body, version)`. In `fallback` mode, read each `skills/content/<skill>/SKILL.md` body and, where a matching key exists in `db_prompt_templates.json`, prefer the archived body. For every body: run `scan_text`; on any violation, **abort** (do not write). Write one `prompts/<skill>.prompt.md` per key with a small YAML front-matter (`key`, `skill`, `source`, `langfuse_version`), and a `prompts/manifest.json` `{generated_from, mode, keys:[{key,skill,file,source,langfuse_version}]}`. (Mirror the abort-on-violation + arg-parsing shape of `build_seed.py`.)

- [ ] **Step 4: Run the build (live or fallback per Task 2.1 Step 3)**

Run: `python scripts/build/build_prompts.py --poindexter-root /c/Users/mattm/glad-labs-website --mode <resolved> --out-dir prompts`
Expected: `wrote <N> prompts -> prompts/` and a manifest.

- [ ] **Step 5: Run test, verify it passes** — `python -m pytest scripts/tests/test_build_prompts.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/ prompts/ && git commit -m "feat(prompts): premium prompt pack build from Langfuse (fallback: SKILL.md+archive)"
```

---

## Phase 3 — Premium dashboards

### Task 3.1: Select + scrub premium dashboards

**Files:** Create `poindexter-pro/dashboards/*.json`, `dashboards/README.md`

The old repo's `approval-queue`/`quality-content` names are stale. Source the genuinely-premium boards from the **live** set in `glad-labs-website/infrastructure/grafana/dashboards/` (12 boards). Candidate premium set (customer-useful, not operator-only): `pipeline-merged`, `qa-rails`, `cost-analytics`, `revenue`, `observability-merged`. **Exclude** operator-only boards (`hardware-power` — Matt's PSU/GPU; `integrations-admin` — operator infra; `findings`/`experiments-dryrun` — internal).

- [ ] **Step 1: Inventory + datasource audit**

For each candidate board, grep its JSON for operator-only datasource UIDs, hardcoded tailnet URLs, panel titles naming Matt/hardware. Record which pass.

- [ ] **Step 2: Copy passing boards through the scrub filter**

For each selected board: read JSON, run `scan_text`; if clean, write to `dashboards/<board>.json`; if it has a stray internal URL/datasource, neutralize (generic datasource placeholder) then re-scan. Abort-on-violation.

- [ ] **Step 3: Write dashboards/README.md** — import instructions (Grafana → Import → upload JSON; set the Prometheus/Postgres datasource), note these mirror the live premium boards.

- [ ] **Step 4: Verify + commit**

Run: `python -c "import pathlib,glob; from scripts.build.scrub_filter import scan_text; [print(f,v) for f in glob.glob('dashboards/*.json') for v in scan_text(pathlib.Path(f).read_text(encoding='utf-8'),source=f)]"`
Expected: no output.

```bash
git add dashboards/ && git commit -m "feat(dashboards): premium Grafana boards, datasource-verified + scrubbed"
```

---

## Phase 4 — Memory-system doc + README

### Task 4.1: Salvage + scrub the memory-system doc

**Files:** Create `poindexter-pro/memory-system.md` (from `glad-labs-prompts/claude-templates/bonus-memory-system.md`)

- [ ] **Step 1: Copy + scrub**

Read the source, run `scan_text`; redact/rewrite any flagged line (replace operator specifics with generic guidance); write `memory-system.md`. Re-scan until clean.

- [ ] **Step 2: Verify + commit**

Run: `python -c "from scripts.build.scrub_filter import scan_text; import pathlib; print(scan_text(pathlib.Path('memory-system.md').read_text(encoding='utf-8'), source='memory-system.md'))"`
Expected: `[]`.

```bash
git add memory-system.md && git commit -m "docs: salvage + scrub memory-system guide"
```

### Task 4.2: README.md (the front door)

**Files:** Create `poindexter-pro/README.md`

- [ ] **Step 1: Write README** — product framing (what Pro is, who it's for), the offer ($19/mo · $180/yr Founding Member rate, locked for life), **activation = `git pull` (collaborator-invite model)** — NO Gitea, NO `localhost:3001`. Contents map (prompts/ config/ dashboards/ book/ memory-system.md). Link `https://www.gladlabs.ai`. "Continuously updated — re-run `git pull` for the latest tuning." No operator PII.

- [ ] **Step 2: Scrub-check + commit**

Run: `python -c "from scripts.build.scrub_filter import scan_text; import pathlib; print(scan_text(pathlib.Path('README.md').read_text(encoding='utf-8'), source='README.md'))"`
Expected: `[]`.

```bash
git add README.md && git commit -m "docs: product README — offer, contents, git-pull activation"
```

---

## Phase 5 — The corrected book (long pole)

The book ships 15 chapters + 2 appendices + outline. Per the audit (§6) + spec §7.6, ~half teach deleted code. **Execution model:** workflow-driven — one correction agent per chapter, each editing against the **live** poindexter source (`glad-labs-website`) + `CLAUDE.md`, followed by an adversarial verify agent per chapter (does any deleted-code reference survive? does the SQL run on a fresh DB?). Chapters are corrected into `poindexter-pro/book/chapters/`.

**Global fixes (apply to every chapter):**

- `content_tasks` → `pipeline_tasks` (table was renamed; old name → "relation does not exist")
- Strip Gitea / `localhost:3001` CI references → GitHub Actions two-remote model
- Price strings `$9` / `$89` / `$39` / `$29` / `$9.99` → `$19/mo · $180/yr`
- Static-Bearer auth → OAuth 2.1 client-credentials (`POST /token`)
- Run every code/SQL block's claims past the live source before keeping them.

**Per-chapter change map (from spec §7.6 + audit):**

| Chapter                     | Required correction                                                                              | Verify gate                                 |
| --------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------- |
| 01 why-architecture         | Light: reconcile vocabulary (kernel/module/capability), drop dead brain-region labels as primary | no `task_executor`/`cross_model_qa`         |
| 02 system-overview          | Strip Gitea; current container topology (worker/prefect-worker/brain split)                      | no `localhost:3001`                         |
| 03 the-foundation           | Strip Gitea CI; two-remote sync model                                                            | no Gitea                                    |
| 04 fastapi-backend          | **Rewrite:** Prefect dispatch (not `task_executor`), OAuth 2.1 (not static Bearer)               | no `task_executor`; OAuth present           |
| 05 nextjs-frontend          | Next.js 16 App Router; tag-based `revalidateTag` (not time-ISR)                                  | no `getStaticProps`/`revalidate:` ISR claim |
| 06 research-drafting        | Reconcile to RAG engine + dispatcher; model-tier router                                          | no hardcoded model names as API             |
| 07 multi-model-qa           | **Rewrite:** `qa.*` atoms → `qa.aggregate` (the `cross_model_qa` stage is deleted)               | no `cross_model_qa`                         |
| 08 seo-training-data        | `seo.*` atoms; capture_training_data atom                                                        | matches graph_def                           |
| 09 publishing-distribution  | Next.js 16 revalidation; R2/static-export propagation; `publishing_adapters`                     | no Gitea deploy                             |
| 10 brain-daemon             | Containerized brain (was host process); strip Gitea                                              | no host-process claim                       |
| 11 grafana-monitoring       | **Rewrite:** real 12-board inventory (current dashboards)                                        | board list matches live                     |
| 12 db-driven-config         | `app_settings` ~901 keys; `_usd` cost keys; secret API                                           | key counts reconciled                       |
| 13 side-project-to-business | Reconcile monetization to $19/$180 single Pro tier                                               | no stale pricing                            |
| 14 deploying-production     | Strip Gitea; Vercel + local worker/brain; two-remote                                             | no Gitea                                    |
| 15 whats-next               | Light: roadmap reconcile                                                                         | no deleted-code refs                        |
| appendix-c-models           | Reconcile model recs to current (glm/gemma3:27b; 72B infeasible note)                            | matches model_constants/cost_tier           |
| appendix-d-troubleshooting  | **Rewrite SQL:** `content_tasks`→`pipeline_tasks`; every query runs on fresh DB                  | fresh-DB SQL run passes                     |

### Task 5.1: Stage the book + delete the orphan SKUs

- [ ] **Step 1: Copy chapters into the deliverable as the working base**

Copy `glad-labs-prompts/docs/guide/chapters/*.md`, `appendix-c-*.md`, `appendix-d-*.md`, and `ai-content-pipeline-guide-outline.md` → `poindexter-pro/book/`. Do NOT copy the 5 role templates (`claude-templates/01..05-*.md`) or the `$29` README/README.pdf (orphan SKU — deleted per spec §7.7). `bonus-memory-system.md` already folded in (Phase 4).

- [ ] **Step 2: Commit the uncorrected base** (so the correction diff is reviewable)

```bash
git add book/ && git commit -m "chore(book): stage uncorrected chapters as correction base"
```

### Task 5.2: Workflow-driven per-chapter correction

- [ ] **Step 1: Run the book-correction workflow** (ultracode — pipeline: correct → adversarially verify per chapter)

Launch a Workflow that, for each chapter in the change map: (a) a correction agent edits `book/chapters/<ch>.md` applying the global fixes + that row's required correction, reading the live `glad-labs-website` source to confirm every claim; (b) a verify agent checks the row's gate (greps for the banned token; for appendix-d, actually runs each SQL block against a fresh DB per `docs/operations/fresh-db-setup.md`). Re-run correction for any chapter that fails its gate (loop-until-clean, max 2 rounds). Schema-validate each agent's report (chapter, edits_made, gate_passed, residual_issues).

- [ ] **Step 2: Aggregate + manual spot-review**

Read the workflow result. For any chapter still failing after 2 rounds, hand-correct. Spot-read the 4 **Rewrite** chapters (04, 07, 11, appendix-d) for prose quality + accuracy.

- [ ] **Step 3: Commit corrected book**

```bash
git add book/ && git commit -m "fix(book): reconcile all chapters to live system (Prefect/OAuth/qa.* atoms/12 boards/pipeline_tasks)"
```

### Task 5.3: Book-wide verification gate

- [ ] **Step 1: Banned-token grep gate**

Run:

```bash
cd /c/Users/mattm/poindexter-pro
grep -rnE "content_tasks|task_executor|cross_model_qa|model_router|localhost:3001|\\\$9\\b|\\\$89|\\\$39|\\\$29|workflow_executor|experiment_service" book/ ; echo "exit=$?"
```

Expected: no matches (grep `exit=1`). Any hit → fix → re-run.

- [ ] **Step 2: Fresh-DB SQL verification (appendix-d)**

Extract each SQL block from `book/appendix-d-troubleshooting.md`, run against a throwaway DB seeded per `glad-labs-website/docs/operations/fresh-db-setup.md`. Expected: every query executes (no "relation does not exist"). Record pass.

- [ ] **Step 3: Commit any fixes**

```bash
git add book/ && git commit -m "test(book): pass banned-token + fresh-DB SQL gates"
```

---

## Phase 6 — Final assembly + whole-repo gate

### Task 6.1: verify_deliverable.py (the ship gate)

**Files:** Create `poindexter-pro/scripts/build/verify_deliverable.py`

- [ ] **Step 1: Implement the whole-repo gate**

Walk every tracked text file (skip `.git/`, binaries). For each: run `scan_text` (PII/secret) AND a banned-token grep (deleted-code identifiers + stale prices from Phase 5 Step 1). Print a report; exit non-zero on any violation. This is the single command that asserts the deliverable is ship-safe.

- [ ] **Step 2: Run the gate**

Run: `python scripts/build/verify_deliverable.py`
Expected: `DELIVERABLE CLEAN — 0 PII/secret, 0 deleted-code, 0 stale-price violations` and exit 0. Fix anything it flags.

- [ ] **Step 3: Cross-check against the live audit (workflow w3chhbd8c)**

Confirm every confirmed secret/PII finding from the preflight audit is absent from `poindexter-pro` (it should be — nothing was copied wholesale). Record.

- [ ] **Step 4: Commit + push**

```bash
git add scripts/ && git commit -m "feat(build): whole-repo ship gate (PII/secret/deleted-code/price)"
git push origin main
```

### Task 6.2: CHANGELOG + handoff

- [ ] **Step 1: Write CHANGELOG.md** — v0.1.0 entry: what's included, build provenance (generated from live system on this date), what's intentionally deferred (book already in; note pay→deliver automation + freshness gate are the remaining `CHECKOUT_LIVE` gate).

- [ ] **Step 2: Commit + push; report to Matt**

```bash
git add CHANGELOG.md && git commit -m "docs: v0.1.0 changelog" && git push origin main
```

Report: repo URL, contents summary, the gate output, and the explicit remaining-before-charging list (webhook automation, freshness CI, R2 rotation, LS re-publish).

---

## Self-Review (run after the worker finishes Phase 6)

1. **Spec coverage** — §7.1 scrub✓(0.2) §7.2 repo+kill-Gitea✓(0.1) §7.4 dashboards✓(P3) §7.5 prompts+seed build artifacts✓(P1,P2) §7.6 book✓(P5) §7.7 price+SKU✓(P5.1,P5.2) | §7.3 webhook + §7.8 freshness = explicitly out-of-scope (noted).
2. **No operator PII/secret survives grep** — enforced by `verify_deliverable.py` (6.1) + per-phase scrub gates.
3. **No `$9`/`$89`/`$39`** — Phase 5 banned-token gate + whole-repo gate.
4. **Book SQL runs on fresh DB; no deleted-code** — 5.3 Step 1+2.
5. **Build-artifact + (snapshot+scripts)** — `build_seed.py`/`build_prompts.py` committed alongside outputs.

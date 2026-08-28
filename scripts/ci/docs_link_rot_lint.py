#!/usr/bin/env python3
"""CI lint: the docs site has no dangling page references or dead internal links.

Replaces coverage that was silently lost. ``Mintlify Validation (gladlabs) -
link-rot`` sits in Glad-Labs/poindexter's ``Main`` ruleset as a REQUIRED status
check, but it has never produced a check run -- 0 on every recent commit and
every recent PR, while ``Mintlify Deployment`` from the same app reports fine.
So the docs were not being link-checked despite the required-check list saying
they were, and a human PR against that repo would have hung forever waiting on
a check that never arrives. Found by the poindexter#1029 follow-on audit
(2026-08-28).

Two classes of rot, both of which ship silently today:

1. **Dangling navigation entry.** Every page listed in ``docs.json``'s
   navigation must exist on disk. A renamed or deleted doc leaves a nav entry
   pointing at nothing, which Mintlify renders as a 404 in the sidebar.

2. **Dead internal link.** Every relative Markdown link between docs files must
   resolve. This is the ``docs/architecture/foo.md`` -> renamed-to-``bar.md``
   case that only shows up when a reader clicks it.

Deliberately NOT checked: external ``http(s)://`` links. Network-dependent
checks make CI flaky and turn an unrelated site's downtime into a red build --
the runtime ``CheckPublishedLinksJob`` already watches outbound links on
PUBLISHED POSTS, which is where a dead external link actually costs a reader.

Run:

    python scripts/ci/docs_link_rot_lint.py      # exit 0 = clean, 1 = rot found

This lint carries its own scan floor (``lib_scan_floor``): a docs tree that has
moved must fail, not report clean over nothing. See the scan-floor principle in
CLAUDE.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_JSON = REPO_ROOT / "docs.json"
DOCS_DIR = REPO_ROOT / "docs"

# Mintlify resolves a bare nav entry to any of these on disk.
PAGE_SUFFIXES = (".mdx", ".md")

# [text](target) — the target group stops at whitespace or the closing paren so
# a title like [x](a.md "T") still yields "a.md".
MD_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)")

# Link targets we never resolve on disk.
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "//")

# A target carrying any of these is a PLACEHOLDER in prose, not a path:
# `[link](/go/<code>)`, `[x]({url})`, `[y](...)`. Resolving them produces pure
# noise -- the docs are full of illustrative examples.
PLACEHOLDER_CHARS = ("<", ">", "{", "}", "$", "*")

FENCE = re.compile(r"^\s*(```|~~~)")

# Inline code spans. `[x](url)` inside backticks is documentation ABOUT link
# syntax, not a link -- anti-hallucination.md and troubleshooting.md both
# discuss the `[Title](url)` shape in prose, and matching inside the ticks made
# this lint report its own examples as rot.
INLINE_CODE = re.compile(r"`[^`]*`")


def _iter_nav_pages(node: object) -> list[str]:
    """Collect every string under any ``pages`` key, at any nesting depth."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pages" and isinstance(value, list):
                found.extend(v for v in value if isinstance(v, str))
                found.extend(_iter_nav_pages(v) for v in value if not isinstance(v, str))  # type: ignore[misc]
            else:
                found.extend(_iter_nav_pages(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_iter_nav_pages(item))
    return [f for f in found if isinstance(f, str)]


def _page_exists(rel: str) -> bool:
    """True when a Mintlify page reference resolves to a file on disk."""
    candidate = REPO_ROOT / rel
    if candidate.is_file():
        return True
    return any((REPO_ROOT / f"{rel}{suffix}").is_file() for suffix in PAGE_SUFFIXES)


def check_navigation() -> tuple[list[str], int]:
    """Every docs.json navigation page must exist. Returns (problems, checked)."""
    if not DOCS_JSON.is_file():
        return ([f"docs.json not found at {DOCS_JSON}"], 0)

    try:
        config = json.loads(DOCS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ([f"docs.json is not valid JSON: {exc}"], 0)

    pages = _iter_nav_pages(config.get("navigation", {}))
    problems = [
        f"  docs.json -> navigation entry {page!r} resolves to no file "
        f"(looked for {page}, {page}.mdx, {page}.md)"
        for page in pages
        if not _page_exists(page)
    ]
    return (problems, len(pages))


def _resolve_link(source: Path, target: str) -> Path | None:
    """Resolve a relative link from ``source``; None when it is out of scope."""
    if target.startswith(SKIP_PREFIXES):
        return None
    if any(c in target for c in PLACEHOLDER_CHARS) or set(target) <= {"."}:
        return None
    # Root-relative links address the DEPLOYED site, not the repo -- e.g.
    # `/go/mercury` is the affiliate redirect route. Mintlify resolves those
    # itself, and its nav check (above) already covers page existence.
    if target.startswith("/"):
        return None
    # Drop any #anchor / ?query tail — we verify the file, not the heading.
    cleaned = target.split("#", 1)[0].split("?", 1)[0]
    if not cleaned:
        return None
    return (source.parent / cleaned).resolve()


def check_internal_links() -> tuple[list[str], int]:
    """Every relative link inside docs/ must resolve. Returns (problems, scanned)."""
    problems: list[str] = []
    scanned = 0
    if not DOCS_DIR.is_dir():
        return (problems, 0)

    for doc in sorted(DOCS_DIR.rglob("*")):
        if not doc.is_file() or doc.suffix not in PAGE_SUFFIXES:
            continue
        scanned += 1
        text = doc.read_text(encoding="utf-8", errors="replace")
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            # A ``` block is sample code, not navigation. Matching inside one
            # produced almost all of this lint's first-run noise.
            if FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for target in MD_LINK.findall(INLINE_CODE.sub("", line)):
                resolved = _resolve_link(doc, target)
                if resolved is None:
                    continue
                if resolved.exists():
                    continue
                # A link may omit the extension the way nav entries do.
                if any(resolved.with_suffix(suffix).is_file() for suffix in PAGE_SUFFIXES):
                    continue
                rel = doc.relative_to(REPO_ROOT).as_posix()
                problems.append(f"  {rel}:{lineno} -> {target!r} does not exist")
    return (problems, scanned)


def main() -> int:
    nav_problems, nav_checked = check_navigation()
    link_problems, docs_scanned = check_internal_links()

    if nav_problems:
        print("DOCS NAVIGATION POINTS AT MISSING PAGES:")
        print("\n".join(nav_problems))
    if link_problems:
        print("DEAD INTERNAL LINKS IN docs/:")
        print("\n".join(link_problems))

    if nav_problems or link_problems:
        total = len(nav_problems) + len(link_problems)
        print(
            f"\n{total} link-rot problem(s). Mintlify renders each of these as a "
            "404 for a reader who clicks it. Fix the path, or delete the "
            "reference if the target is genuinely gone.",
            file=sys.stderr,
        )
        return 1

    # Floor: a docs tree that moved must fail, not pass over nothing.
    require_scanned(nav_checked, lint="docs_link_rot_lint", what="nav pages", roots=(DOCS_JSON,))
    require_scanned(docs_scanned, lint="docs_link_rot_lint", what="docs files", roots=(DOCS_DIR,))

    print(
        f"docs_link_rot_lint: clean — {nav_checked} navigation page(s) resolve, "
        f"{docs_scanned} docs file(s) scanned, 0 dead internal links."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

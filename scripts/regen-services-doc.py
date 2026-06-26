#!/usr/bin/env python3
"""Regenerate docs/reference/services.md from the service + content trees.

Run from anywhere:  python scripts/regen-services-doc.py

The doc is a PURE DETERMINISTIC FUNCTION of tracked source — no date stamp, no
line counts, no DB. Two sources of truth:

  1. ``git ls-files`` over ``services/`` + ``modules/content/``  → the catalog
  2. ``services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF`` → the
     pipeline node list (loaded by file path so it pulls zero LangGraph deps).

CI (``.github/workflows/regen-services-doc.yml``) regenerates and
``git diff --exit-code``s on every PR touching those trees, so the committed
doc can never drift. To change the doc, change this script (it owns the whole
file) or the docstrings/spec it reads. The doc is in ``.prettierignore`` so the
pre-commit formatter doesn't fight the generator.

Ships to the public Poindexter mirror (``docs/reference/`` ships). Both scanned
trees are public and service docstrings describe code, not operator PII, so no
redaction tier is needed (unlike regen-app-settings-doc.py).
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import Counter, OrderedDict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PREFIX = "src/cofounder_agent/"
_TREES = ("src/cofounder_agent/services/", "src/cofounder_agent/modules/content/")
_EXCLUDE_DIRS = ("services/migrations/", "modules/content/migrations/")
_SPEC_PATH = _REPO / "src" / "cofounder_agent" / "services" / "canonical_blog_spec.py"

EM_DASH = "—"
_MAX_SUMMARY = 140


def _escape_pipes(s: str) -> str:
    return s.replace("|", r"\|")


def _truncate(s: str, n: int = _MAX_SUMMARY) -> str:
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


def first_docstring_line(source: str) -> str:
    """First non-empty line of the module docstring, collapsed + pipe-escaped.

    Returns ``EM_DASH`` when the file has no module docstring or won't parse.
    """
    try:
        doc = ast.get_docstring(ast.parse(source))
    except (SyntaxError, ValueError):
        doc = None
    if not doc:
        return EM_DASH
    for raw in doc.splitlines():
        line = " ".join(raw.split())  # collapse + strip internal whitespace
        if line:
            return _escape_pipes(_truncate(line))
    return EM_DASH


def package_label(after: str) -> str:
    """Package heading for a path with the ``src/cofounder_agent/`` prefix stripped."""
    parts = after.split("/")
    if after.startswith("services/") and len(parts) == 2:
        return "services/ (top-level)"
    return "/".join(parts[:-1]) + "/"


def _anchor(label: str) -> str:
    """Approximate GitHub heading slug for a package label."""
    s = re.sub(r"[^a-z0-9 -]", "", label.lower())
    return re.sub(r"\s+", "-", s.strip())


def discover_service_files() -> list[str]:
    """Tracked ``.py`` under the two trees, minus migrations. Fail loud if empty."""
    res = subprocess.run(
        ["git", "ls-files", "--", *_TREES],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    files: list[str] = []
    for rel in res.stdout.splitlines():
        rel = rel.strip()
        if not rel.endswith(".py"):
            continue
        after = rel[len(_PREFIX):] if rel.startswith(_PREFIX) else rel
        if after.startswith(_EXCLUDE_DIRS):
            continue
        files.append(rel)
    if not files:
        raise SystemExit(
            "regen-services-doc: git ls-files returned no .py files under the "
            "service trees — refusing to write an empty doc."
        )
    return sorted(files)


def gather_entries() -> list[tuple[str, str]]:
    """``(path-after-prefix, summary)`` for each catalogued file (IO: reads files)."""
    entries: list[tuple[str, str]] = []
    for rel in discover_service_files():
        after = rel[len(_PREFIX):]
        src = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
        summary = first_docstring_line(src)
        if Path(after).name == "__init__.py" and summary == EM_DASH:
            continue  # skip stub package __init__.py (keeps substantive ones)
        entries.append((after, summary))
    return entries


def _group_sort_key(item: tuple[str, list]) -> tuple[int, int, str]:
    label = item[0]
    tree = 0 if label.startswith("services/") else 1
    toplevel = 0 if label == "services/ (top-level)" else 1
    return (tree, toplevel, label)


def group_by_package(
    entries: list[tuple[str, str]],
) -> OrderedDict[str, list[tuple[str, str]]]:
    """Bucket entries by package label; services first, files sorted within."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for after, summary in entries:
        groups.setdefault(package_label(after), []).append((Path(after).name, summary))
    for items in groups.values():
        items.sort(key=lambda t: t[0])
    return OrderedDict(sorted(groups.items(), key=_group_sort_key))


def _load_graph_def() -> dict:
    """Load CANONICAL_BLOG_GRAPH_DEF by file path (pure data, zero deps)."""
    spec = spec_from_file_location("canonical_blog_spec", _SPEC_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CANONICAL_BLOG_GRAPH_DEF


def pipeline_node_order(graph_def: dict) -> list[tuple[str, str]]:
    """``(node_id, atom)`` in execution order by walking edges entry -> END.

    canonical_blog is a linear backbone with one bounded QA rescue cycle (a
    ``branch``-flagged conditional edge qa_aggregate -> qa_rewrite + a
    ``loop``-flagged back-edge qa_rewrite -> qa_programmatic). Those two flagged
    edges are skipped when building the forward chain so the walk stays linear;
    the branch target (the rescue node) is spliced in right after its source so
    the rendered order still shows where it sits. Raises on an UNflagged cycle
    so a malformed spec fails loud instead of looping.
    """
    by_id = {n["id"]: n["atom"] for n in graph_def["nodes"]}
    # Primary forward chain: skip the rescue cycle's branch + loop edges.
    nxt: dict[str, str] = {}
    for e in graph_def["edges"]:
        if e.get("loop") or e.get("branch"):
            continue
        nxt.setdefault(e["from"], e["to"])
    # Branch targets (rescue nodes) keyed by their source, to splice in-place.
    branch_targets = {
        e["from"]: e["to"] for e in graph_def["edges"] if e.get("branch")
    }
    order: list[tuple[str, str]] = []
    cur: str | None = graph_def["entry"]
    seen: set[str] = set()
    while cur and cur != "END":
        if cur in seen:
            raise ValueError(f"cycle detected at node {cur!r}")
        seen.add(cur)
        order.append((cur, by_id[cur]))
        # Splice the rescue/branch target right after its source node so the
        # rendered chain shows qa_rewrite between qa_aggregate and the default
        # forward target. The rescue node has no forward edge of its own (only
        # the loop back-edge), so the walk continues down the primary chain.
        bt = branch_targets.get(cur)
        if bt and bt not in seen:
            seen.add(bt)
            order.append((bt, by_id[bt]))
        cur = nxt.get(cur)
    return order


def render_pipeline_section(graph_def: dict) -> list[str]:
    order = pipeline_node_order(graph_def)
    counts = Counter(atom.split(".", 1)[0] + ".*" for _id, atom in order)
    comp_parts = [
        f"{counts['stage.*']} `stage.*`",
        f"{counts['content.*']} `content.*`",
        f"{counts['qa.*']} `qa.*`",
        f"{counts['seo.*']} `seo.*`",
        f"{counts['atoms.*']} `atoms.approval_gate`",
    ]
    # Append any additional prefixes introduced by new atom families
    _known = {"stage.*", "content.*", "qa.*", "seo.*", "atoms.*"}
    for prefix, n in sorted(counts.items()):
        if prefix not in _known:
            comp_parts.append(f"{n} `{prefix}`")
    comp = " + ".join(comp_parts)
    out = [
        f"## Content pipeline (`canonical_blog` graph_def) — {len(order)} nodes",
        "",
        (
            "Rendered in execution order from "
            "`services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF` "
            f"({comp}). `stage.*` atoms live in `modules/content/stages/`; "
            "`content.*` / `qa.*` / `seo.*` / `social.*` and the approval gate in "
            "`modules/content/atoms/`."
        ),
        "",
    ]
    out += [f"{i}. `{nid}` → `{atom}`" for i, (nid, atom) in enumerate(order, 1)]
    out.append("")
    return out


# Files referenced in older docs / git history but no longer on disk under the
# scanned trees. Verified absent via `git ls-files` 2026-06-13. The catalog
# can't list what's absent, so this curated tail prevents wild-goose-chase
# greps. Add an entry when you delete/rename/consolidate a service.
_DELETED_SERVICES: list[dict[str, str]] = [
    {
        "names": "`task_executor.py`",
        "note": (
            "Legacy polling daemon. Deleted 2026-05-16 (Prefect Stage 4, "
            "Glad-Labs/poindexter#410). Replaced by `flows/content_generation.py`; "
            "helpers moved to `integrations/operator_notify.py`, "
            "`integrations/handlers/webhook_alertmanager.py`, and `auto_publish.py`."
        ),
    },
    {
        "names": "`model_router.py` / `usage_tracker.py` / `model_constants.py`",
        "note": (
            "Legacy LLM-router trio. Deleted 2026-05-08 (Phase 2). Replaced by "
            "`cost_lookup.py` + per-step `*_model` pins (the `cost_tier.*` "
            "resolver was removed in PR #1907)."
        ),
    },
    {
        "names": (
            "`workflow_executor.py` + `custom_workflows_service.py` + "
            "`template_execution_service.py` + `workflow_validator.py` + "
            "`phase_mapper.py` + `phase_registry.py` + "
            "`workflow_progress_service.py` + `phases/` + "
            "`schemas/custom_workflow_schemas.py` + `agents/`"
        ),
        "note": (
            "The workflow-executor chain. Deleted 2026-05-09 (~3,800 LOC). "
            "Replaced by `template_runner.py` (LangGraph TemplateRunner)."
        ),
    },
    {
        "names": "`experiment_service.py`",
        "note": (
            "A/B harness. Deleted 2026-05-10 (Glad-Labs/poindexter#202). "
            "Replaced by `langfuse_experiments.py` (Langfuse Datasets/Traces/Scores)."
        ),
    },
    {
        "names": "`plugins/stage_runner.py`",
        "note": (
            "Legacy chunked StageRunner. Deleted 2026-05-16 (Lane C Stage 4). "
            "`content_router_service.py` is now a thin TemplateRunner dispatcher."
        ),
    },
    {
        "names": (
            "`admin_database.py` / `content_database.py` / `embeddings_database.py` "
            "/ `tasks_database.py` / `users_database.py` / `writing_style_database.py`"
        ),
        "note": (
            "The six modular DB files. Consolidated into `database_service.py` "
            "(+ `database_mixin.py`); callers reach typed CRUD through the single "
            "coordinator."
        ),
    },
    {
        "names": (
            "`image_generation_config.py` / `image_generation_runner.py` / "
            "`image_prompt_builder.py` / `image_selection_service.py`"
        ),
        "note": (
            "Old image stack. Restructured into the `services/image_providers/` "
            "package (image_gen / pexels / flux_schnell / ai_generation) + "
            "`image_service.py` / `image_captioner.py` / `image_decision_agent.py` "
            "+ content image atoms/stages under `modules/content/`."
        ),
    },
    {
        "names": (
            "`notifications_service.py`, `paging_helpers.py`, `html_sanitizer.py`, "
            "`slugify_service.py`, `quality_checker.py`, `rag_embeddings_service.py`, "
            "`vector_similarity_search.py`, `media_script_generator.py`, "
            "`transcription_service.py`, `handle_task_status_change.py`, "
            "`stateless_decision_handler.py`, `idle_worker.py`"
        ),
        "note": (
            "Removed in the 2026-05/06 cleanup waves (no direct successor — folded "
            "into callers or made obsolete by the atom pipeline). `idle_worker.py` "
            "retired in #1171."
        ),
    },
]


def intro_lines(n_files: int, n_pkgs: int) -> list[str]:
    return [
        "# Poindexter Services Reference",
        "",
        "> **Auto-generated by `scripts/regen-services-doc.py` — do not edit by hand.**  ",
        "> Run `python scripts/regen-services-doc.py` to refresh. CI "
        "(`.github/workflows/regen-services-doc.yml`) fails any PR where this "
        "file is out of date with the `services/` + `modules/content/` trees.",
        ">",
        f"> {n_files} files across {n_pkgs} packages. Each summary is the file's "
        "module-docstring first line — to improve an entry, improve that file's "
        "docstring. `services/migrations/` is excluded (schema deltas; see "
        "[migrations.md](../operations/migrations.md)).",
        "",
        "A catalog of every service, atom, and stage in "
        "`src/cofounder_agent/services/` and `src/cofounder_agent/modules/content/`. "
        'Use it to find "what is responsible for X" without reading source. For '
        "the load-bearing subset on the critical execution path, see "
        '[CLAUDE.md\'s "Key services" table](../../CLAUDE.md).',
        "",
        "---",
        "",
    ]


def render_catalog(groups: OrderedDict[str, list[tuple[str, str]]]) -> list[str]:
    out = ["## Table of contents", ""]
    for label, items in groups.items():
        n = len(items)
        plural = "s" if n != 1 else ""
        out.append(f"- [`{label}`](#{_anchor(label)}) ({n} file{plural})")
    out.append("")
    for label, items in groups.items():
        out.append(f"## {label}")
        out.append("")
        out.append("| File | Summary |")
        out.append("| --- | --- |")
        for name, summary in items:
            out.append(f"| `{name}` | {summary} |")
        out.append("")
    return out


def render_deleted_section() -> list[str]:
    out = [
        "## What's NOT in this catalog (deleted / consolidated)",
        "",
        "Referenced in older docs or git history but no longer on disk under "
        "these trees — listed so a grep doesn't send you on a wild goose chase.",
        "",
    ]
    out += [f"- **{d['names']}** — {d['note']}" for d in _DELETED_SERVICES]
    out.append("")
    return out


def conventions_lines() -> list[str]:
    return [
        "## Conventions",
        "",
        "- **Config via DI.** Services don't import a module-level `site_config`; "
        "they receive `SiteConfig` through DI — `Depends(get_site_config_dependency)` "
        'in routes, a constructor kwarg in services, `context.get("site_config")` '
        "in pipeline atoms. See CLAUDE.md's Configuration section.",
        "- **Async-everywhere.** Blocking work runs in `asyncio.to_thread` or a "
        "worker queue; never block the event loop.",
        "- **Typed errors** via `error_handler.py` (the `AppError` hierarchy); "
        "routes translate them to HTTP status codes.",
        "- **Atoms don't import each other.** `stage.*` / `content.*` / `qa.*` / "
        "`seo.*` atoms are wired into the `canonical_blog` graph_def and "
        "communicate only through `PipelineState` channels, never direct imports.",
        "",
    ]


def build_document(entries: list[tuple[str, str]], graph_def: dict) -> str:
    """Assemble the full markdown document (pure — no IO)."""
    groups = group_by_package(entries)
    parts: list[str] = []
    parts += intro_lines(len(entries), len(groups))
    parts += render_catalog(groups)
    parts += ["---", ""]
    parts += render_pipeline_section(graph_def)
    parts += ["---", ""]
    parts += render_deleted_section()
    parts += ["---", ""]
    parts += conventions_lines()
    return "\n".join(parts).rstrip() + "\n"


def main() -> None:
    entries = gather_entries()
    graph_def = _load_graph_def()
    doc = build_document(entries, graph_def)
    target = _REPO / "docs" / "reference" / "services.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding="utf-8", newline="\n")
    n_pkgs = len(group_by_package(entries))
    print(f"Wrote {target}: {len(entries)} files across {n_pkgs} packages.")


if __name__ == "__main__":
    main()

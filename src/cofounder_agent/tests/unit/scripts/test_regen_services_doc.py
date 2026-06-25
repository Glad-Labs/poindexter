"""Unit tests for ``scripts/regen-services-doc.py``.

The generator is pure stdlib with no DB import, so (unlike the app-settings
generator test) the render logic is tested directly here — no Postgres, no
``brain.bootstrap``/asyncpg stub. The "generated == committed" invariant is
owned by ``.github/workflows/regen-services-doc.yml`` (git diff), mirroring how
app-settings lets its workflow own end-to-end verification.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
SCRIPT_PATH = REPO_ROOT / "scripts" / "regen-services-doc.py"


def _load_script_module() -> ModuleType:
    spec = spec_from_file_location("regen_services_doc", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load_script_module()


def test_first_docstring_line_basic(mod: ModuleType) -> None:
    assert mod.first_docstring_line('"""Hello world."""\n') == "Hello world."


def test_first_docstring_line_multiline_takes_first_nonempty(mod: ModuleType) -> None:
    src = '"""\n\nReal summary here.\nSecond line.\n"""\n'
    assert mod.first_docstring_line(src) == "Real summary here."


def test_first_docstring_line_missing_returns_em_dash(mod: ModuleType) -> None:
    assert mod.first_docstring_line("x = 1\n") == mod.EM_DASH


def test_first_docstring_line_syntax_error_returns_em_dash(mod: ModuleType) -> None:
    assert mod.first_docstring_line("def (:\n") == mod.EM_DASH


def test_first_docstring_line_escapes_pipe(mod: ModuleType) -> None:
    assert mod.first_docstring_line('"""a | b."""') == r"a \| b."


def test_first_docstring_line_collapses_whitespace(mod: ModuleType) -> None:
    assert mod.first_docstring_line('"""Foo    bar\tbaz."""') == "Foo bar baz."


def test_package_label_toplevel(mod: ModuleType) -> None:
    assert mod.package_label("services/foo.py") == "services/ (top-level)"


def test_package_label_subdir(mod: ModuleType) -> None:
    assert mod.package_label("modules/content/atoms/qa_x.py") == "modules/content/atoms/"


def test_package_label_services_subdir(mod: ModuleType) -> None:
    assert mod.package_label("services/llm_providers/dispatcher.py") == "services/llm_providers/"


def test_anchor_strips_punctuation(mod: ModuleType) -> None:
    assert mod._anchor("services/ (top-level)") == "services-top-level"
    assert mod._anchor("modules/content/atoms/") == "modulescontentatoms"


def test_group_by_package_orders_services_then_modules(mod: ModuleType) -> None:
    entries = [
        ("modules/content/atoms/a.py", "A"),
        ("services/z.py", "Z"),
        ("services/auth/x.py", "X"),
        ("services/a_top.py", "Top"),
    ]
    groups = mod.group_by_package(entries)
    labels = list(groups)
    # services/ (top-level) first, then services subdirs, then modules
    assert labels[0] == "services/ (top-level)"
    assert labels.index("services/auth/") < labels.index("modules/content/atoms/")


def test_group_by_package_sorts_files_within_group(mod: ModuleType) -> None:
    entries = [("services/zebra.py", "Z"), ("services/apple.py", "A")]
    groups = mod.group_by_package(entries)
    names = [name for name, _ in groups["services/ (top-level)"]]
    assert names == ["apple.py", "zebra.py"]


def test_pipeline_node_order_follows_edges(mod: ModuleType) -> None:
    gd = {
        "entry": "a",
        "nodes": [{"id": "a", "atom": "stage.a"}, {"id": "b", "atom": "qa.b"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
    }
    assert mod.pipeline_node_order(gd) == [("a", "stage.a"), ("b", "qa.b")]


def test_pipeline_node_order_detects_cycle(mod: ModuleType) -> None:
    gd = {
        "entry": "a",
        "nodes": [{"id": "a", "atom": "stage.a"}, {"id": "b", "atom": "qa.b"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    with pytest.raises(ValueError):
        mod.pipeline_node_order(gd)


def test_render_pipeline_section_matches_real_spec(mod: ModuleType) -> None:
    gd = mod._load_graph_def()
    lines = mod.render_pipeline_section(gd)
    body = "\n".join(lines)
    assert lines[0] == "## Content pipeline (`canonical_blog` graph_def) — 40 nodes"
    assert "11 `stage.*`" in body
    assert "12 `content.*`" in body
    assert "13 `qa.*`" in body
    assert "1 `seo.*`" in body
    assert "1 `social.*`" in body  # social.generate_drafts (PR #1938)
    # draft_gate + preview_gate (component-scoped regen gate, 2026-06-22)
    assert "2 `atoms.approval_gate`" in body
    # removed node (#730) must never reappear since we render the live spec
    assert "qa.guardrails" not in body
    # first + last nodes in execution order
    assert "1. `verify_task` → `stage.verify_task`" in body
    assert body.rstrip().endswith(
        "`evaluate_auto_publish` → `content.evaluate_auto_publish`"
    )


def test_render_catalog_table_shape(mod: ModuleType) -> None:
    from collections import OrderedDict

    groups = OrderedDict(
        [("services/ (top-level)", [("foo.py", "Does foo.")])]
    )
    lines = mod.render_catalog(groups)
    body = "\n".join(lines)
    assert "## Table of contents" in body
    assert "[`services/ (top-level)`](#services-top-level) (1 file)" in body
    assert "| File | Summary |" in body
    assert "| `foo.py` | Does foo. |" in body


def test_build_document_idempotent(mod: ModuleType) -> None:
    entries = [
        ("services/foo.py", "Does foo."),
        ("modules/content/atoms/qa_x.py", "qa.x rail."),
    ]
    gd = mod._load_graph_def()
    assert mod.build_document(entries, gd) == mod.build_document(entries, gd)


def test_build_document_has_all_sections(mod: ModuleType) -> None:
    entries = [("services/foo.py", "Does foo.")]
    gd = mod._load_graph_def()
    doc = mod.build_document(entries, gd)
    assert doc.startswith("# Poindexter Services Reference")
    assert "## Table of contents" in doc
    assert "## Content pipeline (`canonical_blog` graph_def) — 40 nodes" in doc
    assert "## What's NOT in this catalog" in doc
    assert "## Conventions" in doc
    assert doc.endswith("\n")

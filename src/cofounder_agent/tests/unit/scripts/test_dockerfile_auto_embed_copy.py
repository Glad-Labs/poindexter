"""Repo-contract test: ``scripts/Dockerfile.auto-embed`` COPYs every
top-level package ``plugins.registry.get_core_samples()`` needs.

``get_taps()`` (what ``scripts/auto-embed.py`` actually calls) is merged
with ``get_core_samples()``, which unconditionally imports *every*
``_SAMPLES`` entry — not just taps — so a missing top-level package
under any ``_SAMPLES`` module_path breaks the sidecar with a
``ModuleNotFoundError`` per entry on every run (poindexter#849: the
``modules.content.stages.*`` entries had no ``modules/`` COPY after the
2026-06-04 content-module migration).

``schemas/`` isn't a direct ``_SAMPLES`` root but is a transitive import
of ``modules.content.stages.generate_video_shot_list`` — pinned
explicitly rather than derived.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "scripts" / "Dockerfile.auto-embed").exists()
)
DOCKERFILE = REPO_ROOT / "scripts" / "Dockerfile.auto-embed"
REGISTRY_PATH = REPO_ROOT / "src" / "cofounder_agent" / "plugins" / "registry.py"


def _copied_top_level_packages() -> set[str]:
    """Parse ``COPY src/cofounder_agent/<pkg> /app/<pkg>`` lines."""
    packages: set[str] = set()
    prefix = "src/cofounder_agent/"
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("COPY "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        src = parts[1]
        if src.startswith(prefix):
            pkg = src[len(prefix):].split("/", 1)[0]
            packages.add(pkg)
    return packages


def _sample_module_roots() -> set[str]:
    """Extract the top-level package of every ``_SAMPLES`` module_path
    in ``get_core_samples()`` via AST (no import — some entries are
    deliberately unimportable outside the full worker image, which is
    exactly the condition this test exists to catch)."""
    tree = ast.parse(REGISTRY_PATH.read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "get_core_samples"
    )
    roots: set[str] = set()
    for node in ast.walk(fn):
        # `_SAMPLES: list[tuple[str, str, str]] = [...]` is an AnnAssign,
        # not a plain Assign.
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name
        ):
            target_name = node.targets[0].id
        else:
            continue
        if target_name != "_SAMPLES" or node.value is None:
            continue
        for tup in node.value.elts:
            module_path = tup.elts[1].value
            roots.add(module_path.split(".", 1)[0])
    assert roots, "could not find _SAMPLES in get_core_samples() — test is stale"
    return roots


def test_dockerfile_copies_every_core_sample_root():
    copied = _copied_top_level_packages()
    needed = _sample_module_roots()
    missing = needed - copied
    assert not missing, (
        f"scripts/Dockerfile.auto-embed is missing COPY for {sorted(missing)} "
        f"— plugins.registry.get_core_samples() imports every _SAMPLES entry "
        f"unconditionally (even for a get_taps()-only caller), so a missing "
        f"root here means a ModuleNotFoundError per entry on every run "
        f"(see poindexter#849)."
    )


def test_dockerfile_copies_schemas_for_video_shot_list_stages():
    # modules.content.stages.generate_video_shot_list / review_video_shot_list
    # import schemas.video_shot_list — a transitive dep _SAMPLES parsing
    # above doesn't see, so pin it explicitly.
    assert "schemas" in _copied_top_level_packages(), (
        "scripts/Dockerfile.auto-embed must COPY src/cofounder_agent/schemas — "
        "modules.content.stages.generate_video_shot_list imports "
        "schemas.video_shot_list (poindexter#849)."
    )

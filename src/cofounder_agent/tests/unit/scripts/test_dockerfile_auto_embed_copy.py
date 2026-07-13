"""Repo-contract test: ``scripts/Dockerfile.auto-embed`` COPYs every
top-level package ``plugins.registry.get_core_samples()`` needs, and
``pip install``s every third-party package the embed hot path imports.

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

poindexter#852: ``services/ollama_client.py`` — lazy-imported by
``OllamaNativeProvider._get_client()`` on the first real ``embed()``/
``complete()`` call, i.e. always reachable once any embed actually runs
— hard-imports ``aiolimiter`` and ``tenacity`` at module level. Neither
was in the sidecar's minimal pip list, so every embed call
``ModuleNotFoundError``'d the moment an earlier bug (poindexter#850)
stopped masking it. Derived generically (AST-parsed third-party imports
vs. the Dockerfile's pip list) rather than hardcoding these two names,
so the next always-reachable module with an unlisted dependency fails
this test too.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "scripts" / "Dockerfile.auto-embed").exists()
)
DOCKERFILE = REPO_ROOT / "scripts" / "Dockerfile.auto-embed"
REGISTRY_PATH = REPO_ROOT / "src" / "cofounder_agent" / "plugins" / "registry.py"
OLLAMA_CLIENT_PATH = REPO_ROOT / "src" / "cofounder_agent" / "services" / "ollama_client.py"

# First-party roots COPY'd into the image (or resolvable via sys.path
# tricks in auto-embed.py) — never pip packages.
_FIRST_PARTY_ROOTS = frozenset({
    "services", "plugins", "poindexter", "brain", "utils", "schemas", "modules",
})

# Import name -> pip distribution name, for the handful where they differ.
# Empty today (every embed-hot-path import happens to match its pip name)
# but kept so a future mismatch is a one-line fix, not a silent test gap.
_IMPORT_TO_PIP_NAME: dict[str, str] = {}


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
        if target_name != "_SAMPLES" or not isinstance(node.value, ast.List):
            continue
        for tup in node.value.elts:
            if not isinstance(tup, ast.Tuple):
                continue
            module_path_node = tup.elts[1]
            if not isinstance(module_path_node, ast.Constant):
                continue
            roots.add(str(module_path_node.value).split(".", 1)[0])
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


_PIP_COMMAND_WORDS = frozenset({"pip", "install", "&&"})


def _dockerfile_pip_packages() -> set[str]:
    """Parse the ``pip install --no-cache-dir \\`` continuation block into
    a lowercased package-name set. Ignores flags (``--no-cache-dir`` etc.),
    line-continuation backslashes, and the ``pip install``/``&&`` command
    words themselves (the block's first line is ``RUN pip install
    --no-cache-dir --upgrade pip && pip install --no-cache-dir \\``, which
    would otherwise tokenize a fake ``pip`` "package")."""
    packages: set[str] = set()
    in_pip_block = False
    for raw_line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("RUN pip install"):
            in_pip_block = True
        elif not in_pip_block:
            continue
        for tok in stripped.rstrip("\\").split():
            if tok.startswith("-") or tok in _PIP_COMMAND_WORDS:
                continue
            packages.add(tok.lower())
        if not stripped.endswith("\\"):
            in_pip_block = False
    return packages


def _third_party_import_roots(source_path: Path) -> set[str]:
    """AST-parse top-level ``import X`` / ``from X import ...`` roots in a
    module, excluding stdlib and first-party (``_FIRST_PARTY_ROOTS``)
    names. Only looks at module-level statements (not ones nested inside
    ``if``/``try``/functions) since those are frequently deliberate
    optional-dependency guards, not hard requirements."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    stdlib = sys.stdlib_module_names
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".", 1)[0])
    return {
        r for r in roots
        if r not in stdlib and r not in _FIRST_PARTY_ROOTS and r != "__future__"
    }


def test_dockerfile_installs_ollama_client_dependencies():
    # OllamaNativeProvider._get_client() lazy-imports services.ollama_client
    # on the first real embed()/complete() call — always reachable once any
    # embed actually runs (ollama_native is the sidecar's only viable
    # provider; litellm_provider needs `utils`, which the sidecar doesn't
    # ship). A hard top-level import here that isn't pip-installed
    # ModuleNotFoundErrors on every single embed call (poindexter#852:
    # aiolimiter + tenacity were missing, masked until #850 fixed the
    # earlier TypeError that prevented reaching this import).
    needed = {
        _IMPORT_TO_PIP_NAME.get(root, root)
        for root in _third_party_import_roots(OLLAMA_CLIENT_PATH)
    }
    installed = _dockerfile_pip_packages()
    missing = needed - installed
    assert not missing, (
        f"scripts/Dockerfile.auto-embed's pip install list is missing "
        f"{sorted(missing)} — services/ollama_client.py imports them at "
        f"module level and is always reachable from the embed hot path "
        f"(see poindexter#852)."
    )

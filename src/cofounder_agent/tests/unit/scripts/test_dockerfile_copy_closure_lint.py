"""dockerfile_copy_closure_lint — imported siblings must be COPY'd into the image."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
LINT = REPO_ROOT / "scripts" / "ci" / "dockerfile_copy_closure_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location("dockerfile_copy_closure_lint", LINT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules[cls.__module__]
    spec.loader.exec_module(mod)
    return mod


def _tree(root: Path, dockerfile: str, files: dict[str, str]) -> Path:
    (root / "scripts" / "side").mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    (root / "scripts" / "Dockerfile.side").write_text(dockerfile, encoding="utf-8")
    (root / "docker-compose.local.yml").write_text(
        "services:\n  side:\n    build:\n      context: ./scripts\n      dockerfile: Dockerfile.side\n",
        encoding="utf-8",
    )
    return root


def _offences(mod, root: Path) -> list[str]:
    out: list[str] = []
    for img in mod.collect_images(root):
        offs, _ = mod.check_image(img, root)
        out.extend(offs)
    return out


def test_missing_sibling_copy_is_an_offence(tmp_path):
    mod = _load()
    root = _tree(tmp_path, "FROM x\nCOPY side/server.py /app/server.py\n", {
        "scripts/side/server.py": "from _helper import f\n",
        "scripts/side/_helper.py": "def f(): pass\n",
    })
    offs = _offences(mod, root)
    assert len(offs) == 1 and "_helper" in offs[0] and "never COPYs it" in offs[0]


def test_glob_copy_satisfies_the_closure(tmp_path):
    mod = _load()
    root = _tree(tmp_path, "FROM x\nCOPY --chown=app side/*.py /app/\nCOPY side/server.py /app/server.py\n", {
        "scripts/side/server.py": "from _helper import f\nimport chunking\n",
        "scripts/side/_helper.py": "def f(): pass\n",
        "scripts/side/chunking.py": "X = 1\n",
    })
    assert _offences(mod, root) == []


def test_sibling_copied_to_another_directory_is_an_offence(tmp_path):
    mod = _load()
    root = _tree(tmp_path, "FROM x\nCOPY side/server.py /app/server.py\nCOPY side/_helper.py /lib/_helper.py\n", {
        "scripts/side/server.py": "import _helper\n",
        "scripts/side/_helper.py": "",
    })
    offs = _offences(mod, root)
    assert len(offs) == 1 and "not importable from there" in offs[0]


def test_sibling_package_and_relative_imports_are_resolved(tmp_path):
    mod = _load()
    root = _tree(tmp_path / "a", "FROM x\nCOPY side/server.py /app/server.py\n", {
        "scripts/side/server.py": "from .pkg import thing\nfrom pkg.sub import other\n",
        "scripts/side/pkg/__init__.py": "",
        "scripts/side/pkg/sub.py": "",
    })
    offs = _offences(mod, root)
    assert offs and all("pkg" in o for o in offs)
    root2 = _tree(tmp_path / "b", "FROM x\nCOPY side/server.py /app/server.py\nCOPY side/pkg /app/pkg\n", {
        "scripts/side/server.py": "from pkg.sub import other\n",
        "scripts/side/pkg/__init__.py": "",
        "scripts/side/pkg/sub.py": "",
    })
    assert _offences(mod, root2) == []


def test_escape_marker_and_stage_copies_are_ignored(tmp_path):
    mod = _load()
    root = _tree(tmp_path, "FROM x AS build\nCOPY --from=build /x /y\nCOPY [\"side/server.py\", \"/app/server.py\"]\n", {
        "scripts/side/server.py": "try:\n    import _optional  # image-copy-ok\nexcept ImportError:\n    _optional = None\n",
        "scripts/side/_optional.py": "",
    })
    assert _offences(mod, root) == []


def test_copy_parser_handles_flags_globs_and_continuations():
    mod = _load()
    steps = mod.parse_copy_steps(
        "COPY --chown=u:g --link a.py \\\n  b.py /app/\nCOPY --from=stage /x /y\nCOPY [\"c.py\", \"/app/c.py\"]\nRUN echo\n"
    )
    assert [s.sources for s in steps] == [["a.py", "b.py"], ["/x"], ["c.py"]]
    assert steps[0].dest == "/app/" and steps[1].from_stage == "stage" and steps[2].dest == "/app/c.py"


def test_lint_passes_on_the_real_tree():
    proc = subprocess.run([sys.executable, str(LINT)], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "copied python files across" in proc.stdout

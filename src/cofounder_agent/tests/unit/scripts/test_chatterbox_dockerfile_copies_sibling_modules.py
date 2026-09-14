"""Every sibling module chatterbox_server.py imports must be COPY'd into its image.

Earned 2026-09-13: #3730 added ``_voice_paths.py`` beside the server and the
server imported it, but ``scripts/Dockerfile.chatterbox`` copies files by name,
so the rebuilt sidecar died on ``ModuleNotFoundError`` 500+ times before anyone
looked. No CI job builds this image (it is a CUDA image), so the contract is
pinned here instead.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts" / "Dockerfile.chatterbox").is_file():
            return parent
    raise AssertionError("scripts/Dockerfile.chatterbox not found above the test file")


def _sibling_imports(server: Path) -> set[str]:
    tree = ast.parse(server.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if (server.parent / f"{n}.py").is_file()}


def test_every_sibling_import_is_copied_into_the_image():
    root = _repo_root()
    server = root / "scripts" / "tts_sidecars" / "chatterbox_server.py"
    dockerfile = (root / "scripts" / "Dockerfile.chatterbox").read_text(encoding="utf-8")
    copied = set(re.findall(r"^COPY\s+(?:--\S+\s+)*tts_sidecars/([\w.]+)\s+/app/", dockerfile, re.M))
    siblings = _sibling_imports(server)
    assert siblings, "the server imports no sibling modules — the scan found nothing, which is not a pass"
    missing = {f"{n}.py" for n in siblings} - copied
    assert not missing, f"chatterbox_server.py imports {sorted(missing)} but Dockerfile.chatterbox never COPYs them"

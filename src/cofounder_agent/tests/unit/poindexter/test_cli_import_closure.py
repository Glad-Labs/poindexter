"""The bare `pip install poindexter` path stays lean (Glad-Labs/poindexter#1046 step 6).

Importing ``poindexter.cli.app`` -- what ``poindexter --help`` does -- must not
import any package that lives in the ``pipeline`` / ``qa`` / ``rag`` extras.
The import runs in a FRESH interpreter so this test process's own ``sys.modules``
(the suite imports prefect and deepeval elsewhere) cannot mask a leak, and the
extras' membership is read from the manifest so the two cannot drift apart.

Why it matters: the epic's step 6 decided the CLI-only install should not need
prefect or deepeval. A new module-level ``import prefect`` in a module the CLI
touches would silently re-inflate the bare install by hundreds of megabytes
and break `pip install poindexter` users at import time. This fails first.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

BACKEND_DIR = Path(__file__).resolve().parents[3]
MANIFEST = BACKEND_DIR / "pyproject.toml"

# Distribution name -> import root (the ones that differ from the dist name).
_IMPORT_ROOT = {
    "llama-index-core": "llama_index",
    "llama-index-embeddings-ollama": "llama_index",
    "langchain-community": "langchain_community",
    "langchain-ollama": "langchain_ollama",
}
LEAN_EXTRAS = ("pipeline", "qa", "rag")


def _extra_import_roots() -> dict[str, set[str]]:
    with MANIFEST.open("rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    return {
        extra: {_IMPORT_ROOT.get(dist, dist.replace("-", "_")) for dist in extras[extra]}
        for extra in LEAN_EXTRAS
    }


def _cli_import_roots() -> set[str]:
    code = (
        "import json, sys\n"
        "from poindexter.cli.app import main  # noqa: F401\n"
        "print('ROOTS=' + json.dumps(sorted({m.split('.')[0] for m in sys.modules})))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND_DIR, env=env, timeout=300
    )
    assert proc.returncode == 0, f"importing poindexter.cli.app failed:\n{proc.stderr[-2000:]}"
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("ROOTS="))
    return set(json.loads(line[len("ROOTS="):]))


@pytest.mark.unit
def test_manifest_declares_the_lean_extras() -> None:
    roots = _extra_import_roots()
    assert roots["pipeline"] >= {"prefect", "playwright", "litellm"}
    assert roots["qa"] >= {"deepeval", "ragas"}
    assert roots["rag"] >= {"llama_index"}


@pytest.mark.unit
def test_cli_import_closure_excludes_the_lean_extras() -> None:
    imported = _cli_import_roots()
    assert "poindexter" in imported and "click" in imported  # the probe worked
    leaked = {extra: sorted(imported & names) for extra, names in _extra_import_roots().items() if imported & names}
    assert not leaked, (
        f"`poindexter --help` now imports packages from the lean extras: {leaked}. "
        "Either move the import inside the function that needs it, or move the "
        "package back into the main dependencies -- the bare `pip install poindexter` "
        "must not need prefect / deepeval / llama-index (poindexter#1046 step 6)."
    )

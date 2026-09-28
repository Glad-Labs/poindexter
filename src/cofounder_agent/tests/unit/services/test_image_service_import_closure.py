"""Importing image_service must not try to import torch or its ML neighbours.

``services/image_providers/_image_models.py`` used to probe torch, diffusers
and xformers at module level (``try: import torch`` plus two ``find_spec``
calls). Those probes served the worker's in-process diffusers path, which could
never run: the worker image installs no diffusers. Nothing read the names they
bound, but the torch import was real. The worker image carries CPU torch for
sentence-transformers, so every process that imported image_service paid for
it. Measured in the worker image on 2026-09-28, the import took 0.9 s and
peaked at 238 MB RSS with the probe, against 0.1 s and 38 MB without it. That
includes every Prefect flow-run subprocess, which imports image_service while
it bootstraps, whether or not the run has a task. The probe also logged
"Diffusers library not available" at WARNING on each import: 59 times in one
hour from the prefect-worker.

The import runs in a FRESH interpreter so this process's own ``sys.modules``
(the suite imports torch-adjacent packages elsewhere) cannot mask a leak. A
recorder on ``sys.meta_path`` sees import ATTEMPTS, so the check still means
something where torch is not installed: CI and the host venv, both of which
the old probe would have walked into all the same.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[3]

# Heavy ML packages with no business in image_service's import closure: the
# image-gen HTTP server (its own CUDA container) does all the rendering.
_FORBIDDEN = ("torch", "diffusers", "xformers", "transformers", "sentence_transformers")
# A package image_service really imports, so the recorder is proven to fire.
_POSITIVE_CONTROL = "httpx"


def _attempted_imports() -> set[str]:
    watched = [*_FORBIDDEN, _POSITIVE_CONTROL]
    code = (
        "import importlib.abc, json, sys\n"
        f"WATCHED = set({watched!r})\n"
        "seen = set()\n"
        "class _Recorder(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in WATCHED:\n"
        "            seen.add(name.split('.')[0])\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Recorder())\n"
        "import poindexter.services.image_service  # noqa: F401\n"
        "print('SEEN=' + json.dumps(sorted(seen)))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=BACKEND_DIR, env=env, timeout=300,
    )
    assert proc.returncode == 0, (
        f"importing poindexter.services.image_service failed:\n{proc.stderr[-2000:]}"
    )
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("SEEN="))
    return set(json.loads(line[len("SEEN="):]))


@pytest.mark.unit
def test_image_service_import_attempts_no_torch_or_diffusers() -> None:
    attempted = _attempted_imports()
    assert _POSITIVE_CONTROL in attempted, (
        f"the import recorder never saw {_POSITIVE_CONTROL!r}, which image_service "
        "imports at module level. The probe is blind, so its verdict means nothing."
    )
    leaked = sorted(attempted & set(_FORBIDDEN))
    assert not leaked, (
        f"importing image_service now tries to import {leaked}. The worker "
        "image carries CPU torch, so this costs every importing process "
        "(worker, each Prefect flow-run subprocess) ~0.8 s and ~200 MB. "
        "Rendering happens in the image-gen HTTP server; move the import inside "
        "the function that needs it, or out of the worker."
    )

"""``_seed_env_from_bootstrap`` must work on the unit's own sys.path.

``poindexter-mcp-http.service`` runs ``.venv/bin/python http_server.py`` from
the deploy clone's mcp-server/; the ``poindexter`` package is NOT installed in
that venv -- the server puts ``src/cofounder_agent`` on ``sys.path`` itself.
``main()`` calls ``_seed_env_from_bootstrap()`` first, so that function has to
do the path bootstrap before it imports ``poindexter.brain.bootstrap``. Step 3
of poindexter#1046 changed the import from the flat ``brain.bootstrap`` (which
had resolved through a repo-root package) to the canonical spelling and the
unit crash-looped for 27 hours with ``No module named 'poindexter'``.

The test runs the function in a FRESH interpreter whose ``sys.path`` carries no
entry containing ``cofounder_agent`` (the poetry env's editable ``.pth`` would
otherwise mask the bug), with a temporary HOME holding a bootstrap.toml.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parents[1]


def test_seed_env_from_bootstrap_bootstraps_its_own_import_path(tmp_path):
    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    (home / ".poindexter" / "bootstrap.toml").write_text(
        'poindexter_secret_key = "unit-test-key"\n', encoding="utf-8"
    )
    code = (
        "import os, sys\n"
        # The unit's reality: mcp-server/ is sys.path[0]; the worker tree is not on the path.
        "sys.path[:] = [p for p in sys.path if 'cofounder_agent' not in p]\n"
        f"sys.path.insert(0, {str(MCP_DIR)!r})\n"
        "import http_server\n"
        "http_server._seed_env_from_bootstrap()\n"
        "print('SECRET=' + os.environ.get('POINDEXTER_SECRET_KEY', ''))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in {"POINDEXTER_SECRET_KEY", "PYTHONPATH"}}
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env, cwd=MCP_DIR
    )
    assert proc.returncode == 0, f"_seed_env_from_bootstrap failed on the unit's sys.path:\n{proc.stderr[-1500:]}"
    assert "SECRET=unit-test-key" in proc.stdout

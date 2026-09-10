"""Reserved PyPI name for the Poindexter CLI -- this is a placeholder.

Poindexter (https://github.com/Glad-Labs/poindexter) is a self-hosted AI
content pipeline. Its ``poindexter`` command ships WITH the stack, not as a
standalone wheel yet -- the CLI imports the backend's service layer, which the
backend's current import layout cannot publish to PyPI without shadowing real
packages named ``services``, ``utils`` and ``config``. The namespace migration
that fixes that is tracked in the epic linked from this project's README.

Until then, ``pip install poindexter`` gets you this module: it reserves the
name, and its ``poindexter`` command prints the real install path and exits
non-zero so nothing downstream mistakes it for the tool.
"""

from __future__ import annotations

import sys

__version__ = "0.0.1"

REPO = "https://github.com/Glad-Labs/poindexter"
DOCS = "https://gladlabs.mintlify.app"

_MESSAGE = f"""\
poindexter {__version__} -- placeholder, not the CLI.

  The Poindexter CLI is installed as part of the stack, not from PyPI (yet):

    git clone {REPO}
    cd poindexter
    docker compose -f docker-compose.consumer.yml up -d

  Docs: {DOCS}
  Why:  {REPO}/blob/main/packaging/pypi-placeholder/README.md
"""


def main(argv: list[str] | None = None) -> int:
    """Print the real install path. Exit 0 only for --help / --version.

    Everything else exits 1: a script that ran ``poindexter tasks list``
    against this placeholder must not be told it succeeded.
    """
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("--version", "-V"):
        print(f"poindexter {__version__} (placeholder)")
        return 0
    if args and args[0] in ("--help", "-h"):
        print(_MESSAGE)
        return 0
    print(_MESSAGE, file=sys.stderr)
    return 1

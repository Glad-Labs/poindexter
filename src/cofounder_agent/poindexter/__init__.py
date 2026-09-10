"""poindexter — shared public package for the open-source AI content pipeline.

This is the top-level namespace for code that ships with the public
`Glad-Labs/poindexter` release. Everything under `poindexter.*` is public;
private operator code stays under `gladlabs.*`.

Current contents:
- `poindexter.memory` — shared pgvector memory client (MemoryClient)

Filesystem location caveat: this package currently lives under
`src/cofounder_agent/poindexter/` because the worker's docker build-context
is `src/cofounder_agent`. The eventual layout (per Gitea #192) is
`src/poindexter/` at the repo root. The import namespace is stable either
way — callers always write `from poindexter.memory import MemoryClient`.
"""

# Step 2 of Glad-Labs/poindexter#1046: the backend's packages live under this
# root now (``poindexter.services``, ``poindexter.plugins``, ...). The legacy flat
# spellings (``import services.x``) resolve to the SAME module objects through the
# alias finder below -- installed here so the wheel and ``python -m poindexter`` get
# it the moment the root is imported, and again by each flat stub so a process whose
# first import is flat is covered. Removed in step 5 once no flat spelling remains.
from . import _flat_imports as _flat_imports  # noqa: E402

_flat_imports.install()

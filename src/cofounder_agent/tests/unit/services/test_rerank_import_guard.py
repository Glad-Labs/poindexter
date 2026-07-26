"""Real-import regression guard for the sentence-transformers ``rerank`` extra.

Why this file exists
--------------------
``services/rag_engine.py::CrossEncoderRerankRetriever`` imports the *real*
``sentence_transformers`` at runtime; when that import fails the reranker
silently degrades to passthrough — and ``rag_rerank_enabled=true`` on prod.
sentence-transformers is an opt-in ``rerank`` extra (2026-06-23 CI torch-trim),
so the default unit-tests install (``poetry install --no-root``, no extras)
never imports it, and the two adjacent rerank/dedup suites *stub* it
(``test_rag_engine.py`` patches ``_get_model``; ``test_topic_dedup_semantic.py``
injects a fake ``sentence_transformers`` into ``sys.modules``). That left the
real transitive ML stack — transformers / huggingface-hub / tokenizers —
unexercised by any test: a version-skew re-lock could ship a set whose import
blows up (e.g. an old transformers whose ``huggingface-hub<1.0`` cap rejects a
locked hub 1.x), degrading prod with nothing to catch it at PR time.

Design note — NOT ``pytest.importorskip``
-----------------------------------------
The regression we guard against surfaces as an ``ImportError``, and
``importorskip`` treats *any* ``ImportError`` as "not installed" and SKIPS —
which would swallow the exact failure. Instead we gate on whether the
*distribution metadata* is present: the ``.dist-info`` exists whenever the
package is installed, even when importing it would raise on a transitive
conflict. That lets us distinguish "extra genuinely absent" (skip) from
"extra installed but its import is broken" (hard FAIL). So the guard runs and
fails loud wherever ``--extras rerank`` is installed — host dev (bootstrap.sh),
the worker image build (mirror of ``src/cofounder_agent/Dockerfile:73``), and
the ``.github/workflows/rerank-import-guard.yml`` CI job on dependency changes.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution

import pytest


def _rerank_extra_installed() -> bool:
    """True when the ``sentence-transformers`` distribution is installed.

    Reads distribution metadata (the ``.dist-info`` dir), NOT a live import —
    the dist-info exists even when importing the package would raise because of
    a transitive version conflict. That distinction is the whole point: it lets
    the guard skip when the extra is absent but FAIL when it is present-but-broken.
    """
    try:
        distribution("sentence-transformers")
    except PackageNotFoundError:
        return False
    return True


@pytest.mark.rerank
@pytest.mark.skipif(
    not _rerank_extra_installed(),
    reason=(
        "`rerank` extra not installed — guard runs where `--extras rerank` is "
        "present (host dev, worker image, rerank-import-guard CI job)"
    ),
)
def test_rerank_stack_imports_cleanly() -> None:
    """The real sentence-transformers stack must import without conflict.

    A hard import (deliberately not ``importorskip``) so a huggingface-hub /
    transformers version skew fails LOUD. Mirrors the runtime import in
    ``services/rag_engine.py`` and the build-time assertion in
    ``src/cofounder_agent/Dockerfile``.
    """
    import huggingface_hub  # noqa: F401
    import sentence_transformers  # noqa: F401
    import tokenizers  # noqa: F401
    import transformers  # noqa: F401

    # CrossEncoder is exactly what the rag_engine reranker constructs. Assert it
    # resolved to a class so a partial/shimmed import can't pass vacuously.
    from sentence_transformers import CrossEncoder

    assert isinstance(CrossEncoder, type)

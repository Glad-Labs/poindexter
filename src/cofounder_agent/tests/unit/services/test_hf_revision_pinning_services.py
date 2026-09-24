"""Every sentence-transformers load under poindexter/ pins a revision (poindexter#879).

Bandit's B615 knows the transformers + huggingface_hub APIs and is blind to
``sentence_transformers.CrossEncoder`` / ``SentenceTransformer``, so a green
bandit run said nothing about the two live loads (the RAG reranker and topic
dedup), both of which resolved to whatever the repo's ``main`` was on the day of
a cold start. The model name is DB-driven, so the pin is too — a
``<model_key>_revision`` setting — and this test checks the call passes it.
A deliberate exception carries ``# hf-revision:`` on the call line.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from poindexter.services.settings_defaults import DEFAULTS

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3] / "poindexter"
_LOADERS = {"CrossEncoder", "SentenceTransformer"}


def _loads():
    for path in _ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if not any(name in text for name in _LOADERS):
            continue
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name in _LOADERS:
                    yield path, node, lines[node.lineno - 1]


def test_the_scan_finds_the_loads():
    assert sum(1 for _ in _loads()) >= 3, "HF load scan went blind"


def test_every_load_pins_a_revision_or_says_why_not():
    unpinned = [
        f"{p.relative_to(_ROOT)}:{n.lineno}"
        for p, n, line in _loads()
        if not any(k.arg == "revision" for k in n.keywords) and "# hf-revision:" not in line
    ]
    assert not unpinned, f"unpinned Hugging Face loads: {unpinned}"


@pytest.mark.parametrize(
    "key", ["rag_rerank_model_revision", "topic_dedup_embedding_model_revision"],
)
def test_the_seeded_pins_are_full_commit_shas(key):
    sha = DEFAULTS[key]
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_the_reranker_passes_its_pin_and_blank_tracks_main(monkeypatch):
    import sys
    import types

    from poindexter.services import rag_engine
    from poindexter.services.site_config import SiteConfig

    seen = []
    fake = types.ModuleType("sentence_transformers")
    fake.CrossEncoder = lambda name, **kw: seen.append((name, kw)) or object()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.setattr(rag_engine, "_RERANKER_CACHE", {})
    cls = rag_engine._build_rerank_retriever_class()
    for rev, want in (("abc123", "abc123"), ("", None)):
        r = cls(inner=None, top_k=5, site_config=SiteConfig(initial_config={
            "rag_rerank_model": "m", "rag_rerank_model_revision": rev,
        }))
        r._get_model()
        assert seen[-1][1]["revision"] == want


def test_topic_dedup_passes_its_pin(monkeypatch):
    import sys
    import types

    from poindexter.services import topic_dedup_semantic as tds

    seen = []
    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = lambda name, **kw: seen.append(kw) or object()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.setattr(tds, "_model_cache", {})
    tds._get_model("m", "cpu", "abc123")
    assert seen == [{"device": "cpu", "revision": "abc123"}]

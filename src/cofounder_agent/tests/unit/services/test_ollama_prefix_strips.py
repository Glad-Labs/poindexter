"""Every `.removeprefix("ollama/")` under poindexter/ says why it is there
(poindexter#1030).

Model pins are stored either as `ollama/gemma3:27b` or as bare `gemma3:27b`.
For a name headed to `dispatch_complete`, stripping the prefix is a no-op
round trip: `resolve_model_name` puts it straight back. Most of the 33 strips
this issue inventoried were that, copied from one call site to the next (29
became 33 in a month). But a strip is load-bearing wherever a bare name is
actually required, and removing one of those is a hard 404 that the startup
model validator will not catch:

- a direct Ollama consumer: `ollama_chat_text`'s httpx fallback,
  `ChatOllama`, `OllamaClient`, the `ollama_native` provider's no-pool path;
- an equality check or dedup key, where `ollama/X` and `X` must compare equal;
- a recorded identity (a review's `model` field), where changing the spelling
  splits every stored series;
- a value the code re-prefixes itself (`f"ollama/{name}"`).

The inventory proved how easy these are to misjudge: `title_generation`
looked dispatcher-only and has a no-pool `ollama_native` path, and
`social_poster` and `ragas_eval`, listed in the issue as vestigial, both hand
the name to a direct client. So each surviving strip carries a
`# bare-model:` note naming its reason, and this test fails on a new one that
doesn't — the same shape as `# hf-revision:` in
test_hf_revision_pinning_services.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3] / "poindexter"
_STRIP = 'removeprefix("ollama/")'
_NOTE = "# bare-model:"


def _strip_sites() -> tuple[list[tuple[str, int]], int]:
    """((relative path, 1-based line) per strip, number of files scanned)."""
    sites: list[tuple[str, int]] = []
    scanned = 0
    for path in sorted(_ROOT.rglob("*.py")):
        scanned += 1
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if _STRIP in line:
                sites.append((str(path.relative_to(_ROOT)), i + 1))
    return sites, scanned


def test_the_scan_sees_the_tree():
    """A scan of an empty or moved root would pass the check below vacuously.
    llm_text's resolver strips are load-bearing by design (ollama_chat_text's
    httpx fallback needs a bare name), so at least those must be found."""
    sites, scanned = _strip_sites()
    assert scanned >= 100, f"only {scanned} files under {_ROOT} — scan root moved?"
    assert any(p == "services/llm_text.py" for p, _ in sites), (
        "llm_text.py's resolver strips are gone or unseen — the scan went blind"
    )


def test_every_strip_says_why():
    unannotated = []
    for rel, ln in _strip_sites()[0]:
        lines = (_ROOT / rel).read_text(encoding="utf-8").splitlines()
        here, above = lines[ln - 1], lines[ln - 2] if ln > 1 else ""
        if _NOTE not in here and not above.strip().startswith(_NOTE):
            unannotated.append(f"{rel}:{ln}")
    assert not unannotated, (
        f"`{_STRIP}` without a `{_NOTE}` reason on the line or the line above. "
        "If the name only reaches dispatch_complete or ollama_chat_text, delete "
        "the strip — both handle the prefix themselves. Otherwise say which "
        "direct consumer, comparison or recorded identity needs it bare:\n  "
        + "\n  ".join(unannotated)
    )


# --- the two facts that make the removed strips no-ops ----------------------


def test_the_dispatcher_resolves_both_spellings_the_same():
    from poindexter.services.llm_providers.litellm_provider import resolve_model_name

    assert resolve_model_name("gemma3:27b") == "ollama/gemma3:27b"
    assert resolve_model_name("ollama/gemma3:27b") == "ollama/gemma3:27b"


class _Cfg:
    def __init__(self, **values: Any) -> None:
        self._v = {"local_llm_api_url": "http://localhost:11434", **values}

    def get(self, key: str, default: Any = None) -> Any:
        return self._v.get(key, default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(default)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(default)


async def test_ollama_chat_text_strips_the_prefix_before_its_direct_fallback():
    """Why callers of ollama_chat_text need no strip of their own: it resolves
    the name through resolve_writer_model, which strips, before the httpx POST
    that would 404 on `ollama/…`."""
    from poindexter.services.llm_text import ollama_chat_text

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"message": {"content": "ok"}})
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=client):
        await ollama_chat_text("hi", model="ollama/gemma3:27b", site_config=_Cfg())

    assert client.post.await_args[1]["json"]["model"] == "gemma3:27b"


# --- removed strips: these now hand the pin on as configured -----------------


def test_person_mention_rail_hands_its_pin_on_verbatim():
    from poindexter.modules.content.atoms import qa_person_mention

    cfg = _Cfg(qa_person_mention_model="ollama/judge:1b")
    assert qa_person_mention._resolve_model(cfg) == "ollama/judge:1b"


async def test_rag_writer_resolver_hands_its_pin_on_verbatim():
    from poindexter.modules.content.ai_content_generator import _resolve_rag_writer_model

    cfg = _Cfg(pipeline_writer_model="ollama/writer:7b")
    assert await _resolve_rag_writer_model(site_config=cfg) == "ollama/writer:7b"


async def test_retention_summary_resolver_hands_its_pin_on_verbatim():
    from poindexter.services.integrations.handlers import retention_summarize_to_table as rst

    async def _setting(_pool: Any, key: str, default: str) -> str:
        return "ollama/phi4:14b" if key == "memory_compression_summary_model" else default

    with patch.object(rst, "_get_setting", _setting):
        assert await rst._resolve_summary_model(object()) == "ollama/phi4:14b"

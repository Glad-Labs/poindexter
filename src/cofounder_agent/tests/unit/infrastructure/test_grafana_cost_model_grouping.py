"""Every per-model rollup over ``cost_logs`` must normalize the provider prefix.

Dashboards: ``infrastructure/grafana/dashboards/*.json``

WHY THIS TEST EXISTS (stack#3343 follow-up)

``cost_logs.model`` has carried the same local engine under up to three
spellings — bare ``gemma-4-31B-it-qat:latest``, ``ollama/…`` and
``ollama_chat/…`` — because 29 call sites strip the ``ollama/`` prefix off
their configured ``*_model`` value before dispatching and the LiteLLM router
silently re-adds it. Any ``GROUP BY`` on the raw column therefore splits ONE
model into phantom series.

The 2026-08-27 export of the Cost & Analytics "By Model" table showed the
symptom plainly: ``gemma-4-31B-it-qat:latest`` 27,257 calls in one row and
``ollama/gemma-4-31B-it-qat:latest`` 637 in another, with ``phi4:14b`` and
``qwen3-vl:30b`` split the same way. stack#3340 fixed the four Model
Throughput panels; the five older ones (By Model, Calls by Model, Tokens by
Model, Token Usage by Day, Quality by Model) were missed and kept lying for
two months.

The dispatcher now writes the resolved name (``_cost_log_model``), but that
only helps rows written from here on — every panel still has to normalize to
read the HISTORY correctly. So this guard is not redundant with the write-side
fix: it is what keeps a newly-added panel from reintroducing the split.

``ollama_chat/`` collapses into the same bucket ON READ (it is the same
engine, and these are cost/volume rollups) while staying distinct in the
stored column, because it is genuinely a different endpoint. Cloud prefixes
(``anthropic/``, ``gemini/``, ``openai/``) are real provenance and are
deliberately NOT stripped by the regex.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
DASHBOARDS_DIR = REPO_ROOT / "infrastructure" / "grafana" / "dashboards"

# The one canonical spelling. Shared verbatim with services/llm_throughput.py
# so a panel and the API that mirrors it can never bucket differently.
NORMALIZER = re.compile(
    r"regexp_replace\(\s*[\w.]*model\s*,\s*'\^ollama\(_chat\)\?/'\s*,\s*''\s*\)"
    # ...including the output alias, so `AS model` is not re-read as a raw ref.
    r"(?:\s+(?:as|AS)\s+(?:\"[^\"]+\"|\w+))?"
)

# Occurrences that reference the column without bucketing on it: an equality
# filter can compare the raw value safely ('system' has no prefix to strip).
_FILTER_COMPARISON = re.compile(r"[\w.]*\bmodel\s*(?:!=|=|<>)\s*'[^']*'")
_BARE_MODEL = re.compile(r"(?<![\w.])(?:\w+\.)?model(?![\w(])")

# Legitimate exceptions, keyed by (dashboard stem, panel title) so a rename
# drops the exemption rather than silently widening it. Empty on purpose —
# add an entry only with a comment saying why bucketing raw is correct there.
ALLOWED_RAW_MODEL: set[tuple[str, str]] = set()


def _walk(panels):
    for panel in panels:
        yield panel
        yield from _walk(panel.get("panels", []) or [])


def _model_rollups() -> list[tuple[str, str, str]]:
    """(dashboard stem, panel title, sql) for every cost_logs per-model query."""
    found = []
    for path in sorted(DASHBOARDS_DIR.glob("*.json")):
        dashboard = json.loads(path.read_text())
        for panel in _walk(dashboard.get("panels", []) or []):
            for target in panel.get("targets", []) or []:
                sql = target.get("rawSql") or ""
                if "cost_logs" not in sql or "GROUP BY" not in sql.upper():
                    continue
                if not _BARE_MODEL.search(sql) and not NORMALIZER.search(sql):
                    continue  # panel groups cost_logs by something else
                found.append((path.stem, panel.get("title") or "<untitled>", sql))
    return found


def test_dashboards_dir_is_discoverable():
    assert DASHBOARDS_DIR.is_dir(), DASHBOARDS_DIR
    assert _model_rollups(), "found no cost_logs per-model panels — check the walk"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stem", "title", "sql"),
    [pytest.param(*r, id=f"{r[0]}::{r[1]}") for r in _model_rollups()],
)
def test_per_model_rollup_normalizes_the_ollama_prefix(stem: str, title: str, sql: str):
    if (stem, title) in ALLOWED_RAW_MODEL:
        pytest.skip("explicitly allowed to bucket the raw column")

    # Remove the references that are already correct or don't bucket, then
    # anything still naming the column is a raw GROUP BY key.
    residue = NORMALIZER.sub("<normalized>", sql)
    residue = _FILTER_COMPARISON.sub("<filter>", residue)
    leftover = _BARE_MODEL.findall(residue)

    assert not leftover, (
        f"{stem} panel {title!r} buckets cost_logs on the RAW model column, so "
        f"one engine splits across its bare / ollama/ / ollama_chat/ spellings. "
        f"Wrap it: regexp_replace(model, '^ollama(_chat)?/', ''). SQL: {sql}"
    )

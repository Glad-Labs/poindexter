# GSC Query-Dimension Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. NOTE: this repo's CLAUDE.md disables subagent delegation (metered-API billing) — do NOT use superpowers:subagent-driven-development here; execute inline in the same session instead.

**Goal:** Land the `query` search dimension into `external_metrics` (today only page-level GSC data exists) and add a `GscQueryGapSource` TopicSource that turns high-impression/poor-position queries into new topic proposals — closing out Glad-Labs/poindexter#764, the last open piece of epic #762.

**Architecture:** This is a **tap-config + one generic-code extension**, not a new ingestion system. The installed `tap-google-search-console` package already ships a `performance_report_custom` stream whose dimension set (`page`, `query`, `country`, `device`) is driven entirely by Singer catalog **field-level** selection — but our `tap_singer_subprocess.py` only auto-selects whole **streams**, never individual fields, so `performance_report_custom` would request zero dimensions today. Add field-level catalog selection, then point the live `gsc_main` tap row's `performance_report_custom` stream at `page`+`query` via that new `field_selection` config key. `tap_external_metrics_writer.py` needs **no changes** — it already bundles arbitrary `dimension_fields` into the `dimensions` jsonb and dedups on `(source, metric_name, date, slug, dimensions)`, so per-(page, query) rows fall out for free. `GscQueryGapSource` is a new, independent `TopicSource` (same Protocol as `devto.py`/`hackernews.py`) that reads the new per-query data and surfaces gaps as `DiscoveredTopic`s for the existing topic-proposal pipeline.

**Tech Stack:** Python 3.13, asyncpg, pytest + pytest-asyncio, the existing Singer-tap subprocess handler, the `TopicSource` plugin protocol.

## Global Constraints

- **No new `app_settings` seeds required.** `seo.query_ingestion.enabled` (default `'false'`) is already seeded (`settings_defaults.py:2170`). Per-source tuning (`min_impressions`, `min_position`, `window_days`, `max_topics`) follows the established `TopicSource` convention (devto.py, hackernews.py) of **Python-side defaults only** — no other `plugin.topic_source.*` source has a seeded config row (`PluginConfig.load` returns `enabled=True, config={}` when the row is missing).
- **`tap_external_metrics_writer.py` is NOT modified.** It is already fully generic over `dimension_fields`; touching it risks the GA4/GSC dedup fix documented in its docstring.
- **The live `gsc_main` `external_taps` row is never edited directly by me via SQL.** It carries real OAuth credentials and is not seeded anywhere (confirmed: absent from `0000_baseline.seeds.sql`, hand-provisioned). Ship an idempotent one-off script instead; applying it against prod is a separate, explicit step (mirrors how `seo.refresh.enabled=true` was flipped only after explicit sign-off in the Milestone B rollout).
- **Ships inert.** `seo.query_ingestion.enabled` stays `false` until the operator flips it — same "earn autonomy" posture as the rest of the harvest loop.
- Follow `feedback_docs_and_tests_default`: every task ships tests + the doc update lands in the same PR as the code it describes.

---

## File Structure

| File                                                                          | Responsibility                                                                                                                                                                                                                                          |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/cofounder_agent/services/integrations/handlers/tap_singer_subprocess.py` | **Modify.** Add `_apply_field_selection()` (pure, testable) + thread a `field_selection` config key through `_generate_selected_catalog()` and `singer_subprocess()`.                                                                                   |
| `src/cofounder_agent/tests/unit/services/test_tap_singer_subprocess.py`       | **Modify.** Unit tests for `_apply_field_selection()` (no subprocess — fast, runs on Windows) + one extended e2e test.                                                                                                                                  |
| `src/cofounder_agent/tests/integration_db/test_external_metrics_dedup.py`     | **Modify.** One new test proving a `query`-bearing `dimensions` payload dedups/stays-distinct exactly like the existing `search_type` case.                                                                                                             |
| `scripts/enable_gsc_query_dimension.py`                                       | **Create.** One-off, idempotent activation script — adds the `performance_report_custom` stream + its `metrics_mapping` + `field_selection` to the live `gsc_main` row. Not run by the plan itself; documented as the operator's final activation step. |
| `src/cofounder_agent/services/topic_sources/gsc_query_gap.py`                 | **Create.** `GscQueryGapSource` — reads query-gap candidates from `external_metrics`, yields `DiscoveredTopic`s.                                                                                                                                        |
| `src/cofounder_agent/tests/unit/services/topic_sources/test_gsc_query_gap.py` | **Create.** Unit tests for the new source (fake pool, no real DB).                                                                                                                                                                                      |
| `src/cofounder_agent/pyproject.toml`                                          | **Modify.** Register `gsc_query_gap` under the existing `[project.entry-points."poindexter.topic_sources"]` table (confirmed at lines 426-433 — NOT the repo-root `pyproject.toml`).                                                                    |
| `src/cofounder_agent/plugins/registry.py`                                     | **Modify.** Add `("topic_sources", "services.topic_sources.gsc_query_gap", "GscQueryGapSource")` to `_SAMPLES` (entry_points alone can go stale post-install — see `reference_stale_entry_point_metadata`).                                             |
| `docs/architecture/seo-harvest-loop.md`                                       | **Modify.** New `## Query-dimension ingestion (#764)` section; update the `## Status` "Next" bullet.                                                                                                                                                    |

---

## Task 1: Field-level catalog selection in the Singer subprocess handler

**Files:**

- Modify: `src/cofounder_agent/services/integrations/handlers/tap_singer_subprocess.py`
- Test: `src/cofounder_agent/tests/unit/services/test_tap_singer_subprocess.py`

**Interfaces:**

- Produces: `_apply_field_selection(catalog: dict[str, Any], field_selection: dict[str, list[str]]) -> None` (mutates `catalog` in place; safe no-op when `field_selection` is falsy or a stream/field isn't present).
- Modifies: `_generate_selected_catalog(command, config_path, selected_streams, output_path, field_selection=None)` — new trailing optional param.
- Modifies: `singer_subprocess()` — reads `config.get("field_selection")`, validates it's a dict (or falsy), passes it through.

**Why this is the right seam (ground truth, not guesswork):** the installed `tap_google_search_console` package's `performance_report_custom` stream (`streams/performance_reports.py`) has `body_params = {"aggregationType": "auto"}` with **no fixed `dimensions` list** — unlike every other stream. `streams/abstract.py::make_payload` fills it in per-request: `self.body_params["dimensions"] = self.set_dimensions_in_payload(stream_metadata)`, and `set_dimensions_in_payload` walks `self.dimension_list = ["date", "country", "device", "page", "query"]` calling `should_sync_field(inclusion, selected)` — standard Singer field metadata (`{"breadcrumb": ["properties", "<field>"], "metadata": {"selected": True}}`) with `selected_by_default=False`. Our own `_generate_selected_catalog` only ever writes the **stream-level** (`breadcrumb: []`) `selected` flag, so today `performance_report_custom` would request an empty `dimensions` list — nothing GSC-side would change even if the row's `streams` config listed it. This is a tap-config seam, not a new ingestion system — exactly what poindexter#764 itself concluded should be verified before building.

- [ ] **Step 1: Write the failing test for `_apply_field_selection`**

Add to `src/cofounder_agent/tests/unit/services/test_tap_singer_subprocess.py`, in a new section after the existing `TestExternalMetricsWriter` class (before the `TestSingerSubprocessEndToEnd` section):

```python
# ---------------------------------------------------------------------------
# _apply_field_selection unit tests (pure function, no subprocess)
# ---------------------------------------------------------------------------


class TestApplyFieldSelection:
    def test_marks_named_fields_selected_on_matching_stream(self):
        catalog = {
            "streams": [
                {
                    "tap_stream_id": "performance_report_custom",
                    "metadata": [{"breadcrumb": [], "metadata": {"selected": True}}],
                }
            ]
        }
        tap_singer_subprocess._apply_field_selection(
            catalog, {"performance_report_custom": ["page", "query"]}
        )
        stream = catalog["streams"][0]
        by_breadcrumb = {tuple(m["breadcrumb"]): m["metadata"] for m in stream["metadata"]}
        assert by_breadcrumb[("properties", "page")]["selected"] is True
        assert by_breadcrumb[("properties", "query")]["selected"] is True
        # Stream-level entry untouched.
        assert by_breadcrumb[()]["selected"] is True

    def test_ignores_stream_not_present_in_catalog(self):
        catalog = {"streams": [{"tap_stream_id": "performance_report_page", "metadata": []}]}
        # Should not raise even though "performance_report_custom" isn't in this catalog.
        tap_singer_subprocess._apply_field_selection(
            catalog, {"performance_report_custom": ["page", "query"]}
        )
        assert catalog["streams"][0]["metadata"] == []

    def test_noop_on_empty_field_selection(self):
        catalog = {"streams": [{"tap_stream_id": "x", "metadata": [{"breadcrumb": [], "metadata": {}}]}]}
        before = json.loads(json.dumps(catalog))
        tap_singer_subprocess._apply_field_selection(catalog, {})
        assert catalog == before

    def test_creates_metadata_list_when_stream_has_none(self):
        catalog = {"streams": [{"tap_stream_id": "performance_report_custom"}]}
        tap_singer_subprocess._apply_field_selection(
            catalog, {"performance_report_custom": ["query"]}
        )
        entries = catalog["streams"][0]["metadata"]
        assert {"breadcrumb": ["properties", "query"], "metadata": {"selected": True}} in entries
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the main checkout's poetry venv — a fresh worktree has no venv of its own; see `reference_run_worktree_tests`):

```bash
cd src/cofounder_agent && "$(python -c 'import sys; print(sys.executable)' 2>/dev/null || echo python)" -m pytest tests/unit/services/test_tap_singer_subprocess.py::TestApplyFieldSelection -v -o addopts=""
```

If the main checkout's venv python is at a known path, invoke it directly instead, e.g. `C:/Users/mattm/glad-labs-website/src/cofounder_agent/.venv/Scripts/python.exe` — resolve the exact path with `poetry env info --path` from the **main checkout** (not the worktree) if unsure.

Expected: FAIL — `AttributeError: module 'tap_singer_subprocess' has no attribute '_apply_field_selection'`.

- [ ] **Step 3: Implement `_apply_field_selection` and wire it through**

In `tap_singer_subprocess.py`, add the new function right after `_generate_selected_catalog` (or before it — either is fine, but keep both catalog-mutation helpers adjacent):

```python
def _apply_field_selection(
    catalog: dict[str, Any], field_selection: dict[str, list[str]]
) -> None:
    """Mark specific schema fields as selected, in place.

    Complements the whole-stream selection in :func:`_generate_selected_catalog`.
    Some taps only include a dimension in their upstream API request when the
    catalog marks that individual field selected — whole-stream selection
    alone leaves such fields unrequested. Standard Singer field metadata is a
    ``{"breadcrumb": ["properties", "<field>"], "metadata": {"selected": True}}``
    entry in the stream's flat ``metadata`` list (verified against the
    installed ``tap-google-search-console``'s ``performance_report_custom``
    stream, whose ``dimensions`` request payload is built from exactly this
    field-selection state — see ``streams/abstract.py::set_dimensions_in_payload``).

    ``field_selection`` maps ``tap_stream_id`` to the schema property names to
    mark selected. Streams or fields absent from the fetched catalog are
    silently skipped (tap version drift shouldn't crash a sync).
    """
    if not field_selection:
        return
    for stream in catalog.get("streams", []):
        stream_id = stream.get("tap_stream_id")
        fields = field_selection.get(stream_id)
        if not fields:
            continue
        md_list = stream.setdefault("metadata", [])
        for field_name in fields:
            breadcrumb = ["properties", field_name]
            entry = next(
                (m for m in md_list if m.get("breadcrumb") == breadcrumb),
                None,
            )
            if entry is None:
                entry = {"breadcrumb": breadcrumb, "metadata": {}}
                md_list.append(entry)
            entry["metadata"]["selected"] = True
```

Modify `_generate_selected_catalog`'s signature and its tail (find the existing function; add the parameter and one call before the file write):

```python
async def _generate_selected_catalog(
    command: str,
    config_path: str,
    selected_streams: list[str],
    output_path: str,
    field_selection: dict[str, list[str]] | None = None,
) -> None:
```

...and just before the existing `with open(output_path, "w", encoding="utf-8") as fh:` line at the end of that function, add:

```python
    _apply_field_selection(catalog, field_selection or {})
```

Modify `singer_subprocess()` to read and validate the new config key, then pass it through. Find:

```python
    tap_config = config.get("tap_config") or {}
    streams_filter = config.get("streams") or []
    if streams_filter and not isinstance(streams_filter, list):
        raise ValueError("tap.singer_subprocess: row.config.streams must be a list")
```

Add immediately after:

```python
    field_selection = config.get("field_selection") or {}
    if field_selection and not isinstance(field_selection, dict):
        raise ValueError("tap.singer_subprocess: row.config.field_selection must be an object")
```

Find the existing catalog-generation call:

```python
        catalog_path: str | None = None
        if streams_filter:
            catalog_path = os.path.join(tmpdir, "catalog.json")
            await _generate_selected_catalog(
                command, config_path, streams_filter, catalog_path,
            )
```

Replace with:

```python
        catalog_path: str | None = None
        if streams_filter:
            catalog_path = os.path.join(tmpdir, "catalog.json")
            await _generate_selected_catalog(
                command, config_path, streams_filter, catalog_path, field_selection,
            )
```

Finally, update the module docstring's "Row config example" (near the top of the file) to document the new key — append a `field_selection` line to the existing example block so an operator reading the docstring sees it:

```
    {
      "command": "tap-csv",
      "tap_config": {"files": [{"entity": "page_views", "path": "/data/page_views.csv"}]},
      "streams": ["page_views"],
      "field_selection": {"page_views": ["country"]},
      "max_records": 100000,
      "timeout_seconds": 1800
    }
```

Add one sentence under the existing numbered "How it works" list's item 5 (SCHEMA/RECORD/STATE description), after the whitelist-filtering note, explaining: `row.config.field_selection` (optional, `{tap_stream_id: [field, ...]}`) additionally marks individual schema fields selected in the generated catalog — needed for taps whose dimension/field request is driven by catalog field-selection rather than being fixed per stream.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/cofounder_agent && python -m pytest tests/unit/services/test_tap_singer_subprocess.py::TestApplyFieldSelection -v -o addopts=""
```

Expected: 4 passed.

- [ ] **Step 5: Extend the existing end-to-end test to cover `field_selection` flowing through `--discover`**

In the same test file, in `TestSingerSubprocessEndToEnd`, add a new test method after `test_dispatches_records_through_handler`:

```python
    @pytest.mark.asyncio
    async def test_field_selection_reaches_generated_catalog(self, tmp_path):
        """The --catalog file the tap actually receives carries the
        field-level selected=True entries, not just the stream-level one."""
        lines = [
            json.dumps({"type": "SCHEMA", "stream": "custom_metrics", "schema": {"properties": {}}}),
            json.dumps({"type": "RECORD", "stream": "custom_metrics", "record": {
                "date": "2026-04-24", "impressions": 10, "query": "docker compose",
            }}),
        ]
        # This fake tap ignores --catalog content but we intercept it by
        # having the script dump whatever catalog.json it was given to a
        # sentinel file, so the test can assert on it directly.
        sentinel = tmp_path / "seen_catalog.json"
        script_body = f'''
import json
import sys
if "--discover" in sys.argv:
    sys.stdout.write(json.dumps({{"streams": [{{"tap_stream_id": "custom_metrics", "stream": "custom_metrics", "schema": {{"properties": {{}}}}, "metadata": []}}]}}))
    sys.stdout.flush()
    sys.exit(0)
if "--catalog" in sys.argv:
    catalog_path = sys.argv[sys.argv.index("--catalog") + 1]
    with open(catalog_path, encoding="utf-8") as f:
        data = f.read()
    with open({str(sentinel)!r}, "w", encoding="utf-8") as f:
        f.write(data)
for line in {lines!r}:
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()
sys.exit(0)
'''
        script_path = tmp_path / "fake_custom_tap.py"
        script_path.write_text(script_body, encoding="utf-8")
        command = f'"{sys.executable}" "{script_path}"'

        pool = _FakePool()
        row = _row(
            config={
                "command": command,
                "streams": ["custom_metrics"],
                "field_selection": {"custom_metrics": ["page", "query"]},
                "metrics_mapping": {
                    "custom_metrics": {
                        "source": "google_search_console",
                        "date_field": "date",
                        "metric_fields": ["impressions"],
                        "dimension_fields": ["query"],
                    }
                },
                "max_records": 10,
                "timeout_seconds": 30,
            },
        )
        await tap_singer_subprocess.singer_subprocess(
            None, site_config=None, row=row, pool=pool,
        )

        seen_catalog = json.loads(sentinel.read_text(encoding="utf-8"))
        stream = seen_catalog["streams"][0]
        by_breadcrumb = {tuple(m["breadcrumb"]): m["metadata"] for m in stream["metadata"]}
        assert by_breadcrumb[("properties", "page")]["selected"] is True
        assert by_breadcrumb[("properties", "query")]["selected"] is True
```

- [ ] **Step 6: Run the full test file**

```bash
cd src/cofounder_agent && python -m pytest tests/unit/services/test_tap_singer_subprocess.py -v -o addopts=""
```

Expected: all pass (the two subprocess e2e tests only run when `PYTEST_RUN_SUBPROCESS_TESTS=1` is set on Windows — set it for this run to confirm both new and old e2e tests pass: `PYTEST_RUN_SUBPROCESS_TESTS=1 python -m pytest tests/unit/services/test_tap_singer_subprocess.py -v -o addopts=""`).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/integrations/handlers/tap_singer_subprocess.py src/cofounder_agent/tests/unit/services/test_tap_singer_subprocess.py
git commit -m "feat(taps): support field-level catalog selection in the Singer subprocess handler"
```

---

## Task 2: Dedup coverage for the `query` dimension

**Files:**

- Modify: `src/cofounder_agent/tests/integration_db/test_external_metrics_dedup.py`

**Interfaces:**

- Consumes: `external_metrics_writer(payload, *, site_config, row, pool)` (unchanged signature, `services/integrations/handlers/tap_external_metrics_writer.py`).
- Produces: nothing new — this task is pure test coverage proving the existing generic dimension-dedup mechanism (`test_distinct_dimensions_stay_separate` already covers `search_type`) also holds for `query`, satisfying poindexter#764 acceptance criterion 2 ("row growth is bounded").

- [ ] **Step 1: Write the new test**

Add to `src/cofounder_agent/tests/integration_db/test_external_metrics_dedup.py`, after `test_distinct_dimensions_stay_separate`:

```python
async def test_distinct_query_dimension_stays_separate(test_pool):
    # Same page, same date, two different search queries: two GSC rows.
    # A re-emit of one of them must dedup onto itself, not create a third row.
    source = "test_dedup_query"
    row = _row(source, dimension_fields=["query"])
    try:
        await external_metrics_writer(
            _payload(slug="my-post", clicks=1.0, dims={"query": "docker compose tutorial"}),
            site_config=None, row=row, pool=test_pool,
        )
        await external_metrics_writer(
            _payload(slug="my-post", clicks=2.0, dims={"query": "docker compose vs kubernetes"}),
            site_config=None, row=row, pool=test_pool,
        )
        await external_metrics_writer(
            _payload(slug="my-post", clicks=1.0, dims={"query": "docker compose tutorial"}),
            site_config=None, row=row, pool=test_pool,
        )
        assert await _count(test_pool, source) == 2
    finally:
        await _cleanup(test_pool, source)
```

- [ ] **Step 2: Run it against the real test DB**

```bash
cd src/cofounder_agent && python -m pytest tests/integration_db/test_external_metrics_dedup.py -v -o addopts=""
```

Expected: all pass, including the new test (5 total in this file → 6).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/integration_db/test_external_metrics_dedup.py
git commit -m "test(taps): cover query-dimension dedup in external_metrics"
```

---

## Task 3: One-off activation script for the live `gsc_main` tap row

**Files:**

- Create: `scripts/enable_gsc_query_dimension.py`

**Interfaces:**

- Consumes: `services.declarative_config_service.get_row(pool, surface, key)` / `upsert_row(pool, surface, payload)` (surface `"taps"`, key `"gsc_main"` — both already registered in `_SURFACES`).
- Produces: nothing consumed elsewhere — this is an operator-run one-off, not imported by other code.

- [ ] **Step 1: Write the script**

```python
"""One-off: add the performance_report_custom (page+query) stream to the
gsc_main tap row so GSC query-dimension data starts landing in
external_metrics.

Part of Glad-Labs/poindexter#764. Idempotent — safe to re-run (it computes
the target config and upserts the full row each time). Does NOT flip
``seo.query_ingestion.enabled`` — that master switch is a separate, explicit
step once the ingested data has been spot-checked.

Run once, against prod, after this change is deployed:

    poetry run python scripts/enable_gsc_query_dimension.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "cofounder_agent"))

from services import declarative_config_service as dcs  # noqa: E402

_SURFACE = "taps"
_TAP_NAME = "gsc_main"
_STREAM = "performance_report_custom"


def _resolve_db_url() -> str:
    """Mirrors scripts/check_taps.py — bootstrap.toml is canonical (#198),
    force IPv4 so Windows doesn't resolve localhost to the IPv6 proxy."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        try:
            from brain.bootstrap import resolve_database_url  # type: ignore

            dsn = resolve_database_url()
        except Exception as exc:  # bootstrap is best-effort on the host
            print(f"[dsn] bootstrap resolution failed ({exc}); using default", file=sys.stderr)
            dsn = None
    if not dsn:
        dsn = "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
    return dsn.replace("@localhost:", "@127.0.0.1:")


async def main() -> None:
    pool = await asyncpg.create_pool(_resolve_db_url())
    try:
        row = await dcs.get_row(pool, _SURFACE, _TAP_NAME)
        if row is None:
            raise SystemExit(f"no tap named {_TAP_NAME!r} — nothing to update")

        config = dict(row.get("config") or {})

        streams = list(config.get("streams") or [])
        if _STREAM not in streams:
            streams.append(_STREAM)
        config["streams"] = streams

        mappings = dict(config.get("metrics_mapping") or {})
        mappings[_STREAM] = {
            "source": "google_search_console",
            "date_field": "date",
            "post_field": "page",
            "metric_fields": ["impressions", "clicks", "ctr", "position"],
            "dimension_fields": ["query", "site_url", "search_type"],
        }
        config["metrics_mapping"] = mappings

        field_selection = dict(config.get("field_selection") or {})
        field_selection[_STREAM] = ["page", "query"]
        config["field_selection"] = field_selection

        updated = await dcs.upsert_row(pool, _SURFACE, {**row, "config": config})
        print(f"{_TAP_NAME}: streams={updated['config']['streams']}")
        print(f"{_TAP_NAME}: field_selection={updated['config']['field_selection']}")
        print(
            "Next: spot-check one tap run (`poindexter taps run gsc_main`), "
            "confirm external_metrics rows with dimensions ? 'query' look sane, "
            "then set seo.query_ingestion.enabled=true when ready."
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke-test against a local/throwaway DB (not prod)**

If a local Postgres with the `external_taps` table and a `gsc_main`-named row is available, run:

```bash
DATABASE_URL="<local-throwaway-dsn>" python scripts/enable_gsc_query_dimension.py
```

Expected: prints the updated `streams` and `field_selection`, no traceback. If no local row named `gsc_main` exists, this step is skipped — the script's correctness is already covered by Task 1's unit tests on `_apply_field_selection` plus `declarative_config_service`'s own existing test coverage; do not fabricate a throwaway DB just to exercise this script.

- [ ] **Step 3: Commit**

```bash
git add scripts/enable_gsc_query_dimension.py
git commit -m "chore(seo): add one-off activation script for GSC query-dimension ingestion"
```

**Do not run this script against the live production DSN as part of this plan.** Applying it is the operator's explicit "flip it live" step (same posture as `seo.refresh.enabled` in Milestone B) — report it as ready in the final summary instead.

---

## Task 4: `GscQueryGapSource` TopicSource

**Files:**

- Create: `src/cofounder_agent/services/topic_sources/gsc_query_gap.py`
- Test: `src/cofounder_agent/tests/unit/services/topic_sources/test_gsc_query_gap.py`

**Interfaces:**

- Consumes: `plugins.topic_source.DiscoveredTopic` (fields: `title: str`, `category: str`, `source: str`, `source_url: str = ""`, `relevance_score: float = 0.0`, `description: str = ""`, `keywords: list[str] = []`); `services.topic_sources._filters.classify_category(title: str) -> str`.
- Produces: `class GscQueryGapSource` with `name = "gsc_query_gap"` and `async def extract(self, pool: Any, config: dict[str, Any]) -> list[DiscoveredTopic]` — the shape `topic_sources/runner.py` calls generically on every registered source.

**Design (grounded in the actual ingested shape from Task 1-3):** `external_metrics` rows for the new stream carry `source='google_search_console'`, `slug`/`post_id` set from the `page` field (via `post_field: "page"`, same URL-extraction path the existing `performance_report_page` stream already uses), and `dimensions->>'query'` holding the search term. A "query gap" is a query with real search visibility (meaningful impressions) but a poor average position — i.e., the site shows up for it but doesn't rank well, meaning no page really targets it. Aggregate directly over `external_metrics`; do not touch `seo_opportunities` or `striking_distance.py` (out of scope — those model per-_post_ opportunities on page-level data and are untouched by #764).

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/topic_sources/test_gsc_query_gap.py`:

```python
"""Unit tests for GscQueryGapSource — fake pool, no real DB."""

from __future__ import annotations

from typing import Any

import pytest

from services.topic_sources.gsc_query_gap import GscQueryGapSource


class _FakeConn:
    def __init__(self, setting_value: str, gap_rows: list[dict[str, Any]]):
        self._setting_value = setting_value
        self._gap_rows = gap_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query, *args):
        assert "app_settings" in query
        return self._setting_value

    async def fetch(self, query, *args):
        assert "external_metrics" in query
        return self._gap_rows


class _FakePool:
    def __init__(self, setting_value: str = "true", gap_rows: list[dict[str, Any]] | None = None):
        self._setting_value = setting_value
        self._gap_rows = gap_rows or []

    def acquire(self):
        return _FakeConn(self._setting_value, self._gap_rows)


def _gap_row(**overrides) -> dict[str, Any]:
    base = {
        "query": "docker compose tutorial",
        "impressions": 500.0,
        "avg_position": 22.5,
    }
    base.update(overrides)
    return base


class TestGscQueryGapSource:
    name = "gsc_query_gap"

    @pytest.mark.asyncio
    async def test_returns_empty_when_ingestion_disabled(self):
        source = GscQueryGapSource()
        pool = _FakePool(setting_value="false", gap_rows=[_gap_row()])
        topics = await source.extract(pool, {})
        assert topics == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_setting_missing(self):
        source = GscQueryGapSource()
        pool = _FakePool(setting_value=None, gap_rows=[_gap_row()])
        topics = await source.extract(pool, {})
        assert topics == []

    @pytest.mark.asyncio
    async def test_yields_one_topic_per_gap_row(self):
        source = GscQueryGapSource()
        pool = _FakePool(
            setting_value="true",
            gap_rows=[
                _gap_row(query="docker compose tutorial", impressions=500.0, avg_position=22.5),
                _gap_row(query="best gpu 2026", impressions=1200.0, avg_position=18.0),
            ],
        )
        topics = await source.extract(pool, {})
        assert len(topics) == 2
        titles = {t.title for t in topics}
        assert "Docker Compose Tutorial" in titles
        assert "Best Gpu 2026" in titles
        for t in topics:
            assert t.source == "gsc_query_gap"
            assert t.keywords
            assert t.relevance_score > 0
            assert t.source_url.startswith("https://www.google.com/search?q=")

    @pytest.mark.asyncio
    async def test_relevance_score_scales_with_impressions(self):
        source = GscQueryGapSource()
        pool = _FakePool(
            setting_value="true",
            gap_rows=[
                _gap_row(query="low impressions query", impressions=10.0, avg_position=20.0),
                _gap_row(query="high impressions query", impressions=2000.0, avg_position=20.0),
            ],
        )
        topics = await source.extract(pool, {})
        by_query = {t.keywords[0]: t for t in topics}
        assert (
            by_query["high impressions query"].relevance_score
            > by_query["low impressions query"].relevance_score
        )

    @pytest.mark.asyncio
    async def test_config_overrides_thresholds(self):
        # Just confirm the config dict is read without raising — the actual
        # threshold filtering happens in SQL (faked here), so this test only
        # locks in that extract() accepts the standard config keys.
        source = GscQueryGapSource()
        pool = _FakePool(setting_value="true", gap_rows=[])
        topics = await source.extract(
            pool,
            {"min_impressions": 200, "min_position": 10, "window_days": 14, "max_topics": 5},
        )
        assert topics == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/cofounder_agent && python -m pytest tests/unit/services/topic_sources/test_gsc_query_gap.py -v -o addopts=""
```

Expected: FAIL — `ModuleNotFoundError: No module named 'services.topic_sources.gsc_query_gap'`.

- [ ] **Step 3: Implement `GscQueryGapSource`**

Create `src/cofounder_agent/services/topic_sources/gsc_query_gap.py`:

```python
"""GscQueryGapSource — surface high-impression, poorly-ranked search queries
as new topic candidates.

Part of Glad-Labs/poindexter#764. Reads the per-(page, query) rows landed by
the ``gsc_main`` external_taps row's ``performance_report_custom`` stream
(see ``scripts/enable_gsc_query_dimension.py``) — a query that already earns
real search impressions but ranks poorly is a page nobody has written yet
(or an existing page only incidentally matches it), which is exactly the
kind of gap a new post can close. Distinct from the existing
``seo_refresh`` loop (Glad-Labs/poindexter#763), which re-optimizes the
metadata of *existing* posts — this source proposes *new* topics through the
ordinary topic-proposal pipeline.

Config (``plugin.topic_source.gsc_query_gap`` in app_settings — no seed
required, defaults are Python-side, matching every other TopicSource):

- ``config.min_impressions`` (default 50) — floor on summed impressions over
  the window for a query to count as a real gap, not sampling noise.
- ``config.min_position`` (default 15) — average position must be *worse*
  than this (higher number) to count as a gap; ranking well already means
  it's not a gap.
- ``config.window_days`` (default 28) — lookback window over ``external_metrics.date``.
- ``config.max_topics`` (default 10) — cap per run; the shared dedup/ranking
  pass downstream (``topic_sources/runner.py``) handles cross-source ranking,
  this cap just bounds one source's contribution.

Master switch: ``app_settings.seo.query_ingestion.enabled`` (default
``false``) — read directly since it's a cross-cutting SEO-harvest setting,
not this source's own per-plugin config. Ships inert until the operator
verifies the ingested query data and flips it on.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import classify_category

logger = logging.getLogger(__name__)


_GAP_QUERY_SQL = """
SELECT
    dimensions->>'query' AS query,
    SUM(metric_value) FILTER (WHERE metric_name = 'impressions') AS impressions,
    AVG(metric_value) FILTER (
        WHERE metric_name = 'position' AND metric_value > 0
    ) AS avg_position
FROM external_metrics
WHERE source = 'google_search_console'
  AND dimensions ? 'query'
  AND dimensions->>'query' != ''
  AND date > (NOW() - ($1::int || ' days')::interval)::date
GROUP BY dimensions->>'query'
HAVING SUM(metric_value) FILTER (WHERE metric_name = 'impressions') >= $2
   AND AVG(metric_value) FILTER (
         WHERE metric_name = 'position' AND metric_value > 0
       ) > $3
ORDER BY SUM(metric_value) FILTER (WHERE metric_name = 'impressions') DESC
LIMIT $4
"""


class GscQueryGapSource:
    """Turn poorly-ranked, high-impression GSC queries into topic proposals."""

    name = "gsc_query_gap"

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        async with pool.acquire() as conn:
            enabled_raw = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1",
                "seo.query_ingestion.enabled",
            )
        if str(enabled_raw or "").strip().lower() != "true":
            return []

        min_impressions = float(config.get("min_impressions", 50) or 50)
        min_position = float(config.get("min_position", 15) or 15)
        window_days = int(config.get("window_days", 28) or 28)
        max_topics = int(config.get("max_topics", 10) or 10)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _GAP_QUERY_SQL, window_days, min_impressions, min_position, max_topics
            )

        topics: list[DiscoveredTopic] = []
        for r in rows:
            query = (r["query"] or "").strip()
            if not query:
                continue
            impressions = float(r["impressions"] or 0)
            avg_position = float(r["avg_position"] or 0)
            title = query.title()
            topics.append(
                DiscoveredTopic(
                    title=title,
                    category=classify_category(title),
                    source=self.name,
                    source_url=f"https://www.google.com/search?q={quote_plus(query)}",
                    # Real, calculated from impressions — not fabricated.
                    # 500 impressions/window -> score 5.0; scales linearly below that.
                    relevance_score=min(impressions / 100.0, 5.0),
                    description=(
                        f"Ranks ~position {avg_position:.0f} for this query with "
                        f"{impressions:.0f} impressions in the last {window_days}d "
                        "but no page targets it directly."
                    ),
                    keywords=[query],
                )
            )

        logger.info(
            "GscQueryGapSource: %d query-gap topics (window=%dd, min_impressions=%.0f, min_position=%.0f)",
            len(topics), window_days, min_impressions, min_position,
        )
        return topics
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/cofounder_agent && python -m pytest tests/unit/services/topic_sources/test_gsc_query_gap.py -v -o addopts=""
```

Expected: 5 passed. If `test_yields_one_topic_per_gap_row`'s title-casing assertions fail on punctuation edge cases, adjust the test fixtures' query strings (not the implementation) — `.title()` is the same simple normalization already acceptable elsewhere in this codebase for display strings.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/topic_sources/gsc_query_gap.py src/cofounder_agent/tests/unit/services/topic_sources/test_gsc_query_gap.py
git commit -m "feat(seo): add GscQueryGapSource — surface query gaps as new topic candidates"
```

---

## Task 5: Register the new TopicSource

**Files:**

- Modify: `src/cofounder_agent/pyproject.toml`
- Modify: `src/cofounder_agent/plugins/registry.py`

**Interfaces:**

- Consumes: `GscQueryGapSource` from Task 4 (`services.topic_sources.gsc_query_gap:GscQueryGapSource`).
- Produces: nothing new — makes the class discoverable by `plugins.registry.get_topic_sources()`.

- [ ] **Step 1: Add the entry point**

In `src/cofounder_agent/pyproject.toml` (confirmed lines 426-433), the `[project.entry-points."poindexter.topic_sources"]` table currently reads:

```toml
[project.entry-points."poindexter.topic_sources"]
hackernews = "cofounder_agent.services.topic_sources.hackernews:HackerNewsSource"
devto = "cofounder_agent.services.topic_sources.devto:DevtoSource"
web_search = "cofounder_agent.services.topic_sources.web_search:WebSearchSource"
knowledge = "cofounder_agent.services.topic_sources.knowledge:KnowledgeSource"
codebase = "cofounder_agent.services.topic_sources.codebase:CodebaseSource"
dev_diary = "cofounder_agent.services.topic_sources.dev_diary_source:DevDiarySource"
igdb = "cofounder_agent.services.topic_sources.igdb:IGDBSource"
```

Add one line after the `igdb` entry:

```toml
gsc_query_gap = "cofounder_agent.services.topic_sources.gsc_query_gap:GscQueryGapSource"
```

- [ ] **Step 2: Add the core-samples fallback**

In `src/cofounder_agent/plugins/registry.py`, find the `_SAMPLES` list's topic_sources block (near line 880-895, right after the `IGDBSource` entry):

```python
        ("topic_sources", "services.topic_sources.igdb", "IGDBSource"),
```

Add immediately after:

```python
        # GscQueryGapSource — surfaces high-impression/poor-position GSC
        # queries as new topic candidates (poindexter#764). Ships inert:
        # gated on app_settings.seo.query_ingestion.enabled (default false).
        ("topic_sources", "services.topic_sources.gsc_query_gap", "GscQueryGapSource"),
```

This matters even with the entry_point registered — `reference_stale_entry_point_metadata` documents that a fresh `pip install -e .` isn't always re-run, and the imperative core-samples list is what actually loads in that case.

- [ ] **Step 3: Verify the source is discoverable**

```bash
cd src/cofounder_agent && python -c "
from plugins.registry import get_topic_sources
names = [getattr(s, 'name', type(s).__name__) for s in get_topic_sources()]
assert 'gsc_query_gap' in names, names
print('OK:', names)
"
```

Expected: prints `OK: [...]` including `gsc_query_gap` in the list, no assertion error.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/pyproject.toml src/cofounder_agent/plugins/registry.py
git commit -m "feat(seo): register GscQueryGapSource as a topic source"
```

---

## Task 6: Documentation

**Files:**

- Modify: `docs/architecture/seo-harvest-loop.md`

- [ ] **Step 1: Add a new section**

After the existing `## Phase 2 — refresh (\`seo_refresh\` graph_def)`section and before`## Status`, add:

```markdown
## Query-dimension ingestion (#764)

Phase 1 and Phase 2 both work from **page-level** GSC data — `external_metrics`
never carried the `query` dimension, so `seo_refresh`'s `target_query` has
always fallen back to a post's own topic/keyword rather than a real search
term. Closing that gap is two independent, additive pieces:

**Tap-side:** the installed `tap-google-search-console` package ships a
`performance_report_custom` stream whose dimension set is driven by Singer
catalog **field-level** selection (not a fixed per-stream list, unlike every
other stream it offers). `tap_singer_subprocess.py`'s catalog generator
gained a `field_selection` config key (`{tap_stream_id: [field, ...]}`) to
support this — the `gsc_main` `external_taps` row's `performance_report_custom`
stream requests `page` + `query` via `field_selection`, and
`tap_external_metrics_writer.py` needed **no changes**: it already bundles
arbitrary `dimension_fields` into the `dimensions` jsonb and dedups on
`(source, metric_name, date, slug, dimensions)`, so per-(page, query) rows
fall out for free. Activation is a one-off script
(`scripts/enable_gsc_query_dimension.py`) run once against the live tap row,
separate from the code deploy — mirrors how `seo.refresh.enabled` was flipped
only after explicit operator sign-off in Milestone B.

**Topic-side:** `GscQueryGapSource`
([`services/topic_sources/gsc_query_gap.py`](../../src/cofounder_agent/services/topic_sources/gsc_query_gap.py))
is a new `TopicSource` (same protocol as `devto.py`/`hackernews.py`) that
reads the newly-ingested per-query rows and surfaces queries with real
impressions but a poor average position as `DiscoveredTopic`s — a gap in
_new_ content, distinct from `seo_refresh`'s job of re-optimizing _existing_
posts' metadata. It feeds the ordinary topic-proposal pipeline
(`topic_sources/runner.py`), which handles cross-source dedup/ranking the
same way it already does for every other source.

### Settings (`app_settings`)

| Key                           | Default | Meaning                                                                                                                       |
| ----------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `seo.query_ingestion.enabled` | `false` | Master switch — gates both the tap-row activation and the gap source. `GscQueryGapSource.extract()` returns `[]` while false. |

`GscQueryGapSource`'s own tuning (`min_impressions`, `min_position`,
`window_days`, `max_topics`) lives under `plugin.topic_source.gsc_query_gap.config`
per the standard per-source `TopicSource` convention — no seed row required;
Python-side defaults apply when the row is absent, same as every other
topic source.
```

- [ ] **Step 2: Update the `## Status` section**

Find the existing bullet:

```markdown
- **Next:** Search Console query-dimension ingestion (#764) for sharper query
  targeting, and auto-publish graduation (republish without sign-off once the
  edit-distance trust threshold is met).
```

Replace with:

```markdown
- **Shipped (#764 — code, ships inert):** field-level Singer catalog
  selection in `tap_singer_subprocess.py`; the `GscQueryGapSource` topic
  source. **Pending operator activation:** run
  `scripts/enable_gsc_query_dimension.py` against prod, spot-check a tap run,
  then set `seo.query_ingestion.enabled=true`.
- **Next:** auto-publish graduation for `seo_refresh` (republish without
  sign-off once the edit-distance trust threshold is met); once query data has
  been live a few weeks, consider feeding real per-query `target_query` values
  into `seo_opportunities` for sharper `seo_refresh` targeting.
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture/seo-harvest-loop.md
git commit -m "docs(seo): document GSC query-dimension ingestion (#764)"
```

---

## Self-Review Notes (completed during planning)

1. **Spec coverage against poindexter#764's stated scope:**
   - "Configure the search-performance tap row... route through tap_external_metrics_writer" → Task 1 (code seam) + Task 3 (the actual row edit, as an operator-run script).
   - "Verify the writer's dedup/upsert behavior holds" → Task 2.
   - "Add a GscQueryGapSource TopicSource" → Task 4 + Task 5.
   - "Verify the tap-vs-job split before building" → resolved during planning with hard evidence from the installed tap package's source (see Task 1's "why this is the right seam"); no job is created, confirming the issue's own suspicion that this is tap-config, not a new ingestion system.
   - "Contract tests + doc update" → every task ships its own tests; Task 6 is the doc update.
2. **Placeholder scan:** no TBD/TODO markers; every step has complete, runnable code.
3. **Type/name consistency:** `_apply_field_selection(catalog, field_selection)` signature is identical everywhere it's referenced (Task 1 impl and its two call sites). `GscQueryGapSource.extract(pool, config)` matches the `TopicSource` Protocol exactly (`plugins/topic_source.py`). `DiscoveredTopic` field names (`title`, `category`, `source`, `source_url`, `relevance_score`, `description`, `keywords`) match the dataclass exactly as read from `plugins/topic_source.py`.

## Out of scope (deliberately)

- Modifying `seo_opportunities` / `striking_distance.py` to use real per-query `target_query` values — noted as future follow-on in the doc update, not part of #764's acceptance criteria.
- Flipping `seo.query_ingestion.enabled=true` in prod, or running `scripts/enable_gsc_query_dimension.py` against the live `gsc_main` row — operator activation steps, reported as ready rather than executed autonomously (the row carries live OAuth credentials and changes what a production system pulls from a third-party API).
- Auto-publish graduation for `seo_refresh` — separate, pre-existing "Next" item, not part of #764.

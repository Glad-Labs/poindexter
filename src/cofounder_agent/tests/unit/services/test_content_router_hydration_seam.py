"""Contract-driven metadata-hydration seam in ``content_router_service``.

``_load_task_metadata`` flattens per-task hydration metadata (``post_id`` /
``target_task_id`` / ``allow_stock`` / …) from
``pipeline_versions.stage_data -> 'task_metadata'`` onto a graph's initial
state — but ONLY the keys the *active graph* genuinely needs from initial
state: a declared ``PipelineState`` channel that some atom consumes BEFORE any
atom produces it. Produced-then-consumed channels (``content`` /
``quality_score`` / ``seo_title`` / …) are excluded, so a ``canonical_blog``
rejected→rewrite re-run — whose ``task_metadata`` carries the prior draft's
body and scores — can never leak them onto the fresh run's initial state.

Regression under test: the ``image_rebuild`` graph halted at its entry atom
(task ``9aeb0694``) because the previous hardcoded key allowlist only knew
``seo_refresh``'s four keys and silently dropped ``target_task_id`` /
``allow_stock`` before they reached the graph.
"""

from types import SimpleNamespace

import pytest

from services import content_router_service as crs


def _meta(*, requires=(), produces=(), inputs=()):
    """A minimal atom-meta stand-in exposing only the fields the seam reads."""
    return SimpleNamespace(
        requires=tuple(requires),
        produces=tuple(produces),
        inputs=tuple(SimpleNamespace(name=n) for n in inputs),
    )


def _resolver(mapping):
    def get_meta(name):
        return mapping.get(name)

    return get_meta


# ---------------------------------------------------------------------------
# _graph_hydration_keys (pure) — the order-sensitive consume-before-produce walk
# ---------------------------------------------------------------------------


def test_image_rebuild_captures_target_task_id_and_allow_stock():
    # load_draft REQUIRES target_task_id, optionally consumes allow_stock, and
    # RE-PRODUCES allow_stock (it normalises the operator flag). ``plan`` then
    # consumes ``content``, which load_draft produced. Hydration must include
    # allow_stock (consumed before it is produced, within the entry node) and
    # exclude content (produced upstream of its consumer). An order-INSENSITIVE
    # ``requires - produces`` would wrongly drop allow_stock — the bug this
    # test guards against.
    graph = {
        "entry": "load_draft",
        "nodes": [
            {"id": "load_draft", "atom": "content.load_draft_for_image_rebuild"},
            {"id": "plan", "atom": "content.plan_image_markers"},
        ],
        "edges": [
            {"from": "load_draft", "to": "plan"},
            {"from": "plan", "to": "END"},
        ],
    }
    metas = {
        "content.load_draft_for_image_rebuild": _meta(
            requires=("target_task_id",),
            inputs=("target_task_id", "allow_stock"),
            produces=("content", "topic", "target_version", "allow_stock"),
        ),
        "content.plan_image_markers": _meta(
            requires=("content",), inputs=("content",), produces=("image_markers",),
        ),
    }
    assert crs._graph_hydration_keys(graph, _resolver(metas)) == {
        "target_task_id",
        "allow_stock",
    }


def test_produced_channels_never_become_hydration_keys():
    # canonical_blog shape: ``gen`` produces content + quality_score; ``qa``
    # consumes them downstream. Neither may be a hydration key — that exclusion
    # is what stops a re-run's prior draft leaking onto initial state. ``topic``
    # (consumed by gen, produced by none) is the only external input.
    graph = {
        "entry": "gen",
        "nodes": [
            {"id": "gen", "atom": "content.generate_draft"},
            {"id": "qa", "atom": "qa.aggregate"},
        ],
        "edges": [
            {"from": "gen", "to": "qa"},
            {"from": "qa", "to": "END"},
        ],
    }
    metas = {
        "content.generate_draft": _meta(
            requires=("topic",), inputs=("topic",),
            produces=("content", "quality_score"),
        ),
        "qa.aggregate": _meta(
            requires=("content", "quality_score"),
            inputs=("content", "quality_score"),
            produces=("qa_reviews",),
        ),
    }
    keys = crs._graph_hydration_keys(graph, _resolver(metas))
    assert "content" not in keys
    assert "quality_score" not in keys
    assert keys == {"topic"}


def test_hydration_unions_across_all_atoms_not_just_entry():
    # seo_refresh shape: post_id/target_query consumed at the entry atom;
    # seo_opportunity_id + seo_refresh_scope consumed by a DOWNSTREAM atom.
    # All four are produced by no atom, so the union across atoms must surface
    # every one — an entry-atom-only rule would drop the downstream two.
    graph = {
        "entry": "load",
        "nodes": [
            {"id": "load", "atom": "content.load_existing_post"},
            {"id": "republish", "atom": "content.republish"},
        ],
        "edges": [
            {"from": "load", "to": "republish"},
            {"from": "republish", "to": "END"},
        ],
    }
    metas = {
        "content.load_existing_post": _meta(
            requires=("post_id",),
            inputs=("post_id", "target_query", "seo_opportunity_id"),
            produces=("content", "seo_title"),
        ),
        "content.republish": _meta(
            requires=("seo_opportunity_id", "seo_refresh_scope"),
            inputs=("seo_opportunity_id", "seo_refresh_scope", "content"),
            produces=("post_id",),
        ),
    }
    keys = crs._graph_hydration_keys(graph, _resolver(metas))
    assert {
        "post_id",
        "target_query",
        "seo_opportunity_id",
        "seo_refresh_scope",
    } <= keys


def test_undeclared_channels_are_filtered_out():
    # A consumed key that is not a declared PipelineState channel is never a
    # hydration key (defence against a typo'd atom contract flattening junk).
    graph = {
        "entry": "a",
        "nodes": [{"id": "a", "atom": "toy.a"}],
        "edges": [{"from": "a", "to": "END"}],
    }
    metas = {
        "toy.a": _meta(requires=("target_task_id", "bogus_not_a_channel")),
    }
    keys = crs._graph_hydration_keys(graph, _resolver(metas))
    assert "target_task_id" in keys
    assert "bogus_not_a_channel" not in keys


@pytest.mark.parametrize(
    "graph", [None, {}, {"nodes": []}, {"nodes": [{"id": "x"}]}],
)
def test_empty_or_nodeless_graph_yields_no_keys(graph):
    assert crs._graph_hydration_keys(graph, _resolver({})) == set()


# ---------------------------------------------------------------------------
# _load_task_metadata (IO) — reads task_metadata, filters by the active graph
# ---------------------------------------------------------------------------


class _Conn:
    def __init__(self, value):
        self._value = value

    async def fetchval(self, sql, *args):
        return self._value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, value):
        self._conn = _Conn(value)

    def acquire(self):
        return self._conn


class _DB:
    def __init__(self, value):
        self.pool = _Pool(value)


def _loader(graph):
    async def load(pool, slug):
        return graph

    return load


_IMAGE_REBUILD_GRAPH = {
    "entry": "load_draft",
    "nodes": [{"id": "load_draft", "atom": "content.load_draft_for_image_rebuild"}],
    "edges": [{"from": "load_draft", "to": "END"}],
}
_IMAGE_REBUILD_METAS = {
    "content.load_draft_for_image_rebuild": _meta(
        requires=("target_task_id",),
        inputs=("target_task_id", "allow_stock", "database_service"),
        produces=("content", "topic", "target_version", "allow_stock"),
    ),
}


@pytest.mark.asyncio
async def test_flattens_image_rebuild_inputs_including_falsey_allow_stock():
    stage_data = {"task_metadata": {"target_task_id": "f9ed99", "allow_stock": False}}
    out = await crs._load_task_metadata(
        _DB(stage_data), "rebuild-1", "image_rebuild",
        graph_loader=_loader(_IMAGE_REBUILD_GRAPH),
        get_meta=_resolver(_IMAGE_REBUILD_METAS),
    )
    # allow_stock=False is a legitimate value (operator did not opt in) and
    # must survive — the falsey-filter only drops None / "".
    assert out == {"target_task_id": "f9ed99", "allow_stock": False}


@pytest.mark.asyncio
async def test_canonical_blog_rerun_metadata_never_leaks_body_or_scores():
    # A canonical_blog rejected→rewrite re-run carries the prior draft in its
    # task_metadata. content / quality_score are produced in-graph, so they must
    # NOT be flattened onto the fresh run's initial state.
    stage_data = {
        "task_metadata": {
            "content": "PRIOR DRAFT BODY",
            "quality_score": 88,
            "seo_title": "stale title",
            "topic": "The Shift to Native Telemetry",
        }
    }
    canonical_graph = {
        "entry": "gen",
        "nodes": [
            {"id": "gen", "atom": "content.generate_draft"},
            {"id": "seo", "atom": "seo.generate_all_metadata"},
        ],
        "edges": [
            {"from": "gen", "to": "seo"},
            {"from": "seo", "to": "END"},
        ],
    }
    metas = {
        "content.generate_draft": _meta(
            requires=("topic",), inputs=("topic",),
            produces=("content", "quality_score"),
        ),
        "seo.generate_all_metadata": _meta(
            requires=("content",), inputs=("content",), produces=("seo_title",),
        ),
    }
    out = await crs._load_task_metadata(
        _DB(stage_data), "canon-1", "canonical_blog",
        graph_loader=_loader(canonical_graph), get_meta=_resolver(metas),
    )
    assert "content" not in out
    assert "quality_score" not in out
    assert "seo_title" not in out


@pytest.mark.asyncio
async def test_jsonb_returned_as_str_is_tolerated():
    import json

    raw = json.dumps({"task_metadata": {"target_task_id": "abc", "allow_stock": True}})
    out = await crs._load_task_metadata(
        _DB(raw), "rebuild-2", "image_rebuild",
        graph_loader=_loader(_IMAGE_REBUILD_GRAPH),
        get_meta=_resolver(_IMAGE_REBUILD_METAS),
    )
    assert out == {"target_task_id": "abc", "allow_stock": True}


@pytest.mark.asyncio
async def test_empty_when_no_task_metadata():
    loader = _loader(_IMAGE_REBUILD_GRAPH)
    meta = _resolver(_IMAGE_REBUILD_METAS)
    assert await crs._load_task_metadata(
        _DB(None), "t3", "image_rebuild", graph_loader=loader, get_meta=meta
    ) == {}
    assert await crs._load_task_metadata(
        _DB({"task_metadata": {}}), "t4", "image_rebuild",
        graph_loader=loader, get_meta=meta,
    ) == {}

"""Core pipeline Stages — one file per Stage, discovered via entry_points.

Each ``Stage`` class implements the :class:`plugins.stage.Stage`
Protocol. As of 2026-05-16, stages are orchestrated by
``services/template_runner.py`` (LangGraph state machine) via the
template definitions in ``services/pipeline_templates/__init__.py``;
``canonical_blog`` is the prod default. The legacy ``StageRunner``
chunked flow (and ``plugins/stage_runner.py`` itself) was deleted in
Lane C Stage 4 of the pipeline cutover.

See ``docs/architecture/langgraph-cutover.md`` for the cutover history
and template-authoring conventions.
"""

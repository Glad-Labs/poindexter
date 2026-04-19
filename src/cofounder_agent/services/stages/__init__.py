"""Core pipeline Stages — one file per Stage, discovered via entry_points.

Phase E decomposition of the content_router_service.py god-file. Each
``_stage_X`` function in the old file moves here as a standalone class
implementing the :class:`plugins.stage.Stage` Protocol. The runner in
``plugins.stage_runner`` walks the DB-configured order list to invoke
them.

See ``plugins/stage_runner.py`` for the order source-of-truth and per-
stage config conventions.
"""

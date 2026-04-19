"""Job implementations — scheduled maintenance plugins.

Phase C of the plugin refactor (GitHub #67). Each file is one Job
registered via ``poindexter.jobs`` entry_points. apscheduler (wrapped
by ``plugins.scheduler.PluginScheduler``) is the runtime.

Migration strategy: Phase C1 ships the package scaffold + one
representative Job (``SyncPageViewsJob``) to prove the pattern.
The other 24 idle_worker methods migrate in follow-up commits —
this is incremental by design because each job has its own tests +
interval tuning.
"""

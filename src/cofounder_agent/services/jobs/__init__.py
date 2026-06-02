"""Job implementations — scheduled maintenance plugins.

Phase C of the plugin refactor (GitHub #67). Each file is one Job
registered via ``poindexter.jobs`` entry_points. apscheduler (wrapped
by ``plugins.scheduler.PluginScheduler``) is the runtime.

Migration strategy: Phase C1 shipped the package scaffold + one
representative Job (originally ``SyncPageViewsJob``, removed 2026-06-02 as
dead two-DB-era residue; ``DbBackupJob`` is a current example) to prove the
pattern. The idle_worker methods migrate in follow-up commits — this is
incremental by design because each job has its own tests + interval tuning.
"""

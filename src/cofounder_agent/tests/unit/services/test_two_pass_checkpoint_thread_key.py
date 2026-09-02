"""The writer's inner graph keys its checkpoint thread on the TASK.

poindexter#932. The issue was filed as "orphaned checkpoints are unreachable by
checkpoint_prune", diagnosed as checkpoints whose source `pipeline_tasks` row
had been deleted. Measured on prod, that accounts for **6 of 7,846 rows** — and
nothing deletes `pipeline_tasks` at all (no retention policy targets it).

The actual cause is thread ids that `checkpoint_prune` cannot construct. It
discovers checkpoints by building `prefix || task_id`, so any thread named
after something else is invisible to every retention policy:

| thread shape                    | threads | checkpoint rows |
| ------------------------------- | ------- | --------------- |
| `two_pass-{niche}-{topic[:32]}` |      28 |             495 |
| suffixed (`media-<task>-…`)     |       1 |              10 |
| genuinely orphaned task-shaped  |       1 |               6 |

The `two_pass-` key was also not unique per run: 176 distinct 32-char topic
prefixes are shared by more than one task on prod, the worst by 201 of them,
so all of those runs wrote into one ever-growing thread — and the module-level
registries keyed on that same id would collide between concurrent same-topic
runs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "src" / "cofounder_agent").is_dir()
    )


@pytest.fixture(scope="module")
def writer_src() -> str:
    return (
        _repo_root()
        / "src/cofounder_agent/modules/content/atoms/two_pass_writer.py"
    ).read_text()


def _thread_key_expr(src: str) -> str:
    """The thread_id assignment, comments stripped."""
    m = re.search(r"^    thread_id = \(?\n?(.*?)\n    \)?\n", src, re.S | re.M)
    assert m, "thread_id assignment not found — did the writer move?"
    return m.group(0)


def test_thread_id_is_keyed_on_task_id(writer_src):
    """`prefix || task_id` is the only shape checkpoint_prune can build, so a
    thread that is not named after its task is unreachable by every retention
    policy — which is how 7,763 rows accumulated with nothing able to delete
    them."""
    expr = _thread_key_expr(writer_src)
    assert 'f"two_pass-{task_id}"' in expr


def test_legacy_topic_key_survives_only_as_the_no_task_fallback(writer_src):
    """The lab harness and unit tests call without a task_id. That key is
    correct, just not prunable — and those paths do not run in production."""
    expr = _thread_key_expr(writer_src)
    assert "if task_id" in expr
    assert 'f"two_pass-{niche_id}-{topic[:32]}"' in expr
    # ...and the legacy form must not be the unconditional key any more.
    assert not re.search(
        r'^    thread_id = f"two_pass-\{niche_id\}', writer_src, re.M,
    )


def test_prune_covers_the_writer_subgraph():
    """Keying on the task is only half of it — the prune handler still has to
    be told the prefix exists, or the new threads are just as unreachable."""
    from services.integrations.handlers.retention_checkpoint_prune import (
        _DEFAULT_THREAD_PREFIXES,
    )

    assert "two_pass-" in _DEFAULT_THREAD_PREFIXES
    # The canonical graph (thread_id == task_id) must keep its empty prefix.
    assert "" in _DEFAULT_THREAD_PREFIXES


def test_backlog_expression_inherits_the_new_prefix():
    """The backlog query (poindexter#933) reads the same prefix list, so a
    prefix added for pruning is automatically covered by the correctness
    probe rather than needing a second, drift-prone list."""
    from services.integrations.retention_backlog import build_backlog_query

    q = build_backlog_query("checkpoint_prune", {"ttl_days": 30})
    assert "two_pass-" in q.params[1]


class TestCleanupMigration:
    """The residue the old key left behind is unreachable by any policy, so it
    needs a one-time delete."""

    @pytest.fixture(scope="class")
    def migration_src(self) -> str:
        d = _repo_root() / "src/cofounder_agent/services/migrations"
        hits = sorted(d.glob("*delete_two_pass_checkpoint_threads*.py"))
        assert hits, "cleanup migration not found"
        return hits[-1].read_text()

    def test_deletes_only_threads_with_no_matching_task(self, migration_src):
        """Scoped by NOT EXISTS against pipeline_tasks, so a `two_pass-<task>`
        thread written by the new code is left for its policy to prune."""
        assert "NOT EXISTS" in migration_src
        assert "'two_pass-' || p.task_id" in migration_src
        assert "LIKE 'two_pass-%'" in migration_src

    def test_tolerates_a_database_that_never_ran_the_checkpointer(
        self, migration_src,
    ):
        """The checkpoint tables are created by LangGraph's PostgresSaver, not
        by our migrations, so a fresh DB legitimately has none of them."""
        assert "to_regclass" in migration_src

    def test_clears_dependents_before_checkpoints(self, migration_src):
        from importlib import util

        d = _repo_root() / "src/cofounder_agent/services/migrations"
        path = sorted(d.glob("*delete_two_pass_checkpoint_threads*.py"))[-1]
        spec = util.spec_from_file_location("_m932", path)
        mod = util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._TABLES.index("checkpoints") == len(mod._TABLES) - 1

    def test_down_is_an_explicit_no_op(self, migration_src):
        """One-way: the rows are checkpoint state for completed runs and there
        is nothing to restore them from."""
        assert "async def down" in migration_src
        assert "NotImplementedError" not in migration_src

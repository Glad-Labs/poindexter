"""``_RecordingSink`` — the incremental-persist seam for the console task-trace.

The pipeline's ``record_sink`` is a plain list that node adapters append a
``TemplateRunRecord`` to; ``persist_atom_runs`` writes them all in one batch at
end-of-run. That batch never runs if a run is killed mid-graph, so a killed run
leaves *no* trace rows. ``_RecordingSink`` adds an async ``on_record(seq, rec)``
that fires the instant a record lands, so a live run is visible and a killed run
leaves a partial trail (spec §5.2). Capture is best-effort — an ``on_record``
error must never break generation.
"""

from __future__ import annotations

from services.pipeline_architect import _RecordingSink
from services.template_runner import TemplateRunRecord


async def test_recording_sink_fires_on_record_with_monotonic_seq():
    """Each append fires on_record(seq, rec); seq is the pre-append length."""
    seen: list[tuple[int, str]] = []

    async def fake_on_record(seq: int, rec: TemplateRunRecord) -> None:
        seen.append((seq, rec.name))

    sink = _RecordingSink(on_record=fake_on_record)
    await sink.append_and_notify(TemplateRunRecord(name="a", ok=True))
    await sink.append_and_notify(TemplateRunRecord(name="b", ok=True))

    assert seen == [(0, "a"), (1, "b")]
    assert len(sink) == 2
    assert [r.name for r in sink] == ["a", "b"]  # the record still lands in the list


async def test_recording_sink_swallows_on_record_errors():
    """A persist failure must never break the run — the record still lands."""

    async def boom(seq: int, rec: TemplateRunRecord) -> None:
        raise RuntimeError("capture must never break the run")

    sink = _RecordingSink(on_record=boom)
    await sink.append_and_notify(TemplateRunRecord(name="x", ok=True))  # must not raise
    assert len(sink) == 1


async def test_recording_sink_plain_append_still_works():
    """LangGraph / legacy callers use ``.append`` directly (no notify)."""
    sink = _RecordingSink()
    sink.append(TemplateRunRecord(name="a", ok=True))
    assert len(sink) == 1


async def test_set_on_record_binds_after_construction():
    """The runner attaches on_record only after it knows run_id (thread_id),
    which is finalized after the sink is built + threaded into the graph."""
    seen: list[int] = []

    async def cb(seq: int, rec: TemplateRunRecord) -> None:
        seen.append(seq)

    sink = _RecordingSink()  # constructed with no callback
    await sink.append_and_notify(TemplateRunRecord(name="pre", ok=True))  # no-op notify
    sink.set_on_record(cb)
    await sink.append_and_notify(TemplateRunRecord(name="post", ok=True))

    assert seen == [1]  # only the post-bind append fired, at its seq (1)
    assert len(sink) == 2

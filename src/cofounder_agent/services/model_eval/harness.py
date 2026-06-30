"""EvalHarness — the storage/comparison seam for the model-eval loop (Plan 1, Task 4).

The ``Scorer`` computes; the ``EvalHarness`` stores + reads back. Keeping these
separate means the storage backend is rentable: ``LangfuseEvalHarness`` is the
v1 impl, ``InMemoryEvalHarness`` is the test/fallback double, and a future
Postgres impl drops in behind the same Protocol **without touching the scorer,
runner, promotion, or CLI**. No Langfuse type ever leaks past this module —
every return is a plain ``str`` / ``dict``.

The Langfuse calls mirror the proven surface in
``services.langfuse_experiments`` against langfuse ^4.6 (create_dataset /
create_dataset_item / start_as_current_span / create_score). The bug in
``langfuse-python#1655`` does not apply here: those calls take controlled,
plain-data payloads (no cyclic object graphs), unlike ``@observe`` auto-capture.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from services.logger_config import get_logger
from services.model_eval.types import GoldenSet, MetricResult

logger = get_logger(__name__)

try:  # langfuse is a runtime dep but absent from light worktree venvs; lazy-safe.
    from langfuse import Langfuse  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — exercised only on minimal envs
    Langfuse = None  # type: ignore[assignment, misc]

_KIND_GOLDEN = "model_eval_golden"
_KIND_RUN = "model_eval_run"


@runtime_checkable
class EvalHarness(Protocol):
    """Stores eval runs + reads back the latest metric per model for a slot."""

    def ensure_dataset(self, golden_set: GoldenSet) -> str: ...

    def record_results(self, run_name: str, results: list[MetricResult]) -> None: ...

    def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]: ...


class InMemoryEvalHarness:
    """Process-local harness — the test double and the Postgres-fallback seam
    placeholder. ``latest_by_model`` reflects the most recent run's values."""

    def __init__(self) -> None:
        self._runs: list[tuple[str, list[MetricResult]]] = []

    def ensure_dataset(self, golden_set: GoldenSet) -> str:
        return f"{golden_set.name}@{golden_set.version}"

    def record_results(self, run_name: str, results: list[MetricResult]) -> None:
        self._runs.append((run_name, list(results)))

    def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for _run_name, results in self._runs:  # later runs overwrite earlier
            for r in results:
                if r.slot == slot and r.metric_name == metric_name:
                    out[r.model] = r.value
        return out


def _eval_trace_id(slot: str, model: str, run_name: str) -> str:
    """Deterministic Langfuse trace id for one (slot, model, run) — concurrent
    writes upsert cleanly, same as langfuse_experiments' assignment traces."""
    raw = f"modeleval:{slot}:{model}:{run_name}"
    return "lf-meval-" + hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


class LangfuseEvalHarness:
    """Langfuse-backed harness (langfuse ^4.6).

    ``client`` may be injected (tests / advanced wiring); otherwise it is
    lazily built from ``langfuse_*`` app_settings and fails loud if creds are
    missing — an operator who turned this on but didn't configure Langfuse
    should hear about it, not silently lose runs.
    """

    def __init__(self, *, site_config: Any, client: Any = None) -> None:
        self._site_config = site_config
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        host = (self._site_config.get("langfuse_host", "") or "").strip()
        public_key = (self._site_config.get("langfuse_public_key", "") or "").strip()
        secret_key = (self._site_config.get("langfuse_secret_key", "") or "").strip()
        if not (host and public_key and secret_key):
            raise RuntimeError(
                "LangfuseEvalHarness requires langfuse_host + langfuse_public_key + "
                "langfuse_secret_key in app_settings (or pass client=). Configure "
                "Langfuse, or use InMemoryEvalHarness."
            )
        if Langfuse is None:
            raise RuntimeError(
                "langfuse package not importable — declared a dep but absent from "
                "this venv. Run `poetry install`, or use InMemoryEvalHarness."
            )
        self._client = Langfuse(host=host, public_key=public_key, secret_key=secret_key)
        logger.info("[model_eval] Langfuse harness client active (host=%s)", host)
        return self._client

    def ensure_dataset(self, golden_set: GoldenSet) -> str:
        client = self._get_client()
        name = golden_set.name
        try:
            ds = client.create_dataset(
                name=name,
                description=f"model-eval golden set ({name})",
                metadata={"version": golden_set.version, "_poindexter_kind": _KIND_GOLDEN},
            )
        except Exception as exc:  # noqa: BLE001 — dataset may already exist; non-fatal
            logger.warning("[model_eval] create_dataset(%r) failed/exists: %s", name, exc)
            return name
        for i, case in enumerate(golden_set.cases):
            try:
                client.create_dataset_item(
                    dataset_name=name,
                    input={"query": case.query, "candidates": case.candidates},
                    metadata={"case_index": i, "version": golden_set.version},
                )
            except Exception as exc:  # noqa: BLE001 — one bad item shouldn't abort the set
                logger.warning("[model_eval] create_dataset_item failed: %s", exc)
        return str(getattr(ds, "id", name))

    def record_results(self, run_name: str, results: list[MetricResult]) -> None:
        client = self._get_client()
        for r in results:
            trace_id = _eval_trace_id(r.slot, r.model, run_name)
            try:
                with client.start_as_current_span(
                    trace_context={"trace_id": trace_id},
                    name="model_eval_run",
                    metadata={
                        "slot": r.slot,
                        "model": r.model,
                        "run_name": run_name,
                        "metric_name": r.metric_name,
                        "n_cases": r.n_cases,
                        "latency_ms": r.latency_ms,
                        "_poindexter_kind": _KIND_RUN,
                    },
                    input={"slot": r.slot, "model": r.model, "run_name": run_name},
                    output={r.metric_name: r.value},
                ):
                    pass
            except Exception as exc:  # noqa: BLE001 — trace is observability; non-fatal
                logger.warning("[model_eval] eval trace write failed: %s", exc)
            self._score(client, trace_id, r.metric_name, r.value)
            for k, v in r.detail.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    self._score(client, trace_id, f"{r.metric_name}.{k}", float(v))

    @staticmethod
    def _score(client: Any, trace_id: str, name: str, value: float) -> None:
        try:
            client.create_score(trace_id=trace_id, name=name, value=float(value))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[model_eval] create_score %s failed: %s", name, exc)

    def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]:
        """Best-effort read-back. Returns ``{}`` when the SDK has no trace
        enumeration — the runner never depends on this (it compares in-memory
        within a run); this powers the CLI ``status`` view + cross-run history.
        """
        client = self._get_client()
        try:
            api = getattr(client, "api", None)
            if api is None or not hasattr(api, "trace"):
                return {}
            traces = api.trace.list(
                filter=[
                    {
                        "type": "string",
                        "column": "metadata",
                        "key": "slot",
                        "operator": "=",
                        "value": slot,
                    }
                ]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[model_eval] latest_by_model trace list failed: %s", exc)
            return {}
        out: dict[str, float] = {}
        for tr in getattr(traces, "data", []) or []:
            md = getattr(tr, "metadata", {}) or {}
            if md.get("metric_name") != metric_name:
                continue
            model = md.get("model")
            if not model:
                continue
            for s in getattr(tr, "scores", None) or []:
                if getattr(s, "name", None) == metric_name:
                    try:
                        out[str(model)] = float(getattr(s, "value"))
                    except (TypeError, ValueError):
                        # silent-ok: skip a non-numeric score value; one bad
                        # row shouldn't drop the whole latest-by-model listing.
                        pass
        return out

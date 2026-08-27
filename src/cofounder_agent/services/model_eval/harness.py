"""EvalHarness — the storage/comparison seam for the model-eval loop (Plan 1, Task 4).

The ``Scorer`` computes; the ``EvalHarness`` stores + reads back. Keeping these
separate means the storage backend is rentable: ``LangfuseEvalHarness`` is the
v1 impl, ``InMemoryEvalHarness`` is the test/fallback double, and a future
Postgres impl drops in behind the same Protocol **without touching the scorer,
runner, promotion, or CLI**. No Langfuse type ever leaks past this module —
every return is a plain ``str`` / ``dict``.

The Langfuse calls mirror ``services.langfuse_experiments`` (create_dataset /
create_dataset_item / start_as_current_observation / create_score). Both were
written against langfuse ^4.6, where the span helper was
``start_as_current_span``; the dep has since moved to ^4.13, which renamed it.
Because the call sits inside a non-fatal ``except``, the rename turned into a
logged warning per run rather than a failure, and no eval was ever persisted —
so pin these names to the installed major, not to the surface they were
copied from. The bug in
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

# The trace name record_results writes AND latest_by_model filters on. These
# are one constant on purpose: the read side filters server-side by name, so a
# rename on only one side silently empties `poindexter model-eval status`.
_TRACE_NAME = "model_eval_run"
# Read-back window. Runs are a handful of traces each, so this spans plenty of
# history while keeping `status` to a single API page.
_LATEST_TRACE_LIMIT = 100


@runtime_checkable
class EvalHarness(Protocol):
    """Stores eval runs + reads back the latest metric per model for a slot."""

    async def ensure_dataset(self, golden_set: GoldenSet) -> str: ...

    async def record_results(self, run_name: str, results: list[MetricResult]) -> None: ...

    async def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]: ...


class InMemoryEvalHarness:
    """Process-local harness — the test double and the Postgres-fallback seam
    placeholder. ``latest_by_model`` reflects the most recent run's values.

    Methods are ``async`` only to match the ``EvalHarness`` Protocol shape
    (``LangfuseEvalHarness`` needs the DB round-trip in ``get_secret``) —
    nothing here actually awaits.
    """

    def __init__(self) -> None:
        self._runs: list[tuple[str, list[MetricResult]]] = []

    async def ensure_dataset(self, golden_set: GoldenSet) -> str:
        return f"{golden_set.name}@{golden_set.version}"

    async def record_results(self, run_name: str, results: list[MetricResult]) -> None:
        self._runs.append((run_name, list(results)))

    async def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for _run_name, results in self._runs:  # later runs overwrite earlier
            for r in results:
                if r.slot == slot and r.metric_name == metric_name:
                    out[r.model] = r.value
        return out


def _eval_trace_id(slot: str, model: str, run_name: str) -> str:
    """Deterministic Langfuse trace id for one (slot, model, run) — concurrent
    writes upsert cleanly, same as langfuse_experiments' assignment traces.

    Must be EXACTLY 32 lowercase hex chars: langfuse ^4.13 validates the id as
    an OTel trace id and drops any that don't parse (``int(x, 16)``). The old
    ``lf-meval-`` prefix made every id 41 chars, so every write was discarded
    with only a warning. digest_size=16 -> 32 hex chars, so emit the bare
    digest — the namespace already lives in ``raw``.
    """
    raw = f"modeleval:{slot}:{model}:{run_name}"
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


class LangfuseEvalHarness:
    """Langfuse-backed harness (langfuse ^4.13).

    ``client`` may be injected (tests / advanced wiring); otherwise it is
    lazily built from ``langfuse_*`` app_settings and fails loud if creds are
    missing — an operator who turned this on but didn't configure Langfuse
    should hear about it, not silently lose runs.
    """

    def __init__(self, *, site_config: Any, client: Any = None) -> None:
        self._site_config = site_config
        self._client = client

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        host = (self._site_config.get("langfuse_host", "") or "").strip()
        # BOTH keys are is_secret=true on prod, so neither is in the SiteConfig
        # cache (it loads is_secret=false rows only) and plain .get() returns
        # the default — #2131 / GH-107 fixed that for secret_key but left
        # public_key on .get(), which made every credential check fail closed
        # and the whole slot unrunnable. Secrets are async-only: use get_secret.
        public_key = (await self._site_config.get_secret("langfuse_public_key", "") or "").strip()
        secret_key = (await self._site_config.get_secret("langfuse_secret_key", "") or "").strip()
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

    async def ensure_dataset(self, golden_set: GoldenSet) -> str:
        client = await self._get_client()
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

    async def record_results(self, run_name: str, results: list[MetricResult]) -> None:
        client = await self._get_client()
        for r in results:
            trace_id = _eval_trace_id(r.slot, r.model, run_name)
            try:
                # langfuse ^4.13 renamed this to start_as_current_observation
                # (as_type defaults to "span"); the old name is gone from the
                # client, so the previous call raised AttributeError into the
                # except below on every run — the warning was logged and the
                # eval silently never persisted. Same kwargs otherwise.
                with client.start_as_current_observation(
                    trace_context={"trace_id": trace_id},
                    name=_TRACE_NAME,
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

        # The SDK batches spans/scores on a background exporter and ships them
        # on shutdown. The only caller that matters here is the one-shot
        # `poindexter model-eval run` CLI, which exits immediately after this
        # returns — without an explicit flush the whole batch dies with the
        # process and the run is lost. Blocking here is correct: an eval that
        # isn't durable is an eval that didn't happen.
        try:
            client.flush()
        except Exception as exc:  # noqa: BLE001 — best-effort; results already logged
            logger.warning("[model_eval] flush failed, run may not persist: %s", exc)

    @staticmethod
    def _score(client: Any, trace_id: str, name: str, value: float) -> None:
        try:
            client.create_score(trace_id=trace_id, name=name, value=float(value))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[model_eval] create_score %s failed: %s", name, exc)

    async def latest_by_model(self, slot: str, metric_name: str) -> dict[str, float]:
        """Best-effort read-back. Returns ``{}`` when the SDK has no trace
        enumeration — the runner never depends on this (it compares in-memory
        within a run); this powers the CLI ``status`` view + cross-run history.

        Two langfuse ^4.13 constraints shape this, both of which the previous
        implementation violated (and so always returned ``{}``):

        * ``trace.list`` takes NO ``filter=`` argument — it exposes fixed
          kwargs (``name``/``tags``/``from_timestamp``/…). Passing ``filter``
          raised ``TypeError`` straight into the except below. Filter
          server-side on the trace ``name`` we write, then match slot/metric
          client-side from metadata.
        * ``trace.scores`` is a list of score *ids* (``str``), not score
          objects, so ``getattr(s, "name", ...)`` never matched. The metric
          value is already on the trace ``output`` as ``{metric_name: value}``,
          which is one fetch instead of N score look-ups.
        """
        client = await self._get_client()
        try:
            api = getattr(client, "api", None)
            if api is None or not hasattr(api, "trace"):
                return {}
            traces = api.trace.list(name=_TRACE_NAME, limit=_LATEST_TRACE_LIMIT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[model_eval] latest_by_model trace list failed: %s", exc)
            return {}

        rows = list(getattr(traces, "data", []) or [])
        # Newest-first so the first hit per model wins, matching
        # InMemoryEvalHarness ("latest prefers most recent run"). Traces
        # without a timestamp sort last rather than crashing the compare.
        rows.sort(
            key=lambda t: (
                getattr(t, "timestamp", None) is not None,
                getattr(t, "timestamp", None),
            ),
            reverse=True,
        )

        out: dict[str, float] = {}
        for tr in rows:
            md = getattr(tr, "metadata", {}) or {}
            if md.get("slot") != slot or md.get("metric_name") != metric_name:
                continue
            model = md.get("model")
            if not model or str(model) in out:
                continue
            output = getattr(tr, "output", None)
            if not isinstance(output, dict) or metric_name not in output:
                continue
            try:
                out[str(model)] = float(output[metric_name])
            except (TypeError, ValueError):
                # silent-ok: skip a non-numeric value; one bad row shouldn't
                # drop the whole latest-by-model listing.
                continue
        return out

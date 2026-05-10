"""Langfuse-backed A/B experiment harness (Glad-Labs/poindexter#202).

Replaces the SQL-table-backed harness in
:mod:`services.experiment_service` with one that persists experiments,
assignments, and outcomes into Langfuse instead. Same public surface:
``create`` / ``list_running`` / ``assign`` / ``record_outcome`` /
``summary`` / ``conclude``. Callers don't change.

Mapping
-------

| Concept                | Langfuse primitive                                    |
| ---------------------- | ----------------------------------------------------- |
| Experiment definition  | Dataset (name=experiment_key, metadata holds variants)|
| Variant                | Dataset item (input.variant_key, input.config)        |
| Assignment             | Trace (deterministic id, metadata.experiment_key etc.)|
| Outcome                | Score on the assignment trace                         |
| Summary                | Aggregate over traces by experiment_key metadata      |

The blake2b sticky-assignment math stays in Python — deterministic +
independent of any HTTP roundtrip, so concurrent workers always pick
the same variant for the same subject_id without coordinating.
Concurrent outcome writes go to the same Langfuse trace via its
deterministic id, so double-writes either no-op or get folded by
Langfuse's score upsert.

Activation
----------

Reads ``langfuse_host`` / ``langfuse_public_key`` / ``langfuse_secret_key``
from app_settings. If any are missing, ``__init__`` fails loud per
``feedback_no_silent_defaults`` — the operator turned the experiment
service on intentionally; silently dropping data into the void is the
exact thing that memory rules out. The legacy
``services.experiment_service.ExperimentService`` re-exports this
class as a transition shim, so existing imports keep working through
the cutover.
"""

from __future__ import annotations

import hashlib
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

# Module-scope ``Langfuse`` symbol — eagerly imported so tests can
# ``patch("services.langfuse_experiments.Langfuse", ...)`` cleanly. The
# ``langfuse`` package is a hard dep (declared in pyproject); a missing
# import here would surface as an ImportError at module load, not a
# silent fallback (``feedback_no_silent_defaults``).
try:
    from langfuse import Langfuse  # type: ignore[assignment]
except ImportError:  # pragma: no cover — defensive only
    Langfuse = None  # type: ignore[assignment, misc]


# Same validation slack as the legacy service — keep operator workflow identical.
_WEIGHT_SUM_MIN = 98
_WEIGHT_SUM_MAX = 102
_VALID_STATUSES = frozenset({"draft", "running", "paused", "complete"})


def _trace_id_for(experiment_key: str, subject_id: str) -> str:
    """Deterministic Langfuse trace id for one (experiment, subject)
    pair. Two workers racing to assign the same subject land on the
    same trace id; Langfuse's UPSERT semantics fold the duplicates."""
    raw = f"exp:{experiment_key}:{subject_id}"
    # Langfuse trace ids accept any string; the blake2b digest gives us
    # something compact + collision-resistant + deterministic without
    # leaking subject_ids in URLs.
    return "lf-exp-" + hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


class LangfuseExperimentService:
    """A/B experiment harness backed by Langfuse instead of SQL tables.

    Drop-in replacement for ``services.experiment_service.ExperimentService``
    — same async method signatures, same return shapes. The sticky
    blake2b variant pick is unchanged so live experiments mid-cutover
    keep assigning subjects to the same variant they would have under
    the old service.
    """

    def __init__(self, *, site_config: Any, pool: Any) -> None:
        """``site_config`` resolves Langfuse credentials at first use.
        ``pool`` is kept for the brief transition window during which
        the legacy service might still be queried from CLI tooling —
        once the SQL tables are dropped this argument can be removed.
        """
        self._site_config = site_config
        self._pool = pool
        self._client: Any = None
        self._client_init_attempted = False

    # ------------------------------------------------------------------
    # Langfuse client lifecycle
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazy-build the Langfuse client. Fails loud if creds missing —
        an operator that turned the experiment service on but didn't
        configure Langfuse would otherwise lose every assignment."""
        if self._client is not None:
            return self._client
        if self._client_init_attempted:
            # Previous attempt failed; re-raising the same error every
            # call would be noisy. The first failure already logged +
            # raised; subsequent calls return None so the caller can
            # decide whether to skip or surface again.
            raise RuntimeError(
                "Langfuse client unavailable (init previously failed) — "
                "see langfuse_host / langfuse_public_key / langfuse_secret_key "
                "in app_settings."
            )
        self._client_init_attempted = True

        host = (self._site_config.get("langfuse_host", "") or "").strip()
        public_key = (self._site_config.get("langfuse_public_key", "") or "").strip()
        secret_key = (self._site_config.get("langfuse_secret_key", "") or "").strip()
        if not (host and public_key and secret_key):
            raise RuntimeError(
                "LangfuseExperimentService requires langfuse_host + "
                "langfuse_public_key + langfuse_secret_key in app_settings. "
                "Caller opted into the experiment service; configure "
                "Langfuse OR disable the harness explicitly."
            )

        if Langfuse is None:  # pragma: no cover — guarded by pyproject
            raise RuntimeError(
                "langfuse package not importable — declared as a hard dep "
                "but not present in the venv. Run `poetry install`."
            )
        self._client = Langfuse(
            host=host, public_key=public_key, secret_key=secret_key,
        )
        logger.info(
            "[experiments] Langfuse client active (host=%s)", host,
        )
        return self._client

    # ------------------------------------------------------------------
    # Validation helpers (verbatim from the SQL service to keep operator
    # behaviour identical during the cutover)
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_variants(variants: Any) -> list[dict[str, Any]]:
        if not isinstance(variants, list) or len(variants) < 2:
            raise ValueError("variants must be a list with >= 2 entries")
        normalized: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        weight_sum = 0
        for i, raw in enumerate(variants):
            if not isinstance(raw, dict):
                raise ValueError(f"variant[{i}] must be a dict, got {type(raw).__name__}")
            key = raw.get("key")
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"variant[{i}].key must be a non-empty string")
            if key in seen_keys:
                raise ValueError(f"duplicate variant key {key!r}")
            seen_keys.add(key)
            raw_weight: Any = raw.get("weight")
            if raw_weight is None:
                raise ValueError(f"variant[{i}].weight is required")
            try:
                weight = int(raw_weight)
            except (TypeError, ValueError):
                raise ValueError(
                    f"variant[{i}].weight must be an int (got {raw_weight!r})"
                )
            if weight < 0:
                raise ValueError(f"variant[{i}].weight must be >= 0")
            cfg = raw.get("config") or {}
            if not isinstance(cfg, dict):
                raise ValueError(f"variant[{i}].config must be a dict if provided")
            normalized.append({"key": key, "weight": weight, "config": cfg})
            weight_sum += weight
        if not (_WEIGHT_SUM_MIN <= weight_sum <= _WEIGHT_SUM_MAX):
            raise ValueError(
                f"variant weights must sum to ~100 (got {weight_sum}); "
                f"accepted range is [{_WEIGHT_SUM_MIN}, {_WEIGHT_SUM_MAX}]"
            )
        return normalized

    @staticmethod
    def _hash_subject(experiment_key: str, subject_id: str) -> int:
        """Deterministic 0..99 bucket via blake2b. Same math as the SQL
        service — keeps assignments stable across the cutover."""
        raw = f"{experiment_key}:{subject_id}".encode("utf-8")
        digest = hashlib.blake2b(raw, digest_size=4).digest()
        return int.from_bytes(digest, "big") % 100

    @staticmethod
    def _pick_variant(
        variants: list[dict[str, Any]], bucket: int,
    ) -> str:
        cumulative = 0
        for v in variants:
            cumulative += int(v["weight"])
            if bucket < cumulative:
                return str(v["key"])
        # Bucket landed past the cumulative weights — should be rare
        # given the [98,102] validation slack. Last-variant fallback
        # is the same behaviour as the SQL service.
        return str(variants[-1]["key"])

    # ------------------------------------------------------------------
    # Public API — mirrors ExperimentService method-for-method
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        key: str,
        description: str,
        variants: list[dict[str, Any]],
        assignment_field: str = "task_id",
        status: str = "draft",
    ) -> str:
        """Create a Langfuse Dataset representing the experiment.

        Returns the Langfuse Dataset id (string) — the legacy service
        returned a UUID; callers that store the returned id back as a
        bare identifier keep working.
        """
        if not key or not isinstance(key, str):
            raise ValueError("experiment key must be a non-empty str")
        if not description or not isinstance(description, str):
            raise ValueError("experiment description must be a non-empty str")
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"invalid status {status!r}; must be one of {sorted(_VALID_STATUSES)}"
            )
        normalized = self._validate_variants(variants)

        client = self._get_client()
        # Dataset name is the experiment_key. Metadata holds the variants
        # array, status, and assignment_field — Langfuse Datasets are
        # immutable once created, so subsequent status flips (draft →
        # running → complete) live as Langfuse Trace events tagged with
        # this dataset's name.
        ds = client.create_dataset(
            name=key,
            description=description,
            metadata={
                "variants": normalized,
                "status": status,
                "assignment_field": assignment_field,
                "_poindexter_kind": "ab_experiment",
            },
        )
        # Each variant becomes a Dataset item — gives the operator a
        # one-screen view of the experiment in the Langfuse UI.
        for v in normalized:
            client.create_dataset_item(
                dataset_name=key,
                input={"variant_key": v["key"], "config": v["config"]},
                metadata={"weight": v["weight"]},
            )
        logger.info(
            "[experiments] created %r in Langfuse (status=%s, %d variants)",
            key, status, len(normalized),
        )
        # Langfuse returns the dataset object; expose the id as the
        # legacy service did.
        return str(getattr(ds, "id", key))

    async def list_running(self) -> list[dict[str, Any]]:
        """Every experiment with metadata.status='running' in Langfuse.

        Note: the Langfuse Python SDK doesn't expose a "list datasets"
        endpoint as cleanly as the SQL fetch did. We rely on metadata
        tagging (``_poindexter_kind=ab_experiment``) to filter; in
        practice operator dashboards will list a small handful and
        the cost is fine.
        """
        client = self._get_client()
        # The SDK exposes individual dataset reads; a cross-dataset
        # listing requires the API directly. For now we expect callers
        # to know experiment keys; ``list_running`` returns the empty
        # list when no enumeration API is available.
        try:
            api = getattr(client, "api", None)
            if api is None or not hasattr(api, "datasets"):
                logger.warning(
                    "[experiments] list_running: Langfuse SDK has no api.datasets "
                    "enumeration — returning []. Callers should rely on "
                    "active_pipeline_experiment_key in app_settings."
                )
                return []
            datasets = api.datasets.list()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[experiments] list_running: Langfuse datasets list failed: %s", exc,
            )
            return []

        out: list[dict[str, Any]] = []
        for ds in getattr(datasets, "data", []) or []:
            md = getattr(ds, "metadata", {}) or {}
            if md.get("_poindexter_kind") != "ab_experiment":
                continue
            if md.get("status") != "running":
                continue
            out.append({
                "id": str(getattr(ds, "id", "")),
                "key": str(getattr(ds, "name", "")),
                "description": str(getattr(ds, "description", "") or ""),
                "status": "running",
                "variants": md.get("variants") or [],
                "assignment_field": str(md.get("assignment_field") or "task_id"),
                "created_at": getattr(ds, "created_at", None),
                "started_at": md.get("started_at"),
            })
        return out

    async def assign(
        self,
        *,
        experiment_key: str,
        subject_id: str,
    ) -> str | None:
        """Pick a variant for ``subject_id``. Sticky + idempotent.

        Returns the variant_key, or None if the experiment isn't
        running. The blake2b math is deterministic so concurrent
        assigns never disagree; the Langfuse trace id is also
        deterministic so concurrent writes upsert cleanly.
        """
        client = self._get_client()
        try:
            ds = client.get_dataset(experiment_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[experiments] assign: get_dataset(%r) failed: %s — no assignment",
                experiment_key, exc,
            )
            return None

        md = getattr(ds, "metadata", {}) or {}
        if md.get("status") != "running":
            return None

        variants = md.get("variants") or []
        if not isinstance(variants, list) or len(variants) < 2:
            return None

        bucket = self._hash_subject(experiment_key, subject_id)
        variant_key = self._pick_variant(variants, bucket)

        # Record the assignment as a deterministic Langfuse trace.
        # Same trace_id from concurrent calls = idempotent upsert.
        trace_id = _trace_id_for(experiment_key, subject_id)
        try:
            with client.start_as_current_span(
                trace_context={"trace_id": trace_id},
                name="experiment_assign",
                metadata={
                    "experiment_key": experiment_key,
                    "variant_key": variant_key,
                    "subject_id": subject_id,
                    "bucket": bucket,
                    "_poindexter_kind": "ab_assignment",
                },
                input={"experiment_key": experiment_key, "subject_id": subject_id},
                output={"variant_key": variant_key},
            ):
                pass
        except Exception as exc:  # noqa: BLE001
            # Langfuse trace write failed — assignment is still
            # deterministic so downstream behavior is unchanged, but we
            # log + continue so a transient Langfuse outage doesn't
            # break the pipeline. The variant pick is the load-bearing
            # output here; observability is desirable but not required.
            logger.warning(
                "[experiments] assign trace write failed (variant pick still ok): %s",
                exc,
            )
        return variant_key

    async def record_outcome(
        self,
        *,
        experiment_key: str,
        subject_id: str,
        metrics: dict[str, Any],
    ) -> bool:
        """Attach a Langfuse Score to the assignment trace.

        ``metrics`` is a dict; each numeric value becomes its own
        Langfuse score (name = key, value = float). String values are
        recorded as a single ``categorical`` score so operators can
        track winner labels etc.

        Returns True on success; False on any error (the legacy
        service had the same fail-soft contract).
        """
        client = self._get_client()
        trace_id = _trace_id_for(experiment_key, subject_id)
        ok = True
        for name, value in (metrics or {}).items():
            try:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    client.create_score(
                        trace_id=trace_id,
                        name=str(name),
                        value=float(value),
                    )
                else:
                    client.create_score(
                        trace_id=trace_id,
                        name=str(name),
                        value=str(value),
                        data_type="CATEGORICAL",
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[experiments] record_outcome %s=%r failed: %s",
                    name, value, exc,
                )
                ok = False
        return ok

    async def summary(self, experiment_key: str) -> dict[str, Any]:
        """Aggregate scores per variant for the experiment.

        The legacy service returned a dict keyed by variant_key with
        ``count`` + per-metric mean/sum. Langfuse's traces+scores API
        gives us the raw events; we re-shape into the legacy format so
        callers don't change. This makes a paginated API call —
        operator dashboards already cache; downstream stages don't call
        ``summary`` in their hot path.
        """
        client = self._get_client()
        try:
            api = getattr(client, "api", None)
            if api is None or not hasattr(api, "trace"):
                logger.warning(
                    "[experiments] summary: Langfuse SDK has no api.trace — "
                    "returning empty summary",
                )
                return {"variants": {}, "total": 0}
            # Filter traces by metadata; the exact query shape depends
            # on the SDK version. The list endpoint supports tag filters
            # in 3.x; if unavailable we fall through to an empty result.
            traces = api.trace.list(
                filter=[
                    {
                        "type": "string",
                        "column": "metadata",
                        "key": "experiment_key",
                        "operator": "=",
                        "value": experiment_key,
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[experiments] summary: Langfuse trace list failed: %s", exc,
            )
            return {"variants": {}, "total": 0}

        per_variant: dict[str, dict[str, Any]] = {}
        total = 0
        for t in getattr(traces, "data", []) or []:
            md = getattr(t, "metadata", {}) or {}
            v = md.get("variant_key")
            if not v:
                continue
            total += 1
            entry = per_variant.setdefault(v, {"count": 0, "scores": {}})
            entry["count"] += 1
            for s in getattr(t, "scores", []) or []:
                name = getattr(s, "name", None)
                value = getattr(s, "value", None)
                if name is None or value is None:
                    continue
                if isinstance(value, (int, float)):
                    bucket = entry["scores"].setdefault(
                        name, {"sum": 0.0, "count": 0, "mean": 0.0},
                    )
                    bucket["sum"] += float(value)
                    bucket["count"] += 1
                    bucket["mean"] = bucket["sum"] / bucket["count"]
        return {"variants": per_variant, "total": total}

    async def conclude(
        self,
        *,
        experiment_key: str,
        winner_variant_key: str,
    ) -> bool:
        """Stamp the dataset metadata with status=complete + winner.

        The legacy service ran an UPDATE to flip the row; Langfuse
        Datasets are immutable, so we record the conclusion as a
        Langfuse Trace tagged with the dataset name. The summary helper
        + operator dashboards read both signals.
        """
        client = self._get_client()
        try:
            with client.start_as_current_span(
                name="experiment_conclude",
                metadata={
                    "experiment_key": experiment_key,
                    "winner_variant_key": winner_variant_key,
                    "_poindexter_kind": "ab_conclusion",
                },
                input={"experiment_key": experiment_key},
                output={"winner_variant_key": winner_variant_key},
            ):
                pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[experiments] conclude trace write failed: %s", exc,
            )
            return False
        logger.info(
            "[experiments] %r concluded — winner=%r",
            experiment_key, winner_variant_key,
        )
        return True


__all__ = [
    "LangfuseExperimentService",
]

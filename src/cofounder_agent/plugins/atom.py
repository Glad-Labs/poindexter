"""``AtomMeta`` Protocol — atom-level metadata for the architect-LLM.

Phase 2 of the dynamic-pipeline-composition spec. Each atom (every
file in ``services/atoms/``) exposes ``ATOM_META: AtomMeta`` at module
level. The dispatcher walks these at startup, populates a write-through
cache in ``pipeline_atoms`` (db table), and surfaces the catalog to
the architect-LLM (Phase 4) so it can compose new pipelines from the
live block inventory without importing Python.

What this lets the architect do (Phase 4):

- Validate that a composed graph's edges connect compatible I/O
  shapes (atom A produces ``draft: str``, atom B requires
  ``content: str`` — string-to-string is fine; atom B requires
  ``Bundle`` is not).
- Estimate graph cost from per-atom ``cost_class`` and current router
  weights.
- Route atom calls through the right capability tier — atom declares
  ``capability_tier="cheap_critic"``, router resolves to a concrete
  (provider, model) at execution time.
- Retry/fallback policy is declarative — atom says "on httpx.HTTPError
  retry up to 2 times with 5s backoff, then walk the fallback chain
  cheap_critic → budget_critic → free_critic"; the architect doesn't
  hand-code that into every node.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#360.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# Cost classes — coarse buckets that the architect uses for budget
# projection. The real cost lookup happens via ``cost_logs`` after the
# fact; this is just the "expect cheap / expect expensive" hint.
CostClass = Literal["free", "compute", "api", "premium"]


# Atom type — what kind of building block this is. Same shape as the
# existing plugin Protocol categories so the architect can reason about
# "find me an atom of type ``writer_mode``" or "find me an
# ``approval_gate`` to insert here."
AtomType = Literal[
    "stage",
    "writer_mode",
    "topic_source",
    "llm_provider",
    "image_provider",
    "audio_provider",
    "video_provider",
    "tts_provider",
    "qa_gate",
    "object_store",
    "atom",          # generic atomic action (services/atoms/*)
    "approval_gate",  # operator-interrupt atom
    "ops",            # business-ops atom (Stripe, Resend, Discord, etc.)
]


@dataclass(frozen=True)
class FieldSpec:
    """One input or output field declaration on an atom.

    Type is a string for now — LangGraph state is dict[str, Any] and we
    don't want to force pydantic models everywhere in v1. Phase 3+ may
    promote to actual pydantic types per atom; this remains compatible
    because Pydantic models stringify cleanly.
    """

    name: str
    type: str               # e.g. "str", "dict", "BundleDict", "list[Review]"
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class RetryPolicy:
    """How an atom recovers from transient failure.

    ``max_attempts`` includes the initial try; 1 means no retry.
    ``backoff_s`` is the base; the runner multiplies by 2^(attempt-1)
    for exponential. ``retry_on`` lists the exception class names; an
    exception that doesn't match propagates without retry.
    """

    max_attempts: int = 1
    backoff_s: float = 0.0
    retry_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class AtomMeta:
    """Atom-level metadata exposed to the dispatcher + architect-LLM.

    Each atom module declares this as ``ATOM_META`` at module level.
    The startup walker collects them by importing modules under
    ``services.atoms`` (and the existing plugin packages) and reading
    the constant; the result lands in ``pipeline_atoms`` as a
    write-through cache.

    Source of truth is the Python class — the table is just a
    catalog the architect-LLM queries. Re-running the discovery walker
    after a code change rewrites the relevant rows.
    """

    name: str                              # globally unique slug — "atoms.narrate_bundle"
    type: AtomType
    version: str                           # semver — "1.0.0"
    description: str                       # one-liner for human + architect
    inputs: tuple[FieldSpec, ...] = ()
    outputs: tuple[FieldSpec, ...] = ()
    requires: tuple[str, ...] = ()         # preconditions: "context_bundle", "draft"
    produces: tuple[str, ...] = ()         # artifacts: "draft", "image_url"
    capability_tier: str | None = None     # router tier slug, None for non-LLM atoms
    cost_class: CostClass = "compute"
    idempotent: bool = False
    side_effects: tuple[str, ...] = ()     # "writes pipeline_versions", "calls ollama", etc.
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    fallback: tuple[str, ...] = ()         # capability tier chain on persistent failure
    parallelizable: bool = False           # safe to run concurrently with siblings

    def to_jsonb(self) -> dict[str, Any]:
        """Serialize for the ``pipeline_atoms.meta`` JSONB column."""
        return {
            "name": self.name,
            "type": self.type,
            "version": self.version,
            "description": self.description,
            "inputs": [
                {
                    "name": f.name, "type": f.type,
                    "description": f.description, "required": f.required,
                }
                for f in self.inputs
            ],
            "outputs": [
                {
                    "name": f.name, "type": f.type,
                    "description": f.description, "required": f.required,
                }
                for f in self.outputs
            ],
            "requires": list(self.requires),
            "produces": list(self.produces),
            "capability_tier": self.capability_tier,
            "cost_class": self.cost_class,
            "idempotent": self.idempotent,
            "side_effects": list(self.side_effects),
            "retry": {
                "max_attempts": self.retry.max_attempts,
                "backoff_s": self.retry.backoff_s,
                "retry_on": list(self.retry.retry_on),
            },
            "fallback": list(self.fallback),
            "parallelizable": self.parallelizable,
        }


__all__ = [
    "AtomMeta",
    "AtomType",
    "CostClass",
    "FieldSpec",
    "RetryPolicy",
]

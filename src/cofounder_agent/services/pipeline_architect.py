"""``pipeline_architect`` — local-LLM service that composes pipelines on demand.

Phase 4 of the dynamic-pipeline-composition spec. Takes a high-level
intent (free-text or structured) plus the live atom catalog, returns
a LangGraph ``StateGraph`` factory that the TemplateRunner can execute.

Constraints (per Matt 2026-05-04):

- **Local LLM only by default.** Uses ``pipeline_writer_model`` from
  app_settings (currently glm-4.7-5090 or qwen3:30b). Cloud APIs are
  opt-in fallbacks ONLY when explicitly enabled in app_settings AND
  gated by cost_guard. See ``feedback_no_paid_apis`` memory.
- No drag/drop UI. The architect is the composition mechanism; Matt
  approves results not configurations.
- Architect output is always reviewed by the operator (Telegram
  approve/reject) before being cached as a named template.

Output contract:

The architect returns a JSON object describing the graph (nodes,
edges, entry, optional config per node). A separate compiler
function (:func:`build_graph_from_json`) resolves atom names to
callables and wires up the LangGraph. JSON is preferred over Python
code generation because:

1. Local LLMs follow JSON schemas more reliably than emit Python.
2. JSON is trivial to validate (every node atom must exist in the
   catalog, every edge must reference a declared node, the graph
   must be a DAG).
3. JSON serializes cleanly into ``pipeline_templates.graph_def`` for
   caching.

Issue: Glad-Labs/poindexter#364.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, StateGraph

from plugins.tracing import get_tracer
from services.atom_registry import (
    get_atom_callable,
    get_atom_meta,
    list_atoms,
    to_catalog_text,
)
from services.site_config import SiteConfig
from services.template_runner import (
    PipelineState,
    make_stage_node,
)

# Phase-2c (#272): the module-global ``site_config`` + ``set_site_config``
# shim were removed. ``compose`` now requires an explicit ``site_config=``
# kwarg; callers thread the run-bound instance (stages →
# ``context.get("site_config")``). The module is no longer in
# ``di_wiring.WIRED_MODULES``.

logger = logging.getLogger(__name__)

# Per-node tracer (poindexter#711 item 1). The Prefect subprocess wires the
# tracer provider via ``setup_telemetry`` but no graph node created a span, so
# Tempo only saw the FastAPI worker's HTTP spans. ``get_tracer`` returns an
# OTel ProxyTracer when called before the provider is set at flow start, so a
# module-level tracer still exports once ``setup_telemetry`` runs.
_NODE_TRACER = get_tracer("poindexter.pipeline")


def _span_wrap(
    fn: Callable[..., Any], node_id: str, kind: str
) -> Callable[..., Any]:
    """Wrap a compiled graph node callable in an OTel span (poindexter#711
    item 1), giving the pipeline subprocess a per-node trace tree in Tempo.

    An EXTERNAL wrapper applied at the ``add_node`` seam, so the delicate
    ``make_stage_node`` / ``_wrap_atom`` bodies (GraphInterrupt handling,
    record_sink, progress events) stay untouched. ``config`` MUST stay
    annotated as bare ``RunnableConfig`` so LangGraph's config-injection
    allow-list (``KWARGS_CONFIG_KEYS``) still threads the services dict
    through — the same constraint the wrapped nodes document.

    ``GraphInterrupt`` (a langgraph interrupt()-based pause, e.g. an approval
    gate) propagates untouched and is NOT recorded as a span error — it's
    control flow, not a failure. Real exceptions are recorded then re-raised so
    the graph still halts.
    """
    span_name = f"pipeline.{kind}.{node_id}"

    span_attributes = {"pipeline.node_id": node_id, "pipeline.node_kind": kind}

    async def _spanned(
        state: PipelineState,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> Any:
        # Attributes set at creation (start_as_current_span accepts them) so
        # there's no separate guarded set_attribute call. Mirrors
        # plugins.tracing.traced_span's exception shape: GraphInterrupt is
        # control flow and re-raises unrecorded; a real error is recorded then
        # re-raised so the graph still halts.
        with _NODE_TRACER.start_as_current_span(
            span_name, attributes=span_attributes
        ) as span:
            try:
                return await fn(state, config)
            except GraphInterrupt:
                raise
            except Exception as exc:
                span.record_exception(exc)
                raise

    return _spanned


# Prompt key in UnifiedPromptManager + prompt_templates table. The
# default lives at skills/content/atoms/SKILL.md; runtime overrides come
# from the prompt_templates DB row. Per feedback_prompts_must_be_db_configurable.
# The {site_name} placeholder is rendered from site_config by compose().
_PROMPT_KEY = "atoms.pipeline_architect.system_prompt"


def _resolve_system_prompt() -> str:
    """Pull the architect system prompt via UnifiedPromptManager."""
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(_PROMPT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[pipeline_architect] prompt_manager lookup for %r failed (%s) — "
            "using inline fallback",
            _PROMPT_KEY, exc,
        )
        return _ARCHITECT_SYSTEM_PROMPT_FALLBACK


# The {site_name} placeholder is rendered from the run-bound site_config
# by compose() (see the .format() call). The JSON-schema braces are
# escaped as {{ / }} so .format() leaves them as literal single braces.
_ARCHITECT_SYSTEM_PROMPT_FALLBACK = """\
You are the {{site_name}} pipeline architect. Given an INTENT (high-level
request) and an ATOM CATALOG (one bullet line per atom, with PURPOSE
/ INPUTS / OUTPUTS / REQUIRES / PRODUCES blocks), produce a JSON
object describing a LangGraph pipeline that satisfies the intent.

CATALOG FORMAT:
Each atom is a bullet line followed by indented blocks. The header
line shape is: name vN | type | tier=X | cost=Y | flags. The blocks
that follow tell you what to chain:
  PURPOSE: one sentence explaining what the atom does.
  INPUTS: name:type(R|O), comma-separated. R=required, O=optional.
  OUTPUTS: name:type, comma-separated. These land in shared state.
  REQUIRES: state keys this atom needs upstream — chain accordingly.
  PRODUCES: state keys this atom adds — feeds downstream atoms.
  FALLBACK: tier resolution chain (cheap_critic -> budget_critic -> ...).

HARD RULES:

1. Use atom names exactly as they appear in the ATOM CATALOG. Names
   are namespaced — atoms.* are native composable atoms, stage.* are
   legacy stages surfaced as virtual atoms. If the closest name in
   the catalog has a different prefix, use the catalog form.
2. Build the graph as a DAG — every edge moves the pipeline forward
   toward END.
3. Every non-terminal node has at least one outgoing edge.
4. Terminal edges use the literal string "END" as the 'to' value.
5. The 'entry' field names the first node to run.
6. Output one valid JSON object matching the schema. The first
   character is `{{` and the last character is `}}`.

JSON SCHEMA:

{{
  "name": "<short_snake_slug>",
  "description": "<one-sentence purpose>",
  "entry": "<node_id_of_entry>",
  "nodes": [
    {{"id": "<unique_node_id>", "atom": "<atom_name_from_catalog>",
     "config": {{<optional state seed values for this node>}}}}
  ],
  "edges": [
    {{"from": "<node_id>", "to": "<node_id_or_'END'>"}}
  ]
}}

COMPOSITION HEURISTICS (use the catalog REQUIRES/PRODUCES blocks):

- An atom whose REQUIRES lists key K must be downstream of an atom
  whose PRODUCES lists K (or of an upstream stage that seeds K).
- Multiple edges from the same source create a parallel fan-out —
  every successor runs concurrently. Use this for parallel critic
  reviews or independent media generation.
- An atom marked "parallelizable" is safe to run as a sibling fan-out.
- Approval gates (atoms.approval_gate or stage.approval_gate) set
  _halt=True and pause the pipeline; the operator approves to resume.
  Place these AFTER the artifact you want reviewed has been produced.
- aggregate_reviews must follow N review_with_critic atoms (it folds
  state.qa_reviews into a single verdict).
- Prefer linear graphs unless the intent calls for parallelism.
- For dev_diary content: stage.verify_task -> atoms.narrate_bundle
  -> stage.finalize_task is the canonical 3-step pattern.

If the spec validator returns errors on a previous attempt, every
error message starts with "FIX:" followed by exactly what to change.
On retry, apply each FIX literally — keep the rest of the prior
spec intact.
"""


# ---------------------------------------------------------------------------
# Result + error types
# ---------------------------------------------------------------------------


@dataclass
class ArchitectResult:
    """What the architect returned. Either a valid graph spec or errors."""

    ok: bool
    spec: dict[str, Any] | None = None
    raw_response: str = ""
    errors: list[str] | None = None
    model_used: str = ""

    @property
    def slug(self) -> str:
        return (self.spec or {}).get("name") or ""


# ---------------------------------------------------------------------------
# Compose: intent → graph spec (LLM call + validation)
# ---------------------------------------------------------------------------


async def compose(
    intent: str,
    *,
    site_config: SiteConfig,
    context: dict[str, Any] | None = None,
    max_attempts: int = 3,
    pool: Any = None,
) -> ArchitectResult:
    """Ask the local writer LLM to compose a graph for the given intent.

    Retries up to ``max_attempts`` times when validation fails, feeding
    the structured "FIX:..." error messages back into the prompt so
    the LLM can self-correct (per ``feedback_design_for_llm_consumers``
    — error messages are the LLM's primary repair signal). Returns the
    last :class:`ArchitectResult`; ok=True on success, ok=False with
    accumulated errors on persistent failure.

    ``pool`` is the asyncpg pool to use for dispatcher routing. When
    provided, the LLM call goes through ``dispatch_complete`` and
    honors ``plugin.llm_provider.primary.standard`` (LiteLLM / Ollama /
    OpenAI-compat per app_settings). When ``None``, the call falls
    back to direct httpx → local Ollama (tests / bootstrap only).

    ``site_config`` is REQUIRED (#272 Phase-2c): callers thread the
    run-bound :class:`SiteConfig` (pipeline stages →
    ``context.get("site_config")``).
    """
    _sc = site_config

    catalog_text = to_catalog_text()
    if not catalog_text.strip():
        return ArchitectResult(
            ok=False,
            errors=["FIX: atom registry is empty — call atom_registry.discover() before compose()"],
        )

    # poindexter#485 fail-loud sweep: was previously
    # ``... or "glm-4.7-5090:latest"`` — Matt's specific custom model
    # baked into a public OSS path. Architect prefers its dedicated
    # setting; falls through to the writer-model resolver (which
    # itself chains ``pipeline_writer_model`` → ``cost_tier.standard.model``
    # → ValueError). The architect cannot compose pipelines without
    # a model, so raising is the right answer for unset config.
    architect_override = (
        _sc.get("pipeline_architect_model") or ""
    ).strip()
    if architect_override:
        model = architect_override.removeprefix("ollama/")
    else:
        from services.llm_text import resolve_local_model
        model = resolve_local_model(site_config=_sc)

    # The migrated prompt carries the operator persona as a {site_name}
    # placeholder (was hardcoded "Glad Labs"). Render it once from the
    # run-bound site_config before the loop. Empty string when unset so
    # .format never raises on a missing key.
    system_prompt = _resolve_system_prompt().format(
        site_name=(_sc.get("site_name") if _sc else "") or "",
        site_url=(_sc.get("site_url") if _sc else "") or "",
    )

    base_user_prompt = f"INTENT: {intent}\n\n"
    if context:
        base_user_prompt += f"CONTEXT: {json.dumps(context, default=str)[:2000]}\n\n"
    base_user_prompt += "ATOM CATALOG:\n\n" + catalog_text

    last_raw = ""
    last_spec: dict[str, Any] | None = None
    last_errors: list[str] = []
    prior_attempts: list[tuple[str, list[str]]] = []  # (raw_json, errors)

    for attempt in range(1, max_attempts + 1):
        retry_block = ""
        if prior_attempts:
            # Feed the LAST attempt's spec + errors back in; the FIX:
            # prefixes tell the LLM what to change literally.
            last_attempt_raw, last_attempt_errors = prior_attempts[-1]
            retry_block = (
                "\n\nPREVIOUS ATTEMPT (rejected by validator):\n"
                f"{last_attempt_raw}\n\n"
                "VALIDATOR ERRORS — apply each FIX literally:\n"
                + "\n".join(f"- {e}" for e in last_attempt_errors)
                + "\n\nNow emit a corrected JSON spec."
            )

        full_prompt = (
            f"{system_prompt}\n\n"
            f"---\n\n"
            f"{base_user_prompt}{retry_block}\n\n"
            f"---\n\n"
            f"Output one JSON object. The first character is `{{` and "
            f"the last character is `}}`."
        )

        raw = await _ollama_chat_text(
            full_prompt,
            model=model,
            site_config=_sc,
            pool=pool,
            timeout_setting="pipeline_architect_timeout_seconds",
        )
        last_raw = raw
        spec, parse_errors = _parse_json_spec(raw)
        if parse_errors:
            last_errors = parse_errors
            prior_attempts.append((raw[:1500], parse_errors))
            logger.info(
                "[architect] attempt %d/%d: parse failed (%s)",
                attempt, max_attempts, parse_errors[0][:120],
            )
            continue

        valid, validation_errors = _validate_spec(spec)
        if valid:
            return ArchitectResult(
                ok=True, spec=spec, raw_response=raw, model_used=model,
            )

        last_spec = spec
        last_errors = validation_errors
        prior_attempts.append(
            (json.dumps(spec, default=str)[:1500], validation_errors)
        )
        logger.info(
            "[architect] attempt %d/%d: %d validation error(s) — retrying",
            attempt, max_attempts, len(validation_errors),
        )

    return ArchitectResult(
        ok=False, spec=last_spec, raw_response=last_raw,
        errors=last_errors, model_used=model,
    )


def _parse_json_spec(raw: str) -> tuple[dict[str, Any], list[str]]:
    """Extract + parse the JSON spec from the model's response.

    Tolerates models that wrap output in markdown fences or add prose
    around the JSON object — finds the outermost balanced ``{...}``
    and parses that. Returns (spec, errors).
    """
    raw = (raw or "").strip()
    if not raw:
        return {}, ["empty response from model"]

    # Strip markdown fences if present.
    if raw.startswith("```"):
        # Strip ```json or ``` opening, plus closing ```
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # Find the outermost {...}.
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}, [f"no JSON object found in response (preview: {raw[:200]!r})"]
    payload = raw[start : end + 1]

    try:
        spec = json.loads(payload)
    except json.JSONDecodeError as exc:
        return {}, [f"invalid JSON: {exc}"]

    if not isinstance(spec, dict):
        return {}, [f"top-level value is not an object (got {type(spec).__name__})"]
    return spec, []


def _validate_spec(
    spec: dict[str, Any], *, seed_keys: set[str] | None = None
) -> tuple[bool, list[str]]:
    """Verify the architect's spec is well-formed AND every atom exists.

    Returns (ok, errors). Per ``feedback_design_for_llm_consumers``:
    every error message tells the LLM HOW TO FIX the issue, not just
    what's wrong. The architect retries on failure with these errors
    in its prompt context, so they are the primary repair signal.
    """
    errors: list[str] = []

    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        errors.append(
            "FIX: add a 'name' field at the top of the spec — "
            "non-empty string slug like 'daily_dev_diary'"
        )

    nodes = spec.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append(
            "FIX: add a 'nodes' array with at least one entry. Each "
            "node must be {\"id\": str, \"atom\": str, \"config\": {}}"
        )
        return False, errors

    seen_ids: set[str] = set()
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append(
                f"FIX nodes[{i}]: replace with an object "
                "{\"id\": str, \"atom\": str}"
            )
            continue
        nid = n.get("id")
        atom = n.get("atom")
        if not isinstance(nid, str) or not nid.strip():
            errors.append(
                f"FIX nodes[{i}]: add 'id' field with a unique string slug"
            )
            continue
        if nid in seen_ids:
            errors.append(
                f"FIX: rename duplicate node id {nid!r} — every node id "
                "must be unique within the graph"
            )
            continue
        seen_ids.add(nid)
        if not isinstance(atom, str) or not atom.strip():
            errors.append(
                f"FIX node {nid!r}: add 'atom' field naming an atom "
                "from the catalog (e.g. 'atoms.narrate_bundle')"
            )
            continue
        # Tolerant lookup: LLMs sometimes drop the namespace prefix.
        # Resolve "narrate_bundle" → "atoms.narrate_bundle" if the
        # bare name isn't registered but a single namespaced match is.
        if get_atom_meta(atom) is None:
            candidates = [
                m.name for m in list_atoms()
                if m.name == atom or m.name.endswith("." + atom)
            ]
            if len(candidates) == 1:
                # Rewrite the spec in-place to the canonical name so
                # later compilation finds the atom cleanly.
                n["atom"] = candidates[0]
            elif len(candidates) > 1:
                errors.append(
                    f"FIX node {nid!r}: atom {atom!r} is ambiguous — "
                    f"pick one of {candidates}"
                )
            else:
                # Suggest 3 closest by simple substring; gives the LLM
                # something to grab onto on retry.
                all_names = sorted(m.name for m in list_atoms())
                close = [n for n in all_names if atom.lower() in n.lower()][:3]
                hint = (
                    f"closest matches: {close}" if close
                    else f"available: {all_names}"
                )
                errors.append(
                    f"FIX node {nid!r}: atom {atom!r} not in catalog. {hint}"
                )

    edges = spec.get("edges") or []
    if not isinstance(edges, list):
        errors.append(
            "FIX: 'edges' must be an array of {\"from\": node_id, "
            "\"to\": node_id_or_'END'}"
        )
        return False, errors

    for j, e in enumerate(edges):
        if not isinstance(e, dict):
            errors.append(
                f"FIX edges[{j}]: replace with object "
                "{\"from\": str, \"to\": str}"
            )
            continue
        src = e.get("from")
        dst = e.get("to")
        if src not in seen_ids:
            errors.append(
                f"FIX edges[{j}]: 'from' references {src!r} which is "
                f"not a declared node id. Declared: {sorted(seen_ids)}"
            )
        if dst != "END" and dst not in seen_ids:
            errors.append(
                f"FIX edges[{j}]: 'to' references {dst!r} which is "
                f"not a declared node id and is not 'END'. "
                f"Declared: {sorted(seen_ids)}"
            )

    entry = spec.get("entry")
    if entry is not None and entry not in seen_ids:
        errors.append(
            f"FIX: 'entry' is {entry!r} but no node has that id. "
            f"Set entry to one of {sorted(seen_ids)}"
        )

    # Cycle detection: trivial DFS on adjacency list.
    if not errors:
        adj: dict[str, list[str]] = {nid: [] for nid in seen_ids}
        for e in edges:
            # A "loop"-flagged edge is the one designated rescue back-edge
            # (qa.rewrite -> qa.programmatic). Skip it in cycle detection so
            # the deliberate cycle validates; unflagged back-edges still error.
            if e.get("loop"):
                continue
            src, dst = e["from"], e["to"]
            if dst != "END":
                adj.setdefault(src, []).append(dst)
        if _has_cycle(adj):
            errors.append(
                "FIX: the graph contains a cycle. Every edge must move "
                "the pipeline forward; remove edges that loop back to "
                "an earlier node. Pipelines must be DAGs."
            )

    # I/O contract check (#355): every node's atom.requires must be
    # satisfiable from an upstream node's produces, the node's own config,
    # or the initial-state contract (declared PipelineState fields). Catches
    # a mis-wired composition at build/seed time instead of a runtime
    # KeyError mid-pipeline.
    if not errors:
        if seed_keys is None:
            # Lazy import dodges the template_runner <-> pipeline_architect cycle.
            from services.template_runner import PipelineState
            seed_keys = set(PipelineState.__annotations__)

        indeg = dict.fromkeys(seen_ids, 0)
        adj2: dict[str, list[str]] = {nid: [] for nid in seen_ids}
        for e in edges:
            # Skip the designated rescue back-edge: counting it would inflate
            # the loopback target's indegree so it never reaches 0, silently
            # dropping it and its whole downstream chain from the requires
            # reachability check below. The cycle itself is permitted (above).
            if e.get("loop"):
                continue
            if e.get("to") != "END" and e.get("from") in seen_ids and e.get("to") in seen_ids:
                adj2[e["from"]].append(e["to"])
                indeg[e["to"]] += 1
        ready = [nid for nid in seen_ids if indeg[nid] == 0]
        order: list[str] = []
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for nxt in adj2[cur]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)

        node_by_id = {
            n["id"]: n for n in nodes
            if isinstance(n, dict) and isinstance(n.get("id"), str)
        }
        available: set[str] = set(seed_keys)
        for nid in order:
            n = node_by_id.get(nid)
            if n is None:
                continue
            meta = get_atom_meta(n.get("atom", ""))
            cfg_keys = set((n.get("config") or {}).keys())
            req = set(meta.requires) if meta else set()
            missing = req - available - cfg_keys
            if missing:
                errors.append(
                    f"FIX node {nid!r}: requires {sorted(missing)} but no "
                    "upstream node produces them, they're not in the node's "
                    "config, and they're not initial-state fields. Reorder so "
                    "a producer runs first, or seed them via config."
                )
            if meta:
                available |= set(meta.produces)

    return (not errors), errors


def _has_cycle(adj: dict[str, list[str]]) -> bool:
    """Tarjan-ish DFS cycle detection. White/gray/black coloring."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(adj, WHITE)

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for nxt in adj.get(node, []):
            c = color.get(nxt, WHITE)
            if c == GRAY:
                return True
            if c == WHITE and dfs(nxt):
                return True
        color[node] = BLACK
        return False

    for start in adj:
        if color[start] == WHITE and dfs(start):
            return True
    return False


# ---------------------------------------------------------------------------
# Schema validation: produces/requires vs PipelineState (#753)
# ---------------------------------------------------------------------------


def _get_pipeline_state_keys() -> frozenset[str]:
    """Return the set of keys declared on PipelineState.

    Uses ``PipelineState.__annotations__`` (TypedDict introspection).
    Lazy import avoids the template_runner <-> pipeline_architect cycle;
    the result is deterministic for a process lifetime so callers may
    cache it, but it is cheap enough to call inline.
    """
    from services.template_runner import PipelineState
    return frozenset(PipelineState.__annotations__)


def _validate_graph_schema(
    spec: dict,
    *,
    state_keys: frozenset[str] | None = None,
) -> None:
    """Validate that every atom's ``produces`` and ``requires`` keys are
    declared in ``PipelineState``.

    LangGraph silently drops state updates whose keys are not declared in
    the ``TypedDict`` schema (Glad-Labs/poindexter#753 — five recorded
    production incidents). This function catches those mismatches at
    **graph compile time** so the error surfaces immediately rather than
    mid-pipeline at runtime.

    The check runs once at ``build_graph_from_spec`` time (startup /
    migration re-seed) — zero overhead in the hot path.

    ``state_keys`` is injectable for testing; defaults to the live
    ``PipelineState.__annotations__`` key set.

    Raises:
        ValueError: if any atom ``produces`` or ``requires`` a key not
            declared in ``PipelineState``. The message names the atom,
            the direction (produces / requires), and the offending key(s)
            so the developer knows exactly what to add to PipelineState.
    """
    if state_keys is None:
        state_keys = _get_pipeline_state_keys()

    nodes = spec.get("nodes") or []
    errors: list[str] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id") or "<unknown>"
        atom_name = node.get("atom") or ""
        meta = get_atom_meta(atom_name)
        if meta is None:
            # Unknown atom — already caught by _validate_spec; skip here.
            continue

        # Check produces keys.
        undeclared_produces = [k for k in meta.produces if k not in state_keys]
        if undeclared_produces:
            for key in undeclared_produces:
                errors.append(
                    f"Atom {atom_name!r} (node {nid!r}) produces key "
                    f"{key!r} not declared in PipelineState. "
                    f"Add it to PipelineState or fix the atom metadata."
                )

        # Check requires keys (belt-and-suspenders: _validate_spec checks
        # reachability; this checks declaration, which is a separate concern —
        # a required key might be satisfiable upstream yet still undeclared,
        # causing LangGraph to silently drop it on the graph_def path).
        undeclared_requires = [k for k in meta.requires if k not in state_keys]
        if undeclared_requires:
            for key in undeclared_requires:
                errors.append(
                    f"Atom {atom_name!r} (node {nid!r}) requires key "
                    f"{key!r} not declared in PipelineState. "
                    f"Add it to PipelineState or fix the atom metadata."
                )

    if errors:
        bullet_list = "\n".join(f"  - {e}" for e in errors)
        raise ValueError(
            f"Graph spec {spec.get('name', '<unnamed>')!r} failed "
            f"PipelineState schema validation "
            f"(Glad-Labs/poindexter#753):\n{bullet_list}"
        )


# ---------------------------------------------------------------------------
# Compile: spec → LangGraph factory
# ---------------------------------------------------------------------------


def build_graph_from_spec(
    spec: dict[str, Any],
    *,
    pool: Any,
    record_sink: list | None = None,
    on_event: Any = None,
) -> StateGraph:
    """Compile a validated spec into an executable LangGraph.

    Caller MUST validate the spec first (via :func:`compose` or
    :func:`_validate_spec` directly). This function trusts the spec
    and will raise on missing atoms / unresolvable references.

    Atoms are resolved through the registry. Stage-typed atoms (the
    legacy 12 stages) are wrapped via ``make_stage_node``; pure atoms
    are wrapped with a tiny adapter that records to ``record_sink``
    on the same shape as stage nodes.

    ``on_event`` (Glad-Labs/poindexter#361 part 2) is the optional async
    progress callback threaded down from ``TemplateRunner.run``. It is
    forwarded to BOTH ``make_stage_node`` (stage nodes) and ``_wrap_atom``
    (atom nodes) so every node emits node_started / node_completed /
    node_failed, with ``index``/``total`` reflecting position in the graph.
    A callback failure never breaks the run (see ``_safe_on_event``).
    """
    from plugins.registry import get_core_samples

    g: StateGraph = StateGraph(PipelineState)
    stages_by_name = {s.name: s for s in get_core_samples().get("stages", [])}

    nodes = spec["nodes"]
    edges = spec.get("edges") or []
    total_nodes = len(nodes)

    # Build node map.
    node_ids: list[str] = []
    for index, n in enumerate(nodes):
        nid = n["id"]
        atom_name = n["atom"]
        meta = get_atom_meta(atom_name)
        if meta is None and atom_name.startswith("stage."):
            # Allow ``stage.foo`` shorthand to reference the legacy
            # stage registry.
            stage_short = atom_name.removeprefix("stage.")
            stage = stages_by_name.get(stage_short)
            if stage is None:
                raise KeyError(
                    f"node {nid!r} references stage {stage_short!r} "
                    f"which is not registered"
                )
            g.add_node(
                nid,
                _span_wrap(
                    make_stage_node(
                        stage, pool, record_sink=record_sink, on_event=on_event,
                    ),
                    nid,
                    "stage",
                ),
            )
        else:
            if meta is None:
                raise KeyError(f"unknown atom {atom_name!r} in node {nid!r}")
            run_fn = get_atom_callable(atom_name)
            if run_fn is None:
                raise KeyError(f"atom {atom_name!r} has no callable")
            # Thread the node's static ``config`` dict from the spec into
            # the atom adapter so per-node config (e.g. an approval_gate's
            # {"gate_name": "draft_gate"}) seeds the atom_input. State
            # values take precedence; config is the fallback/seed.
            node_config = n.get("config") if isinstance(n.get("config"), dict) else None
            g.add_node(
                nid,
                _span_wrap(
                    _wrap_atom(
                        run_fn, atom_name, nid, record_sink,
                        node_config=node_config,
                        on_event=on_event,
                        index=index,
                        total=total_nodes,
                        retry_policy=meta.retry if meta else None,
                    ),
                    nid,
                    "atom",
                ),
            )
        node_ids.append(nid)

    # Wire entry + edges.
    entry = spec.get("entry") or node_ids[0]
    g.set_entry_point(entry)

    # The architect spec uses the literal string "END" for terminal
    # edges (more readable + JSON-safe than LangGraph's internal
    # ``__end__`` sentinel). Resolve it to the real ``END`` constant
    # at compile time so the runtime sees the right value.
    def _resolve(dst: str) -> Any:
        return END if dst == "END" else dst

    # Group edges by source so we can attach conditional edges that
    # also respect ``_halt`` short-circuit.
    out_by_src: dict[str, list[str]] = {}
    for e in edges:
        out_by_src.setdefault(e["from"], []).append(e["to"])

    # Pre-scan for branch edges: a source with a "branch": true out-edge gets
    # a _goto-aware conditional router (see _branch_router) instead of the
    # default halt/fan-out routers. Maps source node id -> branch target id.
    branch_by_src: dict[str, list[str]] = {}
    for e in edges:
        if e.get("branch"):
            branch_by_src.setdefault(e["from"], []).append(e["to"])

    def _halt_router_single(target: Any) -> Callable[[PipelineState], Any]:
        """Halt-aware router for a single successor."""

        def _route(state: PipelineState) -> Any:
            if state.get("_halt"):
                return END
            return target

        _route.__name__ = (
            f"route_to_{'END' if target is END else target}_or_end"
        )
        return _route

    def _halt_router_multi(targets: list[Any]) -> Callable[[PipelineState], Any]:
        """Halt-aware router for multiple successors (parallel fan-out).
        LangGraph runs every node returned in the list concurrently.
        """

        def _route(state: PipelineState) -> Any:
            if state.get("_halt"):
                return [END]
            return list(targets)

        _route.__name__ = (
            "fan_out_to_" + "_".join(
                "END" if t is END else str(t) for t in targets
            )
        )
        return _route

    def _branch_router(
        branch_targets: list[Any], default_target: Any,
    ) -> Callable[[PipelineState], Any]:
        """Conditional router for a node with one or more ``branch``-flagged
        out-edges.

        Priority: ``_halt`` (-> END) > ``_goto`` matching ANY branch target
        (-> that target) > the default forward target. A single branch target
        is the qa.aggregate->qa.rewrite rescue; multiple targets are the
        preview_gate 3-way split (approve -> default, regen_images / regen_text
        -> their branch targets).
        """
        targets = set(branch_targets)

        def _route(state: PipelineState) -> Any:
            if state.get("_halt"):
                return END
            goto = state.get("_goto")
            if goto in targets:
                return goto
            return default_target

        _route.__name__ = (
            "branch_to_"
            + "_".join(str(t) for t in branch_targets)
            + ("_or_END" if default_target is END else f"_or_{default_target}")
        )
        return _route

    for src, dsts in out_by_src.items():
        # Branch case: a _goto-aware conditional router. One or more of this
        # source's out-edges carry "branch": true (the qa.rewrite rescue is one;
        # preview_gate is two); the remaining non-branch edge is the default
        # forward target.
        if src in branch_by_src:
            bts = branch_by_src[src]  # raw branch target ids (>= 1)
            resolved_bts = [_resolve(b) for b in bts]
            defaults = [d for d in dsts if d not in bts]
            default_target = _resolve(defaults[0]) if defaults else END
            mapping: dict[Any, Any] = {t: t for t in resolved_bts}
            mapping[END] = END
            mapping[default_target] = default_target
            g.add_conditional_edges(
                src, _branch_router(resolved_bts, default_target), mapping,
            )
            continue
        resolved = [_resolve(d) for d in dsts]
        # Single-target case: simple halt-aware edge.
        if len(resolved) == 1:
            target = resolved[0]
            mapping = {target: target} if target is END else {target: target, END: END}
            g.add_conditional_edges(src, _halt_router_single(target), mapping)
            continue
        # Multi-target case: parallel fan-out. Build a mapping that
        # includes every target plus END for the halt path.
        mapping = {t: t for t in resolved}
        mapping[END] = END
        g.add_conditional_edges(src, _halt_router_multi(resolved), mapping)

    # Any node without an outgoing edge → END.
    sources_with_edges = set(out_by_src.keys())
    for nid in node_ids:
        if nid not in sources_with_edges:
            g.add_conditional_edges(nid, _halt_router_single(END), {END: END})

    # Schema validation: assert every atom's produces/requires keys are
    # declared in PipelineState (#753). Runs once at compile time — zero
    # overhead in the hot path. Raises ValueError on mismatch so the error
    # surfaces at startup / migration re-seed rather than silently dropping
    # state updates mid-pipeline.
    _validate_graph_schema(spec)

    return g


def _wrap_atom(
    run_fn: Callable[..., Any],
    atom_name: str,
    node_id: str,
    record_sink: list | None,
    *,
    node_config: dict[str, Any] | None = None,
    on_event: Any = None,
    index: int | None = None,
    total: int | None = None,
    retry_policy: Any = None,
) -> Callable[..., Any]:
    """Wrap a pure atom into the LangGraph node signature with
    record_sink integration so observability matches stage nodes.

    Mirrors the state-vs-services merge from ``make_stage_node``
    (Glad-Labs/poindexter#382): live service handles are pulled from
    ``RunnableConfig.configurable["__services__"]`` and merged into the
    atom's input dict so atoms that read ``state.get("database_service")``
    keep working unchanged. ``config`` MUST be annotated as bare
    ``RunnableConfig`` — see the matching note in ``make_stage_node``.

    The spec node's static ``node_config`` dict is seeded into the atom
    input as a FALLBACK (state and threaded services both take precedence)
    so per-node config — e.g. an ``approval_gate``'s
    ``{"gate_name": "draft_gate"}`` — reaches the atom's ``run(state)``.

    Stamps ``node_id`` + input/output state-key lists + digests onto the
    record so ``atom_runs`` captures the composition shape (#355 Plan 2).

    GraphInterrupt (raised by ``langgraph.types.interrupt`` inside an atom
    like ``approval_gate``) propagates UNTOUCHED — the broad ``except
    Exception`` below would otherwise convert a real checkpoint-pause into
    an error record + ``_halt``, defeating true interrupt()-based resume
    (Glad-Labs/poindexter#363). GraphInterrupt is a LangGraph control-flow
    signal, not a failure — and crucially it does NOT emit a node_failed
    progress event (no record, no on_event), matching make_stage_node.

    ``on_event`` / ``index`` / ``total`` (Glad-Labs/poindexter#361 part 2)
    wire the atom node into the streaming progress feed: node_started fires
    before ``run_fn``, then node_completed (or node_halted) on success /
    node_failed on exception. ``index``/``total`` are the node's position in
    the graph so a streaming channel can render a checklist. Callback
    failures are swallowed (``_safe_on_event``) — they never break the run.
    """

    from services.atom_runs import digest_keys
    from services.template_runner import NODE_DURATION_SECONDS as _node_duration
    from services.template_runner import (
        TemplateRunRecord,
        _mark_stage_column,
        _safe_on_event,
        _services_from_config,
    )

    def _progress_payload(
        task_id: Any, *, extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the on_event payload for this atom node.

        Carries node identity + graph position (index/total) so a
        streaming channel can render a checklist. ``extra`` merges in
        per-event fields (elapsed_ms / reason).
        """
        payload: dict[str, Any] = {
            "task_id": str(task_id or ""),
            "node": atom_name,
        }
        if index is not None:
            payload["index"] = index
        if total is not None:
            payload["total"] = total
        if extra:
            payload.update(extra)
        return payload

    async def node(
        state: PipelineState,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        import time as _time
        t0 = _time.time()
        atom_input: dict[str, Any] = dict(state)
        for svc_key, svc_value in _services_from_config(config).items():
            atom_input.setdefault(svc_key, svc_value)
        # Seed per-node static config LAST as a fallback — setdefault means
        # state + threaded services already present win over config.
        if node_config:
            for cfg_key, cfg_value in node_config.items():
                atom_input.setdefault(cfg_key, cfg_value)
        input_keys = sorted(str(k) for k in atom_input.keys())
        task_id = atom_input.get("task_id")
        # node_started — fires before run_fn so a streaming channel shows the
        # atom as in-flight. _safe_on_event maps the internal event_type to
        # the public contract name + swallows callback failures (#361).
        await _safe_on_event(
            on_event, "template.node_started", _progress_payload(task_id),
        )
        # Per-node stage stamp + progress heartbeat. _mark_stage_column writes
        # pipeline_tasks.stage = <atom node_id> AND last_progress_at = NOW(), so
        # `tasks list` + the Grafana stage panels track the LIVE atom — validation
        # finding 2: the stage column used to freeze at the last stage.* node
        # (verify_task) while the fine-grained content.*/qa.* atoms ran, because
        # atom nodes only stamped the heartbeat (_mark_progress). The brain's
        # stuck-flow probe still gets its last_progress_at heartbeat (folded into
        # the same UPDATE). Best-effort: no-ops without a pool + swallows errors,
        # so it can never break the run.
        _hb_db = atom_input.get("database_service")
        await _mark_stage_column(getattr(_hb_db, "pool", None), task_id, node_id)
        try:
            _max_a = retry_policy.max_attempts if retry_policy else 1
            result: dict[str, Any] = {}
            for _attempt in range(1, _max_a + 1):
                try:
                    result = await run_fn(atom_input)
                    break
                except GraphInterrupt:
                    raise
                except Exception as _exc:
                    _retryable = (
                        _attempt < _max_a
                        and retry_policy is not None
                        and any(
                            pat == cls.__name__ or pat.endswith(f".{cls.__name__}")
                            for cls in type(_exc).__mro__
                            for pat in retry_policy.retry_on
                        )
                    )
                    if not _retryable:
                        raise
                    _sleep = (retry_policy.backoff_s or 0.0) * (2 ** (_attempt - 1))
                    logger.warning(
                        "[architect] atom %s attempt %d/%d (%s), retry in %.1fs",
                        atom_name, _attempt, _max_a, type(_exc).__name__, _sleep,
                    )
                    await asyncio.sleep(_sleep)
            elapsed_ms = int((_time.time() - t0) * 1000)
            out = result if isinstance(result, dict) else {}
            halted = bool(out.get("_halt"))
            _node_duration.labels(
                node=atom_name, outcome="halted" if halted else "ok",
            ).observe(elapsed_ms / 1000.0)
            output_keys = sorted(str(k) for k in out.keys())
            await _safe_on_event(
                on_event,
                "template.node_halted" if halted else "template.node_completed",
                _progress_payload(
                    task_id,
                    extra={
                        "elapsed_ms": elapsed_ms,
                        **({"reason": str(out.get("_halt_reason") or "")} if halted else {}),
                    },
                ),
            )
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=atom_name, ok=True,
                        detail=f"{len(str(out.get('content','') or ''))} chars",
                        elapsed_ms=elapsed_ms,
                        node_id=node_id,
                        metrics={
                            "input_keys": input_keys,
                            "output_keys": output_keys,
                            "input_digest": digest_keys(input_keys),
                            "output_digest": digest_keys(output_keys),
                        },
                    )
                )
            return out
        except GraphInterrupt:
            # Control-flow signal — the atom called interrupt() to pause the
            # graph for operator approval. LangGraph checkpoints here and
            # bubbles this up to ainvoke; we MUST NOT swallow it. No record
            # is written (the node didn't complete or fail — it suspended).
            raise
        except Exception as exc:
            elapsed_ms = int((_time.time() - t0) * 1000)
            logger.exception("[architect] atom %s raised: %s", atom_name, exc)
            _node_duration.labels(node=atom_name, outcome="error").observe(elapsed_ms / 1000.0)
            await _safe_on_event(
                on_event, "template.node_failed",
                _progress_payload(
                    task_id,
                    extra={
                        "elapsed_ms": elapsed_ms,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                ),
            )
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=atom_name, ok=False,
                        detail=f"raised {type(exc).__name__}: {exc}",
                        halted=True, elapsed_ms=elapsed_ms,
                        node_id=node_id,
                        metrics={
                            "input_keys": input_keys,
                            "output_keys": [],
                            "input_digest": digest_keys(input_keys),
                            "output_digest": digest_keys([]),
                        },
                    )
                )
            return {"_halt": True, "_halt_reason": f"{atom_name}: {exc}"}

    node.__name__ = f"atom_node_{atom_name.replace('.', '_')}"
    return node


# ---------------------------------------------------------------------------
# Local-LLM transport
# ---------------------------------------------------------------------------
# 2026-05-16: the private ``_ollama_chat_text`` was deleted in favor of
# :func:`services.llm_text.ollama_chat_text`. Architect calls now route
# through the LLM provider dispatcher so they honor
# ``plugin.llm_provider.primary.standard='litellm'`` like the rest of
# the writer paths. The module-level alias keeps test patches at the
# historical name working.
from services.llm_text import ollama_chat_text as _ollama_chat_text  # noqa: E402

# ---------------------------------------------------------------------------
# Contract handshake: stamp atom fingerprints, gate drift at load (poindexter#755)
# ---------------------------------------------------------------------------


class GraphContractError(RuntimeError):
    """A stored graph_def references an atom whose contract has drifted from
    the live registry (or is unstamped / missing). Raised at load time so a
    drifted graph fails loud instead of running against the wrong contract
    (poindexter#755)."""


def stamp_graph_def(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``spec`` with each node stamped with the atom's current
    contract fingerprint (``_contract_fp``) and version (``_atom_version``).

    Raises :class:`GraphContractError` if a node names an atom that is not in
    the registry — you cannot stamp what you cannot resolve.
    """
    out = copy.deepcopy(spec)
    for node in out.get("nodes", []):
        atom = node.get("atom")
        meta = get_atom_meta(atom) if isinstance(atom, str) else None
        if meta is None:
            raise GraphContractError(
                f"FIX: node {node.get('id')!r} names atom {atom!r} which is not "
                f"in the registry — cannot stamp. Check the atom name."
            )
        node["_contract_fp"] = meta.contract_fingerprint()
        node["_atom_version"] = meta.version
    return out


def assert_graph_def_current(spec: dict[str, Any]) -> None:
    """Raise :class:`GraphContractError` if any node is unstamped, names a
    missing atom, or carries a ``_contract_fp`` that no longer matches the
    registry's current contract for that atom."""
    drift: list[str] = []
    for node in spec.get("nodes", []):
        nid, atom = node.get("id"), node.get("atom")
        stored = node.get("_contract_fp")
        if not stored:
            drift.append(
                f"node {nid!r} (atom {atom!r}) is unstamped — re-seed this "
                f"graph_def to record contract fingerprints"
            )
            continue
        meta = get_atom_meta(atom) if isinstance(atom, str) else None
        if meta is None:
            drift.append(
                f"node {nid!r}: atom {atom!r} no longer exists in the registry"
            )
            continue
        current = meta.contract_fingerprint()
        if current != stored:
            drift.append(
                f"node {nid!r}: atom {atom!r} contract drifted "
                f"(stamped {stored}, current {current})"
            )
    if drift:
        raise GraphContractError(
            "FIX: stored graph_def is out of date with the atom registry:\n  - "
            + "\n  - ".join(drift)
            + "\nRe-seed the affected graph_def (re-run its seeder/migration) so "
            "the stamps match the current atom contracts."
        )


def is_graph_def_fully_unstamped(spec: dict[str, Any]) -> bool:
    """True iff ``spec`` has at least one node and **no** node carries a truthy
    ``_contract_fp``.

    A *partially*-stamped graph (some nodes stamped, some not) returns
    ``False`` — only never-stamped graphs are eligible for a baseline stamp.
    This is the safety pivot for the boot-time self-heal
    (:func:`ensure_active_graph_defs_stamped`): re-stamping a row that already
    carries fingerprints would silently overwrite — and thus mask — the
    genuine atom-contract drift that :func:`assert_graph_def_current` exists to
    catch. A deliberate graph_def reseed writes the raw spec with *every* node
    unstamped, which is exactly the shape this predicate accepts.

    Falsy fingerprints (``""``/``None``) count as unstamped, mirroring
    :func:`assert_graph_def_current`, which rejects any falsy ``_contract_fp``.
    """
    nodes = spec.get("nodes") if isinstance(spec, dict) else None
    if not isinstance(nodes, list) or not nodes:
        return False
    return all(isinstance(n, dict) and not n.get("_contract_fp") for n in nodes)


async def ensure_active_graph_defs_stamped(pool: Any) -> int:
    """Baseline-stamp every active ``pipeline_templates`` row that is *fully*
    unstamped, leaving already-stamped rows untouched. Returns the number of
    rows stamped (poindexter#755).

    Boot-time self-heal: graph_def *reseed* migrations write the raw spec via
    ``json.dumps(CANONICAL_BLOG_GRAPH_DEF)`` with no ``stamp_graph_def`` call —
    deliberately, so they stay importable in the migrations-smoke env (no atom
    registry). That un-stamps the active row, which then trips the load-time
    drift gate (:func:`assert_graph_def_current` via ``TemplateRunner.run``) on
    the next worker boot and halts every pipeline run. One-shot restamp
    migrations only fix rows extant at their time; the next reseed
    re-introduces the outage. Running this after migrations on every boot makes
    the fix recurrence-proof.

    Safe w.r.t. drift detection: a row that carries *any* fingerprint is left
    alone (see :func:`is_graph_def_fully_unstamped`), so the gate still catches
    real atom-contract drift. Only never-stamped rows — which the gate would
    reject outright — get a baseline stamp from the live registry, exactly what
    a deliberate reseed intends.

    Import-guarded: discovery walks ``modules.content.atoms.*`` which pulls
    runtime deps that may be absent (a minimal coordinator deploy, smoke). If
    the registry is unavailable we log and no-op (returning 0) rather than
    block startup.
    """
    try:
        from services.atom_registry import discover  # noqa: PLC0415

        discover()
    except Exception as exc:  # noqa: BLE001 — no registry ⇒ nothing to stamp
        logger.warning(
            "ensure_active_graph_defs_stamped: atom registry unavailable, "
            "skipping (%s)",
            exc,
        )
        return 0

    stamped = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug, graph_def FROM pipeline_templates WHERE active = true"
        )
        for row in rows:
            raw = row["graph_def"]
            spec = json.loads(raw) if isinstance(raw, str) else raw
            if not is_graph_def_fully_unstamped(spec):
                # Already stamped (or empty/malformed) — leave it so the
                # drift gate stays meaningful for this row.
                continue
            try:
                stamped_spec = stamp_graph_def(spec)
            except Exception as exc:  # noqa: BLE001 — skip un-stampable row
                logger.warning(
                    "ensure_active_graph_defs_stamped: slug=%s could not be "
                    "stamped (%s)",
                    row["slug"],
                    exc,
                )
                continue
            await conn.execute(
                "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
                "updated_at = NOW() WHERE slug = $2 AND active = true",
                json.dumps(stamped_spec),
                row["slug"],
            )
            stamped += 1

    if stamped:
        logger.info(
            "ensure_active_graph_defs_stamped: baseline-stamped %d "
            "fully-unstamped active graph_def(s)",
            stamped,
        )
    else:
        logger.debug(
            "ensure_active_graph_defs_stamped: no fully-unstamped active "
            "graph_def(s) to stamp"
        )
    return stamped


def graph_signature(spec: dict[str, Any]) -> str:
    """12-hex digest over node ``(id, _contract_fp)`` pairs + edges — identifies
    the exact graph a checkpoint was produced under (poindexter#755)."""
    nodes = sorted(
        (n.get("id"), n.get("_contract_fp")) for n in spec.get("nodes", [])
    )
    edges = sorted(
        (e.get("from"), e.get("to"))
        for e in spec.get("edges", [])
        if isinstance(e, dict)
    )
    blob = json.dumps(
        {"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Cache: persist successful compositions as named templates
# ---------------------------------------------------------------------------


async def cache_template(pool: Any, spec: dict[str, Any]) -> str:
    """Persist a validated spec to ``pipeline_templates``.

    Returns the slug under which it was registered. The slug is
    derived from ``spec['name']`` with safe-char normalization.
    Subsequent compose() calls with matching intents can short-circuit
    by looking up an existing slug instead of calling the LLM.

    Phase 4: this writes to a new ``pipeline_templates.graph_def``
    JSONB column. The migration ships separately when this lands.
    """
    import re as _re

    raw_name = (spec.get("name") or "").strip()
    slug = _re.sub(r"[^a-z0-9_]+", "_", raw_name.lower()).strip("_") or "architect_composed"

    description = (spec.get("description") or "").strip()

    try:
        # Stamp contract fingerprints so the load-time gate can detect drift
        # (poindexter#755). stamp_graph_def raises if a node names an unknown
        # atom; keeping it inside the try preserves cache_template's
        # best-effort contract (log and skip, never block architect output).
        payload = json.dumps(stamp_graph_def(spec), default=str)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pipeline_templates
                  (slug, name, description, version, active, graph_def, created_by)
                VALUES ($1, $2, $3, 1, true, $4::jsonb, 'architect_llm')
                ON CONFLICT (slug) DO UPDATE
                  SET graph_def   = EXCLUDED.graph_def,
                      description = EXCLUDED.description,
                      updated_at  = NOW()
                """,
                slug, raw_name or slug, description, payload,
            )
    except Exception as exc:
        # The pipeline_templates table is created in a future migration;
        # surface the failure but don't block the architect output —
        # callers can still execute the spec via build_graph_from_spec.
        logger.warning(
            "[architect] failed to cache template %r: %s", slug, exc,
        )

    return slug


__all__ = [
    "ArchitectResult",
    "GraphContractError",
    "assert_graph_def_current",
    "build_graph_from_spec",
    "cache_template",
    "compose",
    "graph_signature",
    "stamp_graph_def",
]

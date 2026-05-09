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

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#364.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.graph import END, StateGraph

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

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc

logger = logging.getLogger(__name__)


# Prompt key in UnifiedPromptManager + prompt_templates table. YAML
# default lives at prompts/atoms.yaml; runtime overrides come from the
# prompt_templates DB row. Per feedback_prompts_must_be_db_configurable.
_PROMPT_KEY = "atoms.pipeline_architect.system_prompt"


def _resolve_system_prompt() -> str:
    """Pull the architect system prompt via UnifiedPromptManager."""
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(_PROMPT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[pipeline_architect] prompt_manager lookup for %r failed (%s) — "
            "using inline fallback",
            _PROMPT_KEY, exc,
        )
        return _ARCHITECT_SYSTEM_PROMPT_FALLBACK


_ARCHITECT_SYSTEM_PROMPT_FALLBACK = """\
You are the Glad Labs pipeline architect. Given an INTENT (high-level
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
   character is `{` and the last character is `}`.

JSON SCHEMA:

{
  "name": "<short_snake_slug>",
  "description": "<one-sentence purpose>",
  "entry": "<node_id_of_entry>",
  "nodes": [
    {"id": "<unique_node_id>", "atom": "<atom_name_from_catalog>",
     "config": {<optional state seed values for this node>}}
  ],
  "edges": [
    {"from": "<node_id>", "to": "<node_id_or_'END'>"}
  ]
}

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
    context: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> ArchitectResult:
    """Ask the local writer LLM to compose a graph for the given intent.

    Retries up to ``max_attempts`` times when validation fails, feeding
    the structured "FIX:..." error messages back into the prompt so
    the LLM can self-correct (per ``feedback_design_for_llm_consumers``
    — error messages are the LLM's primary repair signal). Returns the
    last :class:`ArchitectResult`; ok=True on success, ok=False with
    accumulated errors on persistent failure.
    """
    catalog_text = to_catalog_text()
    if not catalog_text.strip():
        return ArchitectResult(
            ok=False,
            errors=["FIX: atom registry is empty — call atom_registry.discover() before compose()"],
        )

    site_config = site_config

    model = (
        site_config.get("pipeline_architect_model")
        or site_config.get("pipeline_writer_model", "glm-4.7-5090:latest")
        or "glm-4.7-5090:latest"
    ).removeprefix("ollama/")

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
            f"{_resolve_system_prompt()}\n\n"
            f"---\n\n"
            f"{base_user_prompt}{retry_block}\n\n"
            f"---\n\n"
            f"Output one JSON object. The first character is `{{` and "
            f"the last character is `}}`."
        )

        raw = await _ollama_chat_text(full_prompt, model=model)
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


def _validate_spec(spec: dict[str, Any]) -> tuple[bool, list[str]]:
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
            src, dst = e["from"], e["to"]
            if dst != "END":
                adj.setdefault(src, []).append(dst)
        if _has_cycle(adj):
            errors.append(
                "FIX: the graph contains a cycle. Every edge must move "
                "the pipeline forward; remove edges that loop back to "
                "an earlier node. Pipelines must be DAGs."
            )

    return (not errors), errors


def _has_cycle(adj: dict[str, list[str]]) -> bool:
    """Tarjan-ish DFS cycle detection. White/gray/black coloring."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in adj}

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
# Compile: spec → LangGraph factory
# ---------------------------------------------------------------------------


def build_graph_from_spec(
    spec: dict[str, Any],
    *,
    pool: Any,
    record_sink: list | None = None,
) -> StateGraph:
    """Compile a validated spec into an executable LangGraph.

    Caller MUST validate the spec first (via :func:`compose` or
    :func:`_validate_spec` directly). This function trusts the spec
    and will raise on missing atoms / unresolvable references.

    Atoms are resolved through the registry. Stage-typed atoms (the
    legacy 12 stages) are wrapped via ``make_stage_node``; pure atoms
    are wrapped with a tiny adapter that records to ``record_sink``
    on the same shape as stage nodes.
    """
    from plugins.registry import get_core_samples

    g: StateGraph = StateGraph(PipelineState)
    stages_by_name = {s.name: s for s in get_core_samples().get("stages", [])}

    nodes = spec["nodes"]
    edges = spec.get("edges") or []

    # Build node map.
    node_ids: list[str] = []
    for n in nodes:
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
            g.add_node(nid, make_stage_node(stage, pool, record_sink=record_sink))
        else:
            if meta is None:
                raise KeyError(f"unknown atom {atom_name!r} in node {nid!r}")
            run_fn = get_atom_callable(atom_name)
            if run_fn is None:
                raise KeyError(f"atom {atom_name!r} has no callable")
            g.add_node(nid, _wrap_atom(run_fn, atom_name, record_sink))
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

    for src, dsts in out_by_src.items():
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

    return g


def _wrap_atom(
    run_fn: Callable[..., Any],
    atom_name: str,
    record_sink: list | None,
) -> Callable[..., Any]:
    """Wrap a pure atom into the LangGraph node signature with
    record_sink integration so observability matches stage nodes.

    Mirrors the state-vs-services merge from ``make_stage_node``
    (Glad-Labs/poindexter#382): live service handles are pulled from
    ``RunnableConfig.configurable["__services__"]`` and merged into the
    atom's input dict so atoms that read ``state.get("database_service")``
    keep working unchanged. ``config`` MUST be annotated as bare
    ``RunnableConfig`` — see the matching note in ``make_stage_node``.
    """

    from langchain_core.runnables import RunnableConfig
    from services.template_runner import TemplateRunRecord, _services_from_config

    async def node(
        state: PipelineState,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        import time as _time
        t0 = _time.time()
        atom_input: dict[str, Any] = dict(state)
        for svc_key, svc_value in _services_from_config(config).items():
            atom_input.setdefault(svc_key, svc_value)
        try:
            result = await run_fn(atom_input)
            elapsed_ms = int((_time.time() - t0) * 1000)
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=atom_name, ok=True,
                        detail=f"{len(str(result.get('content','') or ''))} chars",
                        elapsed_ms=elapsed_ms,
                    )
                )
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            elapsed_ms = int((_time.time() - t0) * 1000)
            logger.exception("[architect] atom %s raised: %s", atom_name, exc)
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=atom_name, ok=False,
                        detail=f"raised {type(exc).__name__}: {exc}",
                        halted=True, elapsed_ms=elapsed_ms,
                    )
                )
            return {"_halt": True, "_halt_reason": f"{atom_name}: {exc}"}

    node.__name__ = f"atom_node_{atom_name.replace('.', '_')}"
    return node


# ---------------------------------------------------------------------------
# Local-LLM transport (mirrors atoms.narrate_bundle._ollama_chat_text)
# ---------------------------------------------------------------------------


async def _ollama_chat_text(prompt: str, model: str) -> str:
    """Plain-text Ollama call. Matches the helper in
    ``atoms.narrate_bundle`` — they should converge into a shared
    ``capability_router`` resolver in Phase 2.
    """
    import httpx
    site_config = site_config

    base_url = (
        site_config.get("local_llm_api_url", "http://localhost:11434").rstrip("/")
    )
    timeout = site_config.get_float("pipeline_architect_timeout_seconds", 120.0)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return (data.get("message") or {}).get("content", "")


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
    payload = json.dumps(spec, default=str)

    try:
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
    "build_graph_from_spec",
    "cache_template",
    "compose",
]

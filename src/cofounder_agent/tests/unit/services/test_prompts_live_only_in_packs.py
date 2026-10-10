"""Prompts live only in the SKILL.md packs (2026-10-09).

Until then about twenty resolvers caught any prompt-registry error and served an
in-code copy of the prompt. The copies hid real bugs: the architect's pack prompt
was dead for weeks behind a missing format variable, and ``social.reddit_promote``
was never added to a pack, so every Reddit draft used the code's copy and nothing
said so. A missing key now raises into the step's own error handling.

Three guards:
- each resolver renders its pack prompt, and raises when the registry is down;
- every key the code asks the registry for exists in a pack (an AST scan of the
  package, so a new call site is covered the day it lands);
- no module-level prompt text in the package beyond a named allowlist.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from poindexter.services.prompt_manager import UnifiedPromptManager

_PATCH_TARGET = "poindexter.services.prompt_manager.get_prompt_manager"
_PKG = Path(__file__).resolve().parents[3] / "poindexter"


def _qa_rewrite():
    from poindexter.modules.content.atoms import qa_rewrite as m
    return m._resolve_revise_prompt(content="A draft body.", feedback="- name the GPU")


def _narrate():
    from poindexter.modules.content.atoms import narrate_bundle as m
    return m._resolve_system_prompt(None)[0]


def _architect():
    from poindexter.services import pipeline_architect as m
    return m._resolve_system_prompt(None)


def _social(key, limit):
    from poindexter.services import social_poster as m
    return m._resolve_social_prompt(
        key, company_name="Acme", char_limit=limit, url_chars=23,
        prose_budget=limit - 24, title="T", excerpt="E",
        post_url="https://example.com/p/x", hashtags="#a #b",
    )


def _ops_triage():
    from poindexter.services import firefighter_service as m
    return m._resolve_system_prompt()


def _image_caption():
    from poindexter.services import image_captioner as m
    return m._prompt(budget=125)


def _g_eval_criterion():
    from poindexter.services import deepeval_rails as m
    return m._resolve_g_eval_criterion()


def _collapse():
    from poindexter.services.integrations.handlers import retention_embeddings_collapse as m
    return m._resolve_summary_prompt_template()


def _retention_summarize():
    from poindexter.services.integrations.handlers import retention_summarize_to_table as m
    return m._resolve_summary_prompt_template()


def _affiliate():
    from poindexter.modules.content import affiliate_import as m
    return m._resolve_prompt(title="Widget Pro 9000", description="A great widget.")


def _citations():
    from poindexter.modules.content.atoms import content_llm_reconcile_citations as m
    return m._resolve_prompt(sources="- A (https://a.example)", content="Body.")


def _two_pass_revise():
    from poindexter.modules.content.atoms import two_pass_writer as m
    return m._resolve_revise_prompt(draft="Draft.", aug_block="AUG")[0]


def _two_pass_expand():
    from poindexter.modules.content.atoms import two_pass_writer as m
    return m._resolve_expand_prompt(draft="Draft.", target_length=1500, word_count=900)


def _self_consistency():
    from poindexter.services import self_consistency_rail as m
    return m._resolve_summary_prompt(topic="T", content="Body.")


def _self_review():
    from poindexter.services import self_review as m
    return m._resolve_prompt(
        "qa.self_review.contradictions_review", title="T", topic="X", draft="D",
    )


def _chat():
    from poindexter.services import chat_prompts as m
    return m.resolve_chat_prompt(m.CHAT_SYSTEM_KEY, persona_name="P", tool_names="a, b")


# (name, pack key, resolver)
_CASES = [
    ("qa_rewrite", "atoms.qa_rewrite.revise_prompt", _qa_rewrite),
    ("narrate_bundle", "atoms.narrate_bundle.system_prompt", _narrate),
    ("pipeline_architect", "atoms.pipeline_architect.system_prompt", _architect),
    ("social_twitter", "social.twitter_promote", lambda: _social("social.twitter_promote", 280)),
    ("social_linkedin", "social.linkedin_promote", lambda: _social("social.linkedin_promote", 3000)),
    ("ops_triage", "ops.triage.system_prompt", _ops_triage),
    ("image_caption", "image.caption_alt_text", _image_caption),
    ("deepeval_g_eval_criterion", "qa.deepeval_g_eval_criterion", _g_eval_criterion),
    ("collapse", "memory.collapse_old_embeddings.summary", _collapse),
    ("retention_summarize", "ops.retention.summarize_to_table", _retention_summarize),
    ("affiliate_derive_keywords", "task.affiliate_derive_keywords", _affiliate),
    ("llm_reconcile_citations", "atoms.content.llm_reconcile_citations", _citations),
    ("two_pass_revise", "atoms.two_pass_writer.revise_prompt", _two_pass_revise),
    ("two_pass_expand", "atoms.two_pass_writer.expand_prompt", _two_pass_expand),
    ("self_consistency", "qa.self_consistency.summarize", _self_consistency),
    ("self_review", "qa.self_review.contradictions_review", _self_review),
    ("chat_system", "chat.system", _chat),
]
_IDS = [c[0] for c in _CASES]


@pytest.mark.unit
@pytest.mark.parametrize("name,key,call", _CASES, ids=_IDS)
def test_resolver_renders_its_pack_prompt(name, key, call):
    assert key in UnifiedPromptManager().prompts, f"{name}: {key} is in no SKILL.md pack"
    text = call()
    assert isinstance(text, str) and len(text.strip()) > 20, f"{name}: empty prompt"


@pytest.mark.unit
@pytest.mark.parametrize("name,key,call", _CASES, ids=_IDS)
def test_a_registry_failure_raises_instead_of_serving_a_copy(name, key, call):
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        with pytest.raises(RuntimeError):
            call()


# --- package-wide guards -------------------------------------------------------

_LOOKUPS = {"get_prompt", "get_prompt_resolution", "_resolve_template_with_meta"}


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _requested_keys() -> dict[str, set[str]]:
    """Every prompt key the package passes to the registry by literal or by a
    module-level string constant."""
    found: dict[str, set[str]] = {}
    for path in _PKG.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = _module_string_constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in _LOOKUPS:
                continue
            arg = node.args[0]
            key = arg.value if isinstance(arg, ast.Constant) else consts.get(getattr(arg, "id", ""))
            if isinstance(key, str) and re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", key):
                found.setdefault(key, set()).add(path.name)
    return found


@pytest.mark.unit
def test_every_requested_prompt_key_exists_in_a_pack():
    requested = _requested_keys()
    assert len(requested) >= 30, f"scan found only {len(requested)} keys — scanner broke?"
    registered = set(UnifiedPromptManager().prompts)
    missing = {k: sorted(v) for k, v in requested.items() if k not in registered}
    assert not missing, f"prompt keys requested by code but in no SKILL.md pack: {missing}"


# Module-level text that looks like an LLM prompt. Each entry is a deliberate,
# reviewed exception; anything new must go in a SKILL.md pack instead.
_PROMPT_TEXT_ALLOWLIST = {
    # The voice agent's container ships only the poindexter package, not
    # skills/, so these two copies are its only prompts until the packs ship
    # inside the package (follow-up issue). Voice is parked.
    ("voice_prompts.py", "_EMMA_SYSTEM_FALLBACK"),
    ("voice_prompts.py", "_CLAUDE_BRIDGE_TTS_FALLBACK"),
    # Operator overlay: seed values written into niche/settings rows, not prompts
    # read at call time.
    ("operator_overrides.py", "_VOICE_AGENT_SYSTEM_PROMPT"),
    ("operator_overrides.py", "_STARTER_BLOG_OSS_PROMPT"),
    ("operator_overrides.py", "_GLAD_LABS_WRITER_PROMPT"),
    ("operator_overrides.py", "_DEV_DIARY_OSS_PROMPT"),
    ("operator_overrides.py", "_DEV_DIARY_WRITER_PROMPT"),
    # Eval fixtures, not production prompts.
    ("critic.py", "_DELIBERATION_PREAMBLE"),
    ("retrieval.py", "_QUESTION_PROMPT"),
}
_PROMPTISH = re.compile(
    r"\byou are (a|an|the)\b|\brespond with only\b|\breturn only\b|\boutput only\b|"
    r"\breply with only\b|\banswer with one word\b",
    re.IGNORECASE,
)


@pytest.mark.unit
def test_no_new_prompt_text_in_the_package():
    offenders = []
    scanned = 0
    for path in _PKG.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names or node.value is None:
                continue
            text = " ".join(
                n.value for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            )
            if len(text) > 100 and _PROMPTISH.search(text) and (path.name, names[0]) not in _PROMPT_TEXT_ALLOWLIST:
                offenders.append(f"{path.relative_to(_PKG)}:{node.lineno} {names[0]}")
    assert scanned > 300, "scan floor: the package was not found"
    assert not offenders, (
        "prompt text in code — move it to a SKILL.md pack and resolve it with "
        f"get_prompt(): {offenders}"
    )

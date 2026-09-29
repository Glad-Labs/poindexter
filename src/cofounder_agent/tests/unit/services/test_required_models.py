"""The quick start pulls exactly the models the default pipeline calls.

The model tags are never written twice: services/required_models.py names the
ROLES (settings keys), the seeded defaults name the models, and this test
derives the tags and holds README.md's and docs/quickstart.mdx's
``ollama pull`` lines to them. Change a default model and this fails until
the docs say so — the drift that left the README pulling ``qwen3:8b`` (used
by nothing) while omitting models the pipeline called.
"""

from __future__ import annotations

import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from poindexter.services.required_models import (
    OPTIONAL_MODEL_KEYS,
    PIPELINE_MODEL_KEYS,
    ollama_tag,
    pull_command,
    required_models,
)

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "start-stack.sh").is_file()
)
_PULL = re.compile(r"\bollama\s+pull\s+([A-Za-z0-9._:/-]+)")


def _seed_parsers():
    """The seed readers settings_seed_value_drift_lint.py already maintains."""
    path = _REPO_ROOT / "scripts" / "ci" / "settings_seed_value_drift_lint.py"
    spec = spec_from_file_location("seed_value_drift_lint_for_models", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fresh_install_settings() -> dict[str, str]:
    """What a fresh `setup --auto` seeds: the baseline, then DEFAULTS for the rest."""
    lint = _seed_parsers()
    settings = dict(lint._defaults())
    settings.update(lint._baseline())
    return settings


def _quick_start_pulls(text: str) -> list[str]:
    """``ollama pull`` tags in the first ```bash block under ## Quick start."""
    section = text.split("## Quick start", 1)[1]
    block = section.split("```bash", 1)[1].split("```", 1)[0]
    return _PULL.findall(block)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ollama/gemma3:27b", "gemma3:27b"),
        ("ollama_chat/phi4:14b", "phi4:14b"),
        ("nomic-embed-text", "nomic-embed-text"),
        ("auto", None),
        ("", None),
        (None, None),
        ("anthropic/claude-sonnet-5", None),
        ("cross-encoder/ms-marco-MiniLM-L-6-v2", None),
    ],
)
def test_ollama_tag(value, expected):
    assert ollama_tag(value) == expected


def test_required_models_dedupes_in_key_order():
    settings = {"a": "ollama/x:1", "b": "x:1", "c": "ollama/y:2", "d": "auto"}
    assert required_models(settings, keys=("a", "b", "c", "d", "missing")) == ["x:1", "y:2"]
    assert pull_command(["x:1", "y:2"]) == "ollama pull x:1 && ollama pull y:2"


def test_every_role_is_seeded_with_an_ollama_model():
    """A role with no seeded model would make the derivation silently shorter."""
    settings = _fresh_install_settings()
    for key in (*PIPELINE_MODEL_KEYS, *OPTIONAL_MODEL_KEYS):
        assert ollama_tag(settings.get(key)), f"{key} is not seeded with an Ollama model: {settings.get(key)!r}"


def test_a_model_is_never_both_required_and_optional():
    settings = _fresh_install_settings()
    required = set(required_models(settings))
    optional = set(required_models(settings, keys=OPTIONAL_MODEL_KEYS))
    assert not required & optional, f"{sorted(required & optional)} listed as both"
    assert optional, "no optional models derived — the optional table would be empty"


def test_readme_pulls_exactly_the_default_pipeline_models():
    expected = required_models(_fresh_install_settings())
    readme = _quick_start_pulls((_REPO_ROOT / "README.md").read_text(encoding="utf-8"))
    assert readme == expected, (
        f"README quick start pulls {readme}, but the default pipeline calls {expected} "
        "(services/required_models.py roles, seeded defaults). Update the README's "
        f"`ollama pull` line to: {pull_command(expected)}"
    )


def test_mintlify_quickstart_pulls_the_same_models():
    expected = required_models(_fresh_install_settings())
    mdx = (_REPO_ROOT / "docs" / "quickstart.mdx").read_text(encoding="utf-8")
    assert _PULL.findall(mdx) == expected


@pytest.mark.parametrize("page", ["README.md", "docs/quickstart.mdx"])
def test_optional_models_are_named_in_the_docs(page):
    """The optional table is derived too: every optional tag appears, so a
    changed default (e.g. a new vision model) cannot leave the docs stale."""
    text = (_REPO_ROOT / page).read_text(encoding="utf-8")
    settings = _fresh_install_settings()
    missing = [t for t in required_models(settings, keys=OPTIONAL_MODEL_KEYS) if t not in text]
    assert not missing, f"{page} does not mention the optional model(s) {missing}"


def test_readme_optional_table_is_exactly_the_derived_optional_models():
    """The E2E driver reads the README's optional table to know which refused
    models are documented (and so tolerated) — so the table must be the derived
    optional set, no more and no fewer."""
    lint_path = _REPO_ROOT / "scripts" / "ci" / "quickstart_e2e.py"
    spec = spec_from_file_location("quickstart_e2e_for_optional", lint_path)
    assert spec is not None and spec.loader is not None
    driver = module_from_spec(spec)
    sys.modules[spec.name] = driver
    spec.loader.exec_module(driver)

    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    expected = required_models(_fresh_install_settings(), keys=OPTIONAL_MODEL_KEYS)
    assert driver.optional_models(readme) == expected, (
        "README's 'Optional' model table must list exactly the models "
        f"{expected} (services/required_models.py OPTIONAL_MODEL_KEYS), in that order."
    )

"""Unit tests for ``UnifiedPromptManager`` Langfuse fall-through paths.

The Langfuse stack is the operator's preferred prompt-edit surface
(per CLAUDE.md and feedback_prompts_must_be_db_configurable). It must
fall through gracefully to the YAML defaults when:

  - The langfuse package isn't installed (OSS distribution path).
  - Credentials are missing in ``app_settings`` (fresh checkout).
  - The Langfuse host is unreachable (live outage / credential rotation).
  - A specific prompt isn't synced yet to Langfuse.

Silent breakage in any of these paths means operator edits land in
Langfuse but production keeps serving the old YAML version. These
tests catch the regression where the fall-through chain stops working.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.prompt_manager import UnifiedPromptManager


# --------------------------------------------------------------------------- #
# _init_langfuse_client — lazy build, fail-safe when prerequisites missing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_load_from_db_captures_site_config_for_lazy_langfuse_init():
    """``load_from_db`` is the worker-startup hook. It must capture
    site_config + pre-fetch the Langfuse secret so the sync get_prompt
    path can read them later without awaiting.
    """
    sc = MagicMock()
    sc.get_secret = AsyncMock(return_value="sk-lf-test")
    pm = UnifiedPromptManager()
    rows_loaded = await pm.load_from_db(pool=None, site_config=sc)
    assert rows_loaded == 0  # DB layer is gone; return type stable
    assert pm._site_config is sc
    assert pm._langfuse_secret_key == "sk-lf-test"
    sc.get_secret.assert_awaited_once_with("langfuse_secret_key", "")


@pytest.mark.asyncio
async def test_load_from_db_swallows_secret_fetch_exception():
    """If the DB is briefly unreachable during startup, prompt_manager
    must NOT abort — it should fall through to YAML on subsequent
    get_prompt calls. Operator-visible failures are routed through the
    explicit Langfuse-callback path, not the prompt-load path.
    """
    sc = MagicMock()
    sc.get_secret = AsyncMock(side_effect=RuntimeError("db down"))
    pm = UnifiedPromptManager()
    # Should NOT raise — we'd lose worker startup if it did.
    rows = await pm.load_from_db(pool=None, site_config=sc)
    assert rows == 0


def test_init_langfuse_client_returns_none_when_langfuse_not_installed(monkeypatch):
    """OSS distribution path: ``langfuse`` package not installed. The
    method must return None so the caller falls through to YAML, NOT
    raise ImportError up the call stack on every prompt fetch.
    """
    pm = UnifiedPromptManager()
    pm._site_config = MagicMock()
    # Block ``from langfuse import Langfuse`` by clearing the cached
    # module + planting a meta path finder that raises ImportError for it.
    monkeypatch.setitem(sys.modules, "langfuse", None)

    # Build expects from langfuse import Langfuse → ImportError → None.
    client = pm._init_langfuse_client()
    assert client is None


def test_init_langfuse_client_returns_none_when_credentials_missing(monkeypatch):
    """If the operator hasn't populated langfuse_host /
    langfuse_public_key / langfuse_secret_key, return None and log
    once at INFO. Fresh checkout path.
    """
    fake_langfuse_module = MagicMock(name="langfuse")
    fake_langfuse_module.Langfuse = MagicMock()
    monkeypatch.setitem(sys.modules, "langfuse", fake_langfuse_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": ""  # all keys empty
    pm._site_config = sc
    pm._langfuse_secret_key = ""

    client = pm._init_langfuse_client()
    assert client is None
    # Crucially, did NOT instantiate Langfuse — would have hit a network
    # auth on the wrong host otherwise.
    fake_langfuse_module.Langfuse.assert_not_called()


def test_init_langfuse_client_returns_none_when_construction_raises(monkeypatch):
    """If Langfuse() raises (credentials wrong, host unreachable on
    init), the method must demote to a warning log + return None so
    fetch falls through. Marks ``_langfuse_enabled = False`` so we
    don't keep retrying on every get_prompt call.
    """
    fake_langfuse_module = MagicMock(name="langfuse")
    fake_langfuse_module.Langfuse = MagicMock(
        side_effect=RuntimeError("auth failed"),
    )
    monkeypatch.setitem(sys.modules, "langfuse", fake_langfuse_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": "http://lf:3000",
        "langfuse_public_key": "pk-test",
    }.get(key, default)
    pm._site_config = sc
    pm._langfuse_secret_key = "sk-test"

    client = pm._init_langfuse_client()
    assert client is None
    assert pm._langfuse_enabled is False


def test_init_langfuse_client_caches_built_client(monkeypatch):
    """Second call to _init_langfuse_client returns the cached client
    without re-importing or re-constructing — cuts ~10ms off every
    get_prompt call after the first.
    """
    fake_module = MagicMock(name="langfuse")
    sentinel_client = MagicMock(name="langfuse_client_instance")
    fake_module.Langfuse = MagicMock(return_value=sentinel_client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": "http://lf:3000",
        "langfuse_public_key": "pk-test",
    }.get(key, default)
    pm._site_config = sc
    pm._langfuse_secret_key = "sk-test"

    first = pm._init_langfuse_client()
    second = pm._init_langfuse_client()
    assert first is sentinel_client
    assert second is first
    # Construction happened exactly once
    assert fake_module.Langfuse.call_count == 1


# --------------------------------------------------------------------------- #
# _fetch_from_langfuse — error swallowing + caller fall-through
# --------------------------------------------------------------------------- #


def test_fetch_from_langfuse_returns_none_when_disabled():
    """``_langfuse_enabled = False`` is the short-circuit set when a
    prior init failed. Subsequent fetches must skip the API call.
    """
    pm = UnifiedPromptManager()
    pm._langfuse_enabled = False
    assert pm._fetch_from_langfuse("any.key") is None


def test_fetch_from_langfuse_returns_none_on_lookup_exception(monkeypatch):
    """Network failures, prompt-not-yet-synced, expired credentials —
    all must demote to debug log + None so caller falls through to
    YAML. A KeyError raised here would crash every get_prompt call.
    """
    fake_module = MagicMock(name="langfuse")
    client = MagicMock()
    client.get_prompt = MagicMock(side_effect=RuntimeError("404 not found"))
    fake_module.Langfuse = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": "http://lf:3000",
        "langfuse_public_key": "pk-test",
    }.get(key, default)
    pm._site_config = sc
    pm._langfuse_secret_key = "sk-test"

    result = pm._fetch_from_langfuse("blog_generation.initial_draft")
    assert result is None


def test_fetch_from_langfuse_returns_string_template_on_hit(monkeypatch):
    """Happy path — Langfuse returns a Prompt object with a string
    .prompt field. Must be returned as-is.
    """
    fake_module = MagicMock(name="langfuse")
    client = MagicMock()
    prompt_obj = MagicMock()
    prompt_obj.prompt = "OVERRIDDEN: hello {topic}"
    client.get_prompt = MagicMock(return_value=prompt_obj)
    fake_module.Langfuse = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": "http://lf:3000",
        "langfuse_public_key": "pk-test",
    }.get(key, default)
    pm._site_config = sc
    pm._langfuse_secret_key = "sk-test"

    result = pm._fetch_from_langfuse("seo.generate_title")
    assert result == "OVERRIDDEN: hello {topic}"


def test_fetch_from_langfuse_flattens_chat_prompt_to_single_string(monkeypatch):
    """Langfuse supports chat-prompt shape (a list of role/content
    dicts). Our consumer expects a single string template — flatten
    to ``role: content\n\nrole: content`` so existing callers don't
    have to learn about the chat shape.
    """
    fake_module = MagicMock(name="langfuse")
    client = MagicMock()
    prompt_obj = MagicMock()
    prompt_obj.prompt = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Write about {topic}."},
    ]
    client.get_prompt = MagicMock(return_value=prompt_obj)
    fake_module.Langfuse = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": "http://lf:3000",
        "langfuse_public_key": "pk-test",
    }.get(key, default)
    pm._site_config = sc
    pm._langfuse_secret_key = "sk-test"

    result = pm._fetch_from_langfuse("blog_generation.blog_system_prompt")
    assert result is not None
    assert "system: You are a helpful assistant." in result
    assert "user: Write about {topic}." in result


def test_fetch_from_langfuse_returns_none_on_empty_string_prompt(monkeypatch):
    """An accidentally-empty Langfuse prompt must NOT override the YAML
    default. Returning empty would silently disable the prompt in prod.
    """
    fake_module = MagicMock(name="langfuse")
    client = MagicMock()
    prompt_obj = MagicMock()
    prompt_obj.prompt = "   "  # whitespace only
    client.get_prompt = MagicMock(return_value=prompt_obj)
    fake_module.Langfuse = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": "http://lf:3000",
        "langfuse_public_key": "pk-test",
    }.get(key, default)
    pm._site_config = sc
    pm._langfuse_secret_key = "sk-test"

    result = pm._fetch_from_langfuse("seo.generate_title")
    assert result is None


# --------------------------------------------------------------------------- #
# get_prompt integration: Langfuse override beats YAML; fall through on miss
# --------------------------------------------------------------------------- #


def test_get_prompt_falls_through_to_yaml_when_langfuse_disabled():
    """The OSS path: Langfuse never wired up, YAML defaults serve every
    request. This is the most common path for fresh checkouts.
    """
    pm = UnifiedPromptManager()
    pm._langfuse_enabled = False  # short-circuits Langfuse lookup
    # Should NOT raise — falls through to the YAML-loaded prompt
    result = pm.get_prompt("seo.generate_title", topic="AI healthcare")
    assert "AI healthcare" in result


def test_get_prompt_uses_langfuse_override_when_present(monkeypatch):
    """When Langfuse returns a template, it MUST win over YAML —
    that's the whole point of the override layer (per
    feedback_prompts_must_be_db_configurable).
    """
    fake_module = MagicMock(name="langfuse")
    client = MagicMock()
    prompt_obj = MagicMock()
    prompt_obj.prompt = "Custom Langfuse-edited prompt about {topic}"
    client.get_prompt = MagicMock(return_value=prompt_obj)
    fake_module.Langfuse = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    pm = UnifiedPromptManager()
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": "http://lf:3000",
        "langfuse_public_key": "pk-test",
    }.get(key, default)
    pm._site_config = sc
    pm._langfuse_secret_key = "sk-test"

    result = pm.get_prompt("seo.generate_title", topic="hardware")
    assert result == "Custom Langfuse-edited prompt about hardware"

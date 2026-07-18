"""Unit tests — CLI SiteConfig-load visibility (4 modules, one shape).

Silent-excepts OLD-debt burn-down (batch 25). ``posts create`` builds a
``SiteConfig`` and swallows a failed load:

    site_cfg = SiteConfig(pool=pool)
    try:
        await site_cfg.load(pool)
    except Exception:
        pass

An unloaded ``SiteConfig`` is not empty-and-obvious — every later
``site_cfg.get(key, default)`` quietly returns the **code default** instead of
the operator's tuned app_settings value. In this command that silently
redirects ``--media`` resolution (``default_media_to_generate``) and the
idempotency switch (``cli_post_create_idempotency_enabled``, whose code
default is ``"true"``).

Scope honestly: the global ``default_media_to_generate`` seeds to ``''``, so
an operator who never tuned it sees no behavioural difference. The gap is that
one who DID tune it gets the default silently — and the CLI is the primary
operator UI, so a command quietly ignoring configuration is exactly the case
that must speak up.

The warning goes to stderr so stdout stays clean for scripting.

**Four modules share this exact shape** and are fixed together for
consistency: ``posts`` (inline in the create command) and the
``_make_site_config`` / ``_load_site_config`` helpers in ``approval``,
``publish_approval`` and ``schedule``. Two of those helpers carry a comment
justifying the CATCH ("fall back to an empty config so gate-list still
works") — but justifying the catch is not the same as telling the operator
their configuration was ignored, so the comment does not settle it. Concretely
``approval gate-list`` with an empty config can report gates as disabled when
they are in fact enabled: a wrong answer presented as fact.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

import poindexter.cli.posts as posts

pytestmark = pytest.mark.unit


def test_create_warns_when_site_config_load_fails(monkeypatch):
    """A swallowed SiteConfig load must be announced — otherwise the command
    silently falls back to code defaults for every setting it reads."""

    pool = MagicMock()
    pool.close = AsyncMock()
    monkeypatch.setattr(posts, "_make_gate_pool", AsyncMock(return_value=pool))

    class _BrokenSiteConfig:
        def __init__(self, *_a, **_k):
            pass

        async def load(self, _pool):
            raise RuntimeError("app_settings unreachable")

        def get(self, _key, default=""):
            return default

    # posts.py imports SiteConfig function-locally (posts.py:548), so the
    # patch has to land on the source module, not on posts'.
    import services.site_config as sc_mod

    monkeypatch.setattr(sc_mod, "SiteConfig", _BrokenSiteConfig)

    res = CliRunner().invoke(
        posts.posts_group,
        ["create", "--title", "Test Post"],
        input="# Test Post\n\nSome body text.\n",
    )

    # The command may still fail further down on the mock pool — irrelevant.
    # What matters is that the config-load failure was announced first.
    combined = (res.stderr or "").lower()
    assert "config" in combined or "app_settings" in combined or "default" in combined, (
        "a swallowed SiteConfig load silently substitutes code defaults for "
        f"every tuned setting — it must warn; got stderr: {res.stderr!r}"
    )


@pytest.mark.parametrize(
    ("module_path", "helper_name"),
    [
        ("poindexter.cli.approval", "_make_site_config"),
        ("poindexter.cli.publish_approval", "_make_site_config"),
        ("poindexter.cli.schedule", "_load_site_config"),
    ],
)
def test_site_config_helpers_warn_on_load_failure(
    module_path, helper_name, monkeypatch, capsys,
):
    """The three helper-shaped twins of the posts case must warn too.

    Fixed together deliberately: shipping a warning in one and staying silent
    in three structurally identical siblings would be an inconsistency, not a
    judgement call.
    """
    import asyncio
    import importlib

    import services.site_config as sc_mod

    class _BrokenSiteConfig:
        def __init__(self, *_a, **_k):
            pass

        async def load(self, _pool):
            raise RuntimeError("app_settings unreachable")

        def get(self, _key, default=""):
            return default

    monkeypatch.setattr(sc_mod, "SiteConfig", _BrokenSiteConfig)

    mod = importlib.import_module(module_path)
    helper = getattr(mod, helper_name)

    # Degraded-but-working is the contract: it must still return a config.
    cfg = asyncio.run(helper(MagicMock()))
    assert cfg is not None

    err = capsys.readouterr().err.lower()
    assert "config" in err or "app_settings" in err or "default" in err, (
        f"{module_path}.{helper_name}: a swallowed load silently substitutes "
        f"code defaults for every setting — it must warn; got stderr: {err!r}"
    )

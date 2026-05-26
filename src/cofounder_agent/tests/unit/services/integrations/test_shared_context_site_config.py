"""Pin the shared_context SiteConfig setter/getter pair.

Regression class: PR #514 (2026-05-20) introduced
``operator_notify._resolve_site_config`` which imports
``get_site_config`` from :mod:`services.integrations.shared_context`,
but the setter/getter pair was never added there. Every call landed in
the bare ``except`` and returned ``None``; ``outbound_dispatcher.deliver``
then received ``site_config=None``; ``secret_resolver`` short-circuited
with "no site_config in scope — treating as unconfigured"; and the
``discord_ops`` / ``telegram_ops`` handlers raised "no webhook URL".

The existing pin in
``test_operator_notify_site_config_fallback.py`` mocked over
``_resolve_site_config`` itself, so the broken import never tripped.
This test exercises the *real* import path so a future regression that
removes / renames the symbol fails the build.
"""

from __future__ import annotations

import pytest

from services.integrations import shared_context
from services.integrations.operator_notify import _resolve_site_config


@pytest.fixture(autouse=True)
def _clear_shared_context():
    """Reset shared_context between tests so order doesn't leak state."""
    shared_context.clear_site_config()
    yield
    shared_context.clear_site_config()


@pytest.mark.unit
def test_shared_context_exposes_set_and_get_site_config():
    """The setter/getter pair must exist as importable symbols.

    A simple ``hasattr`` on a fresh import catches the PR #514 regression
    shape exactly: the module's public API surface lost the symbols that
    a downstream import was pinned against.
    """
    assert hasattr(shared_context, "set_site_config"), (
        "services.integrations.shared_context must expose set_site_config "
        "for the integrations framework to register the lifespan-bound "
        "SiteConfig. Restoring this symbol closes the 2026-05-26 "
        "discord_ops 'no webhook URL' regression."
    )
    assert hasattr(shared_context, "get_site_config"), (
        "services.integrations.shared_context must expose get_site_config "
        "for operator_notify._resolve_site_config() to find the lifespan-"
        "bound SiteConfig. Without it every operator notification falls "
        "back to ``site_config=None`` and the secret_resolver short-circuits."
    )
    assert callable(shared_context.set_site_config)
    assert callable(shared_context.get_site_config)


@pytest.mark.unit
def test_shared_context_site_config_round_trip():
    """A value set via set_site_config must be retrievable via get_site_config.

    Pins the contract end-to-end so a future refactor that splits the
    storage into separate modules can't silently break the pair.
    """
    sentinel = object()
    assert shared_context.get_site_config() is None  # cleared by fixture
    shared_context.set_site_config(sentinel)
    assert shared_context.get_site_config() is sentinel


@pytest.mark.unit
def test_resolve_site_config_returns_registered_instance():
    """operator_notify._resolve_site_config must hit the real shared_context.

    Without mocking the helper itself — this is the path that was
    silently broken from 2026-05-20 to 2026-05-26. Registering a sentinel
    via the real setter and confirming the helper returns it pins the
    full wiring: import resolves, getter is called, value flows through.
    """
    sentinel = object()
    shared_context.set_site_config(sentinel)
    resolved = _resolve_site_config()
    assert resolved is sentinel, (
        "_resolve_site_config() must return the SiteConfig registered "
        "via shared_context.set_site_config(). If this fails, the "
        "discord_ops/telegram_ops notification path is broken and the "
        "secret_resolver will short-circuit on 'no site_config in scope'."
    )


@pytest.mark.unit
def test_resolve_site_config_returns_none_when_unset():
    """Unset state — early boot, tests, CLI one-shots — must return None cleanly.

    The notify_operator framework path treats None as "framework not yet
    wired" and continues; the legacy fallback path treats None as
    "cannot resolve discord_ops_webhook_url" and logs at debug. Either
    way the helper must not raise.
    """
    assert shared_context.get_site_config() is None
    assert _resolve_site_config() is None

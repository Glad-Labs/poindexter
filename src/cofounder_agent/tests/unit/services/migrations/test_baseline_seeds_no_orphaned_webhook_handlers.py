"""Regression gate: the retired inbound ``webhook.*`` handler surface stays retired.

The three inbound webhook handlers — ``alertmanager_dispatch``,
``revenue_event_writer``, ``subscriber_event_writer`` — were orphaned when the
generic inbound ``services/integrations/webhook_dispatcher.py`` was deleted
(2026-05-09). Nothing dispatched them afterward: ``registry.dispatch("webhook",
…)`` has no caller anywhere, and the live inbound webhooks are served by
dedicated routes — ``routes/alertmanager_webhook_routes.py`` for Alertmanager
and ``routes/external_webhooks.py`` for Lemon Squeezy + Resend. The three
handler modules were registered by ``handlers/__init__.py::load_all`` for
side effect only and never invoked.

They (and their vestigial ``webhook_endpoints`` inbound seed rows) were deleted.
This gate hard-fails if any is reintroduced — a re-run of a seed extractor, or a
copy-paste of the old handler pattern, would otherwise quietly resurrect dead
code + config that references handlers no dispatcher invokes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# handler_name values that used to live in webhook_endpoints (direction=inbound).
_RETIRED_HANDLER_NAMES = (
    "alertmanager_dispatch",
    "revenue_event_writer",
    "subscriber_event_writer",
)
# The handler module files that registered them.
_RETIRED_MODULES = (
    "webhook_alertmanager",
    "webhook_revenue",
    "webhook_subscriber",
)

# tests/unit/services/migrations/ -> src/cofounder_agent
_REPO_SRC = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (
        _REPO_SRC / "services" / "migrations" / "0000_baseline.seeds.sql"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("module_name", _RETIRED_MODULES)
def test_orphaned_webhook_handler_module_is_deleted(module_name: str) -> None:
    """The dead handler module file must stay gone.

    If the file returns, ``load_all()`` would register a ``webhook`` handler
    that no dispatcher ever calls (the inbound dispatcher was deleted
    2026-05-09). Live inbound webhooks are bespoke routes.
    """
    path = (
        _REPO_SRC / "services" / "integrations" / "handlers" / f"{module_name}.py"
    )
    assert not path.exists(), (
        f"{module_name}.py was retired — it registered an orphaned webhook.* "
        "handler with no dispatcher. Live inbound webhooks are served by "
        "routes/alertmanager_webhook_routes.py and routes/external_webhooks.py. "
        "Do not re-add it."
    )


@pytest.mark.parametrize("handler_name", _RETIRED_HANDLER_NAMES)
def test_no_inbound_webhook_endpoint_seed_for_retired_handler(
    handler_name: str, baseline_seeds_text: str
) -> None:
    """No ``webhook_endpoints`` seed row may name a retired inbound handler.

    Outbound rows (``discord_ops`` / ``telegram_ops`` → ``apprise_notify``) are
    unaffected — only the inbound rows for the deleted handlers were removed.
    """
    pattern = re.compile(
        r"INSERT INTO webhook_endpoints[^;]*'" + re.escape(handler_name) + r"'[^;]*;",
        re.DOTALL,
    )
    match = pattern.search(baseline_seeds_text)
    assert match is None, (
        f"baseline seeds still contain a webhook_endpoints row naming the retired "
        f"handler '{handler_name}'. The inbound webhook.* handler surface was "
        "retired (its dispatcher was deleted 2026-05-09; live inbound webhooks "
        "are dedicated routes). Remove the seed row."
    )

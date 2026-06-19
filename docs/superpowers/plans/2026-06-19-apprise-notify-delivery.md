# Apprise Notify-Delivery Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two hand-written operator-notify handlers (`outbound_discord`, `outbound_telegram`) with one generic, data-driven `outbound_apprise` handler, so future notification channels become a row insert rather than a new handler module.

**Architecture:** A single `@register_handler("outbound", "apprise_notify")` handler reads an `apprise_url` template from the integration row's `config` JSONB, substitutes `{secret}` (resolved via the existing `secret_key_ref` → `app_settings` path) plus any `{config-key}`, hands the URL to Apprise, and notifies off-thread. The `telegram_ops` and `discord_ops` `webhook_endpoints` rows are re-pointed to this handler. Telegram's `send_/edit_telegram_message` Bot API helpers are retained (the pipeline edit-streaming path needs them); only the fire-and-forget `telegram_post` handler is removed. Caller surface (`notify_operator`, the dispatcher, routing) is unchanged.

**Tech Stack:** Python 3.13, FastAPI/asyncpg backend, `apprise` (already shipped transitively via Prefect), the in-tree integrations registry/dispatcher framework, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-19-apprise-notify-delivery-design.md`

## Global Constraints

_Every task's requirements implicitly include this section._

- **Dependency floor:** `apprise = ">=1.11,<2.0"`. Already resolved in `poetry.lock` at `1.11.0` via Prefect — declaring it is making an existing dependency explicit, not adding a new one.
- **Scope:** ONLY the `telegram_ops` + `discord_ops` rows. Do not touch `vercel_isr`, `alertmanager`, or `publishing_*`.
- **Retain Telegram streaming helpers:** `send_telegram_message`, `edit_telegram_message`, `_parse_bot_result` in `outbound_telegram.py` stay byte-for-byte (imported by `services/pipeline_streaming.py`). Only the `telegram_post` `@register_handler` function is removed.
- **Async-everywhere:** Apprise is synchronous internally; the handler MUST offload via `asyncio.to_thread` so it never blocks the event loop.
- **Fail loud:** Missing `config.apprise_url`, an unresolved `{secret}`, or an unknown `{placeholder}` raises with a remediation message naming the row (`feedback_no_silent_defaults`). No silent default.
- **Leak-neutral:** The Telegram `chat_id` stays in `config.chat_id` and is referenced via the `{chat_id}` placeholder. Never copy the literal `chat_id` into a new string (`feedback_no_operator_info_to_public_repo`).
- **Behavior identical today:** same two channels, same message text.
- **Seeds vs migrations:** fresh-install row state → `0000_baseline.seeds.sql`; existing-install mutation → one timestamped migration (`feedback_seed_data_in_baseline_not_new_migrations`).
- **Docs + tests required** for every change (`feedback_docs_and_tests_default`).
- **CI green is the gate** (`feedback_ci_is_the_review_gate`). Branch-only; no direct main pushes (`feedback_all_changes_via_pr`).

**Running tests in this worktree:** from the backend package dir:
`cd src/cofounder_agent && poetry run pytest <path> -v`. If `poetry install` hasn't run in the worktree, fall back to the repo-root venv with `PYTHONPATH=src/cofounder_agent` per `reference_worktree_test_invocation`.

---

### Task 1: Declare the `apprise` dependency

**Files:**

- Modify: `src/cofounder_agent/pyproject.toml` (the `[tool.poetry.dependencies]` table)
- Modify: `src/cofounder_agent/poetry.lock` (regenerated)

**Interfaces:**

- Produces: an importable top-level `apprise` package available to handler code.

- [ ] **Step 1: Add the dependency declaration**

In `src/cofounder_agent/pyproject.toml`, immediately after the `apscheduler = "^3.11.2"` line (in `[tool.poetry.dependencies]`), add:

```toml
# Apprise — unified push-notification library backing the generic
# services/integrations/handlers/outbound_apprise.py handler. Already
# resolved in poetry.lock at 1.11.0 via Prefect's dependency tree;
# declared directly here so the outbound notify path owns its floor
# instead of riding a transitive pin. BSD-2-Clause (permissive).
apprise = ">=1.11,<2.0"
```

- [ ] **Step 2: Re-lock without bumping other packages**

Run: `cd src/cofounder_agent && poetry lock --no-update`
Expected: completes successfully ("Resolving dependencies... Writing lock file").

- [ ] **Step 3: Verify the lock diff is limited to apprise metadata**

Run: `cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/wonderful-franklin-e4605b" && git diff --stat src/cofounder_agent/poetry.lock`
Expected: only `poetry.lock` changed. Inspect with `git diff src/cofounder_agent/poetry.lock` — the only semantic change is `apprise` moving into the `main` group's direct-dependency set + the content-hash. If any unrelated package version changes, revert (`git checkout src/cofounder_agent/poetry.lock`) and re-run with `poetry lock --no-update` again; do not accept incidental bumps.

- [ ] **Step 4: Verify apprise imports**

Run: `cd src/cofounder_agent && poetry run python -c "import apprise; print(apprise.__version__)"`
Expected: prints `1.11.0` (or a `1.x` ≥ 1.11).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/pyproject.toml src/cofounder_agent/poetry.lock
git commit -m "build(deps): declare apprise as a direct dependency

Already resolved at 1.11.0 via Prefect; declare it directly so the
outbound notify path owns its floor. Backs the upcoming
outbound_apprise handler.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Implement the generic `outbound_apprise` handler

**Files:**

- Create: `src/cofounder_agent/services/integrations/handlers/outbound_apprise.py`
- Test: `src/cofounder_agent/tests/unit/services/integrations/handlers/test_outbound_apprise.py`

**Interfaces:**

- Consumes: `services.integrations.registry.register_handler(surface, name)`; `services.integrations.secret_resolver.resolve_secret(row, site_config) -> str | None`.
- Produces: `apprise_notify(payload, *, site_config, row, pool) -> dict[str, Any]` registered under `"outbound.apprise_notify"`. Returns `{"delivered": True}` on success; raises `RuntimeError`/`TypeError` on any failure (the dispatcher records + re-raises).

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/services/integrations/handlers/test_outbound_apprise.py`:

```python
"""Unit tests for the generic outbound.apprise_notify handler."""

from __future__ import annotations

import pytest

from services.integrations.handlers import outbound_apprise


class _FakeSiteConfig:
    def __init__(self, secrets: dict[str, str] | None = None):
        self._secrets = secrets or {}

    async def get_secret(self, key: str, default: str = "") -> str | None:
        return self._secrets.get(key)


class _FakeApprise:
    """Stand-in for apprise.Apprise — records add()/notify() calls."""

    instances: list["_FakeApprise"] = []
    add_returns = True
    notify_returns = True

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.notified: list[dict[str, str]] = []
        _FakeApprise.instances.append(self)

    def add(self, url: str) -> bool:
        self.urls.append(url)
        return type(self).add_returns

    def notify(self, body: str = "", title: str = "", **kwargs) -> bool:
        self.notified.append({"body": body, "title": title})
        return type(self).notify_returns


@pytest.fixture(autouse=True)
def _patch_apprise(monkeypatch):
    _FakeApprise.instances = []
    _FakeApprise.add_returns = True
    _FakeApprise.notify_returns = True
    monkeypatch.setattr(outbound_apprise.apprise, "Apprise", _FakeApprise)
    return _FakeApprise


@pytest.mark.asyncio
async def test_telegram_template_builds_url(_patch_apprise):
    row = {
        "name": "telegram_ops",
        "secret_key_ref": "telegram_bot_token",
        "config": {"chat_id": "42", "apprise_url": "tgram://{secret}/{chat_id}/"},
    }
    result = await outbound_apprise.apprise_notify(
        "hi",
        site_config=_FakeSiteConfig({"telegram_bot_token": "TOKEN"}),
        row=row,
        pool=None,
    )
    assert result == {"delivered": True}
    assert _patch_apprise.instances[0].urls == ["tgram://TOKEN/42/"]
    assert _patch_apprise.instances[0].notified == [{"body": "hi", "title": ""}]


@pytest.mark.asyncio
async def test_discord_secret_passthrough(_patch_apprise):
    webhook = "https://discord.com/api/webhooks/123/abc"
    row = {
        "name": "discord_ops",
        "secret_key_ref": "discord_ops_webhook_url",
        "config": {"apprise_url": "{secret}"},
    }
    await outbound_apprise.apprise_notify(
        {"content": "ping"},
        site_config=_FakeSiteConfig({"discord_ops_webhook_url": webhook}),
        row=row,
        pool=None,
    )
    assert _patch_apprise.instances[0].urls == [webhook]
    assert _patch_apprise.instances[0].notified[0]["body"] == "ping"


@pytest.mark.asyncio
async def test_dict_payload_text_key(_patch_apprise):
    row = {"name": "r", "config": {"apprise_url": "json://localhost"}}
    await outbound_apprise.apprise_notify(
        {"text": "from-text-key"},
        site_config=_FakeSiteConfig(),
        row=row,
        pool=None,
    )
    assert _patch_apprise.instances[0].notified[0]["body"] == "from-text-key"


@pytest.mark.asyncio
async def test_missing_apprise_url_raises():
    row = {"name": "telegram_ops", "config": {}}
    with pytest.raises(RuntimeError, match="apprise_url"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_unknown_placeholder_raises():
    row = {"name": "r", "config": {"apprise_url": "x://{nope}"}}
    with pytest.raises(RuntimeError, match="nope"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_secret_placeholder_without_secret_raises():
    # No secret_key_ref on the row -> resolve_secret returns None.
    row = {"name": "r", "config": {"apprise_url": "{secret}"}}
    with pytest.raises(RuntimeError, match="secret"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_add_rejects_url_raises(_patch_apprise):
    _patch_apprise.add_returns = False
    row = {"name": "r", "config": {"apprise_url": "not-a-real-scheme://"}}
    with pytest.raises(RuntimeError, match="rejected"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_notify_failure_raises(_patch_apprise):
    _patch_apprise.notify_returns = False
    row = {"name": "r", "config": {"apprise_url": "json://localhost"}}
    with pytest.raises(RuntimeError, match="delivery failed"):
        await outbound_apprise.apprise_notify(
            "x", site_config=_FakeSiteConfig(), row=row, pool=None
        )


@pytest.mark.asyncio
async def test_invalid_payload_type_raises():
    row = {"name": "r", "config": {"apprise_url": "json://localhost"}}
    with pytest.raises(TypeError):
        await outbound_apprise.apprise_notify(
            12345, site_config=_FakeSiteConfig(), row=row, pool=None
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_outbound_apprise.py -v`
Expected: collection error / FAIL — `ModuleNotFoundError: No module named 'services.integrations.handlers.outbound_apprise'`.

- [ ] **Step 3: Implement the handler**

Create `src/cofounder_agent/services/integrations/handlers/outbound_apprise.py`:

```python
"""Handler: ``outbound.apprise_notify`` — generic notification delivery.

One data-driven handler that replaces the per-channel ``outbound_discord``
and ``outbound_telegram`` notify handlers. The destination is described by
the row's ``config.apprise_url`` template; adding a new channel is a row
insert, not a new module.

Template substitution (``config.apprise_url``):

- ``{secret}``      -> resolved via ``secret_key_ref`` (see secret_resolver)
- ``{<config-key>}``-> any other key in the row's ``config`` (e.g. ``{chat_id}``)

Examples:

- telegram_ops : ``tgram://{secret}/{chat_id}/`` + ``secret_key_ref=telegram_bot_token``
- discord_ops  : ``{secret}``                    + ``secret_key_ref=discord_ops_webhook_url``
  (Apprise accepts the native ``https://discord.com/api/webhooks/ID/TOKEN`` URL directly.)

Payload: a plain ``str`` or a dict carrying one of
``content`` / ``text`` / ``body`` / ``message`` (so existing callers and the
old Discord/Telegram payload shapes keep working unchanged).

Apprise is synchronous internally, so delivery is offloaded with
``asyncio.to_thread`` to keep the event loop free. Any failure raises — the
outbound dispatcher records it on the row and re-raises.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import apprise

from services.integrations.registry import register_handler
from services.integrations.secret_resolver import resolve_secret
from services.logger_config import get_logger

logger = get_logger(__name__)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_BODY_KEYS = ("content", "text", "body", "message")


def _coerce_body(payload: Any) -> str:
    """Reduce the supported payload shapes to a notification body string."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in _BODY_KEYS:
            value = payload.get(key)
            if value:
                return str(value)
        raise TypeError(
            "apprise_notify: dict payload needs one of "
            f"{_BODY_KEYS!r} with a non-empty value"
        )
    raise TypeError(
        f"apprise_notify: payload must be str or dict, got {type(payload).__name__}"
    )


def _build_url(template: str, secret: str | None, config: dict[str, Any], row_name: Any) -> str:
    """Substitute ``{secret}`` + ``{config-key}`` placeholders in the template."""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "secret":
            if not secret:
                raise RuntimeError(
                    f"apprise_notify: row {row_name!r} apprise_url references "
                    "{secret} but no secret resolved — check secret_key_ref and "
                    "the referenced app_settings key"
                )
            return secret
        if token in config:
            return str(config[token])
        raise RuntimeError(
            f"apprise_notify: row {row_name!r} apprise_url references "
            f"{{{token}}} but row config has no such key"
        )

    return _PLACEHOLDER.sub(_replace, template)


@register_handler("outbound", "apprise_notify")
async def apprise_notify(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,  # noqa: ARG001 — handler protocol signature; pool unused here
) -> dict[str, Any]:
    """Deliver ``payload`` to the destination described by ``row.config.apprise_url``."""
    row_name = row.get("name")
    config = row.get("config") or {}
    if not isinstance(config, dict):
        raise RuntimeError(
            f"apprise_notify: row {row_name!r} config is not an object"
        )
    template = config.get("apprise_url")
    if not template:
        raise RuntimeError(
            f"apprise_notify: row {row_name!r} config.apprise_url is required"
        )

    body = _coerce_body(payload)
    secret = await resolve_secret(row, site_config)
    url = _build_url(str(template), secret, config, row_name)

    aobj = apprise.Apprise()
    if not aobj.add(url):
        raise RuntimeError(
            f"apprise_notify: Apprise rejected the URL for row {row_name!r} "
            "(malformed apprise_url?)"
        )

    # Apprise's notify() is blocking (requests-based); offload it.
    delivered = await asyncio.to_thread(aobj.notify, body=body, title="")
    if not delivered:
        raise RuntimeError(
            f"apprise_notify: delivery failed for row {row_name!r}"
        )

    logger.debug("[outbound.apprise_notify] delivered via row %s", row_name)
    return {"delivered": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_outbound_apprise.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/integrations/handlers/outbound_apprise.py src/cofounder_agent/tests/unit/services/integrations/handlers/test_outbound_apprise.py
git commit -m "feat(integrations): generic outbound.apprise_notify handler

Data-driven notification delivery: an apprise_url template on the row
with {secret}/{config-key} substitution, offloaded via asyncio.to_thread.
Fail-loud on missing url/secret/unknown placeholder.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Register the handler, retire Discord, shrink Telegram

**Files:**

- Modify: `src/cofounder_agent/services/integrations/handlers/__init__.py`
- Delete: `src/cofounder_agent/services/integrations/handlers/outbound_discord.py`
- Modify: `src/cofounder_agent/services/integrations/handlers/outbound_telegram.py`
- Modify: `src/cofounder_agent/services/http_client.py` (remove the `outbound_discord` entry from `WIRED_HTTP_CLIENT_MODULES`)
- Test: `src/cofounder_agent/tests/unit/services/integrations/handlers/test_handler_registration.py` (new)
- Modify: `src/cofounder_agent/tests/unit/services/test_outbound_handlers.py` (drop Discord/Telegram handler cases)

**Interfaces:**

- Consumes: `apprise_notify` from Task 2; `services.integrations.registry.lookup` / `registered_names`.
- Produces: `outbound.apprise_notify` discoverable after `load_all()`; `outbound.discord_post` / `outbound.telegram_post` no longer registered; `outbound_telegram.send_telegram_message` / `edit_telegram_message` still importable.

- [ ] **Step 1: Write the failing registration test**

Create `src/cofounder_agent/tests/unit/services/integrations/handlers/test_handler_registration.py`:

```python
"""Registration surface after the Apprise cutover."""

from __future__ import annotations

import importlib

import pytest

from services.integrations import registry
from services.integrations.handlers import load_all


def test_apprise_handler_registered_and_legacy_gone():
    load_all()
    outbound = registry.registered_names("outbound")
    assert "outbound.apprise_notify" in outbound
    assert "outbound.discord_post" not in outbound
    assert "outbound.telegram_post" not in outbound


def test_discord_module_deleted():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            "services.integrations.handlers.outbound_discord"
        )


def test_telegram_streaming_helpers_retained():
    mod = importlib.import_module(
        "services.integrations.handlers.outbound_telegram"
    )
    assert hasattr(mod, "send_telegram_message")
    assert hasattr(mod, "edit_telegram_message")
    assert not hasattr(mod, "telegram_post")


def test_outbound_discord_not_in_http_client_wiring():
    from services.http_client import WIRED_HTTP_CLIENT_MODULES

    assert (
        "services.integrations.handlers.outbound_discord"
        not in WIRED_HTTP_CLIENT_MODULES
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_handler_registration.py -v`
Expected: FAIL — `outbound.apprise_notify` not in registry / `outbound_discord` still imports / `telegram_post` still present.

- [ ] **Step 3: Register apprise + drop the two legacy imports in `__init__.py`**

In `src/cofounder_agent/services/integrations/handlers/__init__.py`:

Change the surface reference comment line:

```python
    # outbound.* surface: outbound_discord, outbound_telegram, outbound_vercel_isr
```

to:

```python
    # outbound.* surface: outbound_apprise, outbound_vercel_isr
    #   (outbound_telegram retains Bot API helpers for pipeline_streaming but
    #    no longer registers a handler; outbound_discord was deleted — both
    #    superseded by the generic apprise_notify handler.)
```

In the `from services.integrations.handlers import (...)` block, remove the
`outbound_discord,` and `outbound_telegram,` lines and add `outbound_apprise,`
(keep the block alphabetized):

```python
    from services.integrations.handlers import (  # noqa: F401
        outbound_apprise,
        outbound_vercel_isr,
        publishing_mastodon,
        publishing_youtube,
        retention_downsample,
        retention_summarize_to_table,
        retention_ttl_prune,
        tap_builtin_topic_source,
        tap_corsair_csv,
        tap_external_metrics_writer,
        tap_singer_subprocess,
        webhook_alertmanager,
        webhook_revenue,
        webhook_subscriber,
    )
```

And update the `_ = (...)` reference tuple to match (replace
`outbound_discord, outbound_telegram, outbound_vercel_isr,` with
`outbound_apprise, outbound_vercel_isr,`):

```python
    _ = (
        webhook_alertmanager, webhook_revenue, webhook_subscriber,
        outbound_apprise, outbound_vercel_isr,
        publishing_mastodon, publishing_youtube,
        retention_downsample, retention_summarize_to_table, retention_ttl_prune,
        tap_builtin_topic_source, tap_corsair_csv,
        tap_external_metrics_writer, tap_singer_subprocess,
    )
```

- [ ] **Step 4: Delete the Discord handler**

Run: `git rm src/cofounder_agent/services/integrations/handlers/outbound_discord.py`

- [ ] **Step 5: Remove the Discord entry from the http_client wiring**

In `src/cofounder_agent/services/http_client.py`, delete the line inside `WIRED_HTTP_CLIENT_MODULES`:

```python
    "services.integrations.handlers.outbound_discord",
```

(Leave `"services.integrations.operator_notify"` — its own shared-client fallback path is unrelated and still used.)

- [ ] **Step 6: Shrink the Telegram handler to its helpers**

In `src/cofounder_agent/services/integrations/handlers/outbound_telegram.py`:

- Delete the entire `telegram_post` function and its `@register_handler("outbound", "telegram_post")` decorator (lines from `@register_handler(...)` through the end of `telegram_post`'s `return {...}`).
- Remove the now-unused imports `from services.integrations.registry import register_handler` and `from services.integrations.secret_resolver import resolve_secret`.
- Keep `import httpx`, `import logging`, the `logger`, and the three helpers `send_telegram_message`, `edit_telegram_message`, `_parse_bot_result` exactly as-is.
- Update the module docstring's first paragraph to:

```python
"""Telegram Bot API helpers (``sendMessage`` / ``editMessageText``).

These low-level helpers back the pipeline edit-streaming path
(``services/pipeline_streaming.py``), which sends a message and then
edits it in place as a run progresses — behaviour Apprise's
fire-and-forget model cannot express. Operator notifications now go
through the generic ``outbound.apprise_notify`` handler, so this module
no longer registers a dispatcher handler; it is a helper library only.
"""
```

- [ ] **Step 7: Trim the legacy handler tests**

In `src/cofounder_agent/tests/unit/services/test_outbound_handlers.py`:

- Change the import block to only:

```python
from services.integrations.handlers import outbound_vercel_isr
```

- Delete the entire `class TestDiscordPost:` and `class TestTelegramPost:` blocks (the `class TestVercelIsr:` block and the `_FakeSiteConfig` / `_CapturingTransport` / `patch_httpx` fixtures stay — `TestVercelIsr` still uses them).

- [ ] **Step 8: Run the registration + outbound + streaming tests**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/integrations/handlers/test_handler_registration.py tests/unit/services/test_outbound_handlers.py tests/unit/services/test_pipeline_streaming.py -v`
Expected: all PASS (registration asserts apprise present + legacy gone; vercel cases pass; streaming helper tests unaffected).

- [ ] **Step 9: Commit**

```bash
git add -A src/cofounder_agent/services/integrations/handlers/__init__.py src/cofounder_agent/services/integrations/handlers/outbound_telegram.py src/cofounder_agent/services/http_client.py src/cofounder_agent/tests/unit/services/integrations/handlers/test_handler_registration.py src/cofounder_agent/tests/unit/services/test_outbound_handlers.py
git commit -m "refactor(integrations): route ops notify through apprise_notify

Register the generic apprise handler; delete outbound_discord (+ its
http_client wiring entry); shrink outbound_telegram to its Bot API
streaming helpers (telegram_post handler removed, send_/edit_ kept for
pipeline_streaming).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Re-point the two rows (baseline seed + migration)

**Files:**

- Modify: `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` (the `telegram_ops` + `discord_ops` `webhook_endpoints` INSERT rows)
- Create: `src/cofounder_agent/services/migrations/20260619_000000_repoint_ops_webhooks_to_apprise.py`
- Test: `src/cofounder_agent/tests/integration_db/test_apprise_repoint_migration.py` (new)

**Interfaces:**

- Consumes: the registered `apprise_notify` handler (Task 3); live `webhook_endpoints` rows whose `config` is a JSONB **object** (verified).
- Produces: `telegram_ops` + `discord_ops` rows with `handler_name='apprise_notify'` and a `config.apprise_url` template.

- [ ] **Step 1: Edit the baseline seed rows (fresh installs)**

In `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql`, replace the `discord_ops` row (id `812c6760-3523-498f-a125-3a7e937f4a2e`) with:

```sql
-- discord_ops: generic apprise handler. config is a proper JSONB object
-- (not the legacy double-encoded string form) because apprise_notify reads
-- config.apprise_url as a dict key. Apprise accepts the native Discord
-- webhook URL directly, so apprise_url is just the {secret} passthrough.
INSERT INTO webhook_endpoints (id, name, direction, handler_name, path, url, signing_algorithm, secret_key_ref, event_filter, enabled, config, metadata) VALUES ('812c6760-3523-498f-a125-3a7e937f4a2e', 'discord_ops', 'outbound', 'apprise_notify', NULL, NULL, 'none', 'discord_ops_webhook_url', '"{}"'::jsonb, true, '{"apprise_url": "{secret}"}'::jsonb, '"{\"description\": \"Discord #ops channel webhook — operator notifications (via apprise)\", \"source_setting\": \"discord_ops_webhook_url\"}"'::jsonb) ON CONFLICT (id) DO NOTHING;
```

Replace the `telegram_ops` row (id `a38197c6-e215-4a3a-9214-1cbdd645e508`) with:

```sql
-- telegram_ops: generic apprise handler. config is a proper JSONB object;
-- chat_id stays in config and is referenced via the {chat_id} placeholder
-- (leak-neutral — no chat_id literal copied into a new string).
INSERT INTO webhook_endpoints (id, name, direction, handler_name, path, url, signing_algorithm, secret_key_ref, event_filter, enabled, config, metadata) VALUES ('a38197c6-e215-4a3a-9214-1cbdd645e508', 'telegram_ops', 'outbound', 'apprise_notify', NULL, NULL, 'bearer', 'telegram_bot_token', '"{}"'::jsonb, true, '{"chat_id": "5318613610", "apprise_url": "tgram://{secret}/{chat_id}/"}'::jsonb, '"{\"description\": \"Telegram Bot API sendMessage to the operator chat (via apprise)\"}"'::jsonb) ON CONFLICT (id) DO NOTHING;
```

(The existing `chat_id` value is preserved verbatim — this is not a new exposure. Whether a literal `chat_id` should ship in the public baseline at all is the pre-existing question flagged in the spec's Open items; out of scope here.)

- [ ] **Step 2: Write the re-point migration (existing installs)**

Create `src/cofounder_agent/services/migrations/20260619_000000_repoint_ops_webhooks_to_apprise.py`:

```python
"""Migration 20260619_000000_repoint_ops_webhooks_to_apprise: route ops notify via apprise

Re-points the two operator-notification webhook_endpoints rows from the
per-channel ``discord_post`` / ``telegram_post`` handlers to the generic
``apprise_notify`` handler, adding an ``apprise_url`` template to each row's
``config``. Live config is a JSONB object, so the ``||`` merge is safe.

- discord_ops : apprise_url = "{secret}" (native webhook URL passthrough)
- telegram_ops: apprise_url = "tgram://{secret}/{chat_id}/" (chat_id from config)

Idempotent: the WHERE clause matches only rows still on the legacy handler.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'apprise_notify',
                   config = config || '{"apprise_url": "{secret}"}'::jsonb
             WHERE name = 'discord_ops'
               AND handler_name = 'discord_post'
            """
        )
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'apprise_notify',
                   config = config || '{"apprise_url": "tgram://{secret}/{chat_id}/"}'::jsonb
             WHERE name = 'telegram_ops'
               AND handler_name = 'telegram_post'
            """
        )
    logger.info("re-pointed discord_ops + telegram_ops to apprise_notify")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'discord_post',
                   config = config - 'apprise_url'
             WHERE name = 'discord_ops'
               AND handler_name = 'apprise_notify'
            """
        )
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'telegram_post',
                   config = config - 'apprise_url'
             WHERE name = 'telegram_ops'
               AND handler_name = 'apprise_notify'
            """
        )
    logger.info("rolled back discord_ops + telegram_ops to legacy handlers")
```

- [ ] **Step 3: Lint the migration**

Run: `cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/wonderful-franklin-e4605b" && python scripts/ci/migrations_lint.py`
Expected: passes (no collisions; `up`/`down` interface present).

- [ ] **Step 4: Write the re-point assertion test**

Create `src/cofounder_agent/tests/integration_db/test_apprise_repoint_migration.py`:

```python
"""integration_db: the ops webhook rows end up on apprise_notify after migrate."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration_db


@pytest.mark.asyncio
async def test_ops_rows_repointed_to_apprise(migrated_pool):
    """After the full migration chain, both ops rows use apprise_notify with a
    valid apprise_url template, and config is a JSONB object.

    ``migrated_pool`` is the integration_db harness fixture that applies the
    full migration chain (0000_baseline + all timestamped migrations) to a
    throwaway database. If the local fixture name differs, adapt to the
    harness in tests/integration_db/conftest.py.
    """
    async with migrated_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT name, handler_name, jsonb_typeof(config) AS cfg_type,
                   config->>'apprise_url' AS apprise_url
              FROM webhook_endpoints
             WHERE name IN ('discord_ops', 'telegram_ops')
             ORDER BY name
            """
        )
    by_name = {r["name"]: r for r in rows}
    assert by_name["discord_ops"]["handler_name"] == "apprise_notify"
    assert by_name["telegram_ops"]["handler_name"] == "apprise_notify"
    assert by_name["discord_ops"]["cfg_type"] == "object"
    assert by_name["telegram_ops"]["cfg_type"] == "object"
    assert by_name["discord_ops"]["apprise_url"] == "{secret}"
    assert by_name["telegram_ops"]["apprise_url"] == "tgram://{secret}/{chat_id}/"
```

- [ ] **Step 5: Run the fresh-DB migrations smoke + the re-point test**

Run: `cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/wonderful-franklin-e4605b" && python scripts/ci/migrations_smoke.py`
Expected: applies the full chain against a fresh DB with no error.

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_apprise_repoint_migration.py -v`
Expected: PASS. If the integration_db harness/fixture is unavailable in this environment, instead verify against the live DB read-only:
`SELECT name, handler_name, config->>'apprise_url' FROM webhook_endpoints WHERE name IN ('discord_ops','telegram_ops');` after the migration runs at worker startup — both rows show `apprise_notify` + the template.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/migrations/0000_baseline.seeds.sql src/cofounder_agent/services/migrations/20260619_000000_repoint_ops_webhooks_to_apprise.py src/cofounder_agent/tests/integration_db/test_apprise_repoint_migration.py
git commit -m "feat(integrations): re-point ops webhooks to apprise_notify

Baseline seed (fresh installs, proper JSONB-object config) + timestamped
migration (existing installs, idempotent ||-merge). discord_ops uses
{secret} passthrough; telegram_ops uses tgram://{secret}/{chat_id}/.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Documentation

**Files:**

- Create: `docs/integrations/outbound_apprise.md`
- Delete: `docs/integrations/outbound_discord_post.md`, `docs/integrations/outbound_telegram_post.md`
- Modify: `docs/integrations/index.mdx`, `docs.json` (nav entries referencing the two deleted pages)

**Interfaces:**

- Consumes: nothing (docs only).
- Produces: operator-facing reference for the generic handler.

- [ ] **Step 1: Write the new handler doc**

Create `docs/integrations/outbound_apprise.md` documenting: the `apprise_notify` handler; the `config.apprise_url` template with `{secret}`/`{config-key}` substitution; the two seeded rows (`telegram_ops`, `discord_ops`) and their templates; and a "add a new channel = insert a `webhook_endpoints` row with an `apprise_url`" walkthrough (e.g. an ntfy example `ntfy://{secret}@ntfy.sh/poindexter`). Mirror the structure/length of the existing `docs/integrations/outbound_vercel_isr.md`.

- [ ] **Step 2: Remove the superseded docs + nav references**

Run:

```bash
git rm docs/integrations/outbound_discord_post.md docs/integrations/outbound_telegram_post.md
```

Then in `docs/integrations/index.mdx` and `docs.json`, replace the two removed entries (`outbound_discord_post`, `outbound_telegram_post`) with the single `outbound_apprise` entry. Search both files for `outbound_discord_post` and `outbound_telegram_post` and update every hit.

- [ ] **Step 3: Verify no dangling references remain**

Run: `cd "C:/Users/mattm/glad-labs-website/.claude/worktrees/wonderful-franklin-e4605b" && git grep -n "outbound_discord_post\|outbound_telegram_post" -- docs docs.json`
Expected: no output (all references updated). (Hits under `CHANGELOG.md` or dated `docs/audits/*` historical files are acceptable — leave those; only fix live docs + nav.)

- [ ] **Step 4: Commit**

```bash
git add -A docs/integrations docs.json
git commit -m "docs(integrations): replace discord/telegram handler docs with apprise

New channel = insert a webhook_endpoints row with an apprise_url template.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Final verification (suite, lint, behavior parity)

**Files:** none (verification only).

- [ ] **Step 1: Full backend unit suite**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit -q`
Expected: all pass, 0 failures, 0 collection errors. Investigate any failure mentioning `outbound_discord`, `telegram_post`, or handler registration before proceeding.

- [ ] **Step 2: Lint + type check the touched files**

Run: `cd src/cofounder_agent && poetry run ruff check services/integrations/handlers/outbound_apprise.py services/integrations/handlers/outbound_telegram.py services/integrations/handlers/__init__.py services/http_client.py`
Expected: no errors (in particular, no unused-import warnings in `outbound_telegram.py` after dropping `register_handler`/`resolve_secret`).

- [ ] **Step 3: Behavior-parity check (manual, on Matt's box)**

After the worker picks up the migration (restart per the deploy posture in `reference_deploy_restart_on_sync`), trigger one routine and one critical notification and confirm both arrive and read the same as before:

```bash
cd src/cofounder_agent && poetry run python -c "import asyncio; from services.integrations.operator_notify import notify_operator; asyncio.run(notify_operator('apprise parity test — routine', critical=False)); asyncio.run(notify_operator('apprise parity test — critical', critical=True))"
```

Expected: the routine message lands in Discord #ops; the critical message lands in Telegram. **Check the Discord rendering:** if it shows as an embed instead of plain content (a visual change from today), append `?format=text` to the discord row's `apprise_url`:

```sql
UPDATE webhook_endpoints SET config = config || '{"apprise_url": "{secret}?format=text"}'::jsonb WHERE name = 'discord_ops';
```

and update the baseline seed + migration template to match, then re-verify. If the rendering already matches today's plain post, no change needed.

- [ ] **Step 4: Confirm the dispatcher counters still move**

Read-only check that delivery is recorded on the rows (Grafana Integrations panels read these):

```sql
SELECT name, handler_name, total_success, last_success_at, last_error FROM webhook_endpoints WHERE name IN ('discord_ops','telegram_ops');
```

Expected: `total_success` incremented and `last_error` NULL for both after the parity test.

- [ ] **Step 5: Push the branch and open the PR**

```bash
git push -u origin claude/wonderful-franklin-e4605b
```

Open a PR against `Glad-Labs/glad-labs-stack` (the code source of truth). File the tracking issue on `Glad-Labs/poindexter` (OSS surface) and reference it in the PR body. Let CI run; merge when green per `feedback_ci_is_the_review_gate`.

---

## Self-Review

**Spec coverage:**

- Declare apprise dep → Task 1. ✓
- Generic apprise handler (Model B, `{secret}`/`{config-key}`, async offload, fail-loud, payload compat) → Task 2. ✓
- Register handler; delete `outbound_discord` + its `WIRED_HTTP_CLIENT_MODULES` entry; shrink `outbound_telegram` keeping streaming helpers → Task 3. ✓
- Re-point rows: baseline seed (proper-object config) + timestamped migration (idempotent `||` merge, verified object shape) → Task 4. ✓
- Docs swap → Task 5. ✓
- Tests for handler + registration + migration; full-suite + lint + behavior-parity gates (Discord embed mitigation, async) → Tasks 2/3/4/6. ✓
- Scope (only the two ops rows); leak-neutral chat_id; behavior identical → enforced in Global Constraints + Task 4. ✓
- Open item (literal chat_id in baseline) → preserved verbatim, flagged, out of scope. ✓

**Placeholder scan:** No TBD/TODO. Every code step has complete code; every command has expected output. The one environment-dependent point (integration_db fixture name) carries an explicit read-only fallback. ✓

**Type/name consistency:** `apprise_notify(payload, *, site_config, row, pool) -> dict` is defined in Task 2 and consumed by name in Tasks 3/4/6. `resolve_secret` return contract (None/""/plaintext) matches the `{secret}`-without-secret test. `config.apprise_url` templates (`{secret}`, `tgram://{secret}/{chat_id}/`) are identical across the handler tests, the seed, the migration, and the integration_db assertion. ✓

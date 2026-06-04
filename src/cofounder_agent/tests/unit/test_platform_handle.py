"""Unit tests for the ``Platform`` handle contract + capability-scoping (Wave 0).

Self-contained: uses an in-test stub ``Platform`` (the concrete
``KernelPlatform`` / ``FakePlatform`` are Wave 1). Pins:

- the ``Platform`` Protocol is runtime-checkable and a stub satisfies it;
- ``Capability`` values match the handle attribute names they gate;
- ``ScopedPlatform`` delegates *granted* capabilities to the backing platform;
- ``ScopedPlatform`` fails loud (``CapabilityError``) on an *ungranted* one;
- the error message names the missing capability AND what was granted;
- non-capability attribute access is a plain ``AttributeError`` (the handle
  does not leak arbitrary backing attributes).
"""

from __future__ import annotations

import pytest

from plugins.platform import (
    Capability,
    CapabilityError,
    Platform,
    ScopedPlatform,
)


class _StubConfig:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _StubSecret:
    async def get(self, key: str, default: str | None = None) -> str | None:
        return f"secret::{key}"


class _StubDispatch:
    async def complete(self, *args: object, **kwargs: object) -> str:
        return "completion"


class _StubDb:
    def acquire(self) -> str:
        return "connection-ctx"


class _StubAudit:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    async def write(
        self,
        event_type: str,
        *,
        source: str,
        details: dict[str, object] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        self.rows.append(
            {
                "event_type": event_type,
                "source": source,
                "details": details or {},
                "task_id": task_id,
                "severity": severity,
            }
        )


class _StubPlatform:
    """A fully-featured in-test ``Platform`` (stands in for the Wave-1 impls)."""

    def __init__(self) -> None:
        self.config = _StubConfig({"k": "v"})
        self.secret = _StubSecret()
        self.dispatch = _StubDispatch()
        self.db = _StubDb()
        self.audit = _StubAudit()
        self.log_calls: list[tuple[str, dict[str, object]]] = []
        self.metric_calls: list[tuple[str, float, dict[str, str]]] = []
        # A non-capability attribute the handle must NOT forward.
        self.internal_state = "private"

    def log(self, message: str, /, **fields: object) -> None:
        self.log_calls.append((message, fields))

    def metric(self, name: str, value: float = 1.0, /, **labels: str) -> None:
        self.metric_calls.append((name, value, labels))


def test_stub_satisfies_platform_protocol() -> None:
    assert isinstance(_StubPlatform(), Platform)


def test_capability_values_match_handle_attr_names() -> None:
    assert {c.value for c in Capability} == {
        "config",
        "secret",
        "dispatch",
        "db",
        "log",
        "metric",
        "audit",
    }


def test_scoped_delegates_granted_sync_capability() -> None:
    backing = _StubPlatform()
    scoped = ScopedPlatform(backing, {Capability.CONFIG, Capability.DB})
    assert scoped.config.get("k") == "v"
    assert scoped.db.acquire() == "connection-ctx"


def test_scoped_delegates_granted_capability_by_identity() -> None:
    # Capabilities exposed as instance objects are forwarded as-is (identity),
    # so we don't need an event loop to prove delegation of the async ones.
    backing = _StubPlatform()
    scoped = ScopedPlatform(backing, {Capability.SECRET, Capability.DISPATCH})
    assert scoped.secret is backing.secret
    assert scoped.dispatch is backing.dispatch


def test_scoped_delegates_callable_capabilities() -> None:
    # log/metric are bound methods on the backing, so identity won't hold
    # (a fresh bound method per access) — prove delegation behaviorally.
    backing = _StubPlatform()
    scoped = ScopedPlatform(backing, {Capability.LOG, Capability.METRIC})
    scoped.log("hello", run="x")
    scoped.metric("count", 2.0, unit="n")
    assert backing.log_calls == [("hello", {"run": "x"})]
    assert backing.metric_calls == [("count", 2.0, {"unit": "n"})]


def test_scoped_blocks_ungranted_capability() -> None:
    scoped = ScopedPlatform(_StubPlatform(), {Capability.CONFIG})
    with pytest.raises(CapabilityError):
        _ = scoped.secret


def test_capability_error_names_missing_and_granted() -> None:
    scoped = ScopedPlatform(_StubPlatform(), {Capability.CONFIG})
    with pytest.raises(CapabilityError) as exc_info:
        _ = scoped.dispatch
    message = str(exc_info.value)
    assert "dispatch" in message  # the missing capability
    assert "config" in message  # what WAS granted (so the operator sees the fix)


def test_empty_grant_blocks_everything() -> None:
    scoped = ScopedPlatform(_StubPlatform(), set())
    assert scoped.granted == frozenset()
    for cap in Capability:
        with pytest.raises(CapabilityError):
            getattr(scoped, cap.value)


def test_non_capability_attribute_is_attribute_error() -> None:
    # The handle forwards capabilities only — it must not leak arbitrary
    # backing attributes, even when fully granted.
    scoped = ScopedPlatform(_StubPlatform(), set(Capability))
    with pytest.raises(AttributeError):
        _ = scoped.internal_state
    with pytest.raises(AttributeError):
        _ = scoped.not_a_capability


def test_granted_is_exposed_readonly() -> None:
    scoped = ScopedPlatform(
        _StubPlatform(), {Capability.LOG, Capability.METRIC}
    )
    assert scoped.granted == frozenset({Capability.LOG, Capability.METRIC})

"""``FakePlatform`` — the one shared in-memory ``Platform`` for module tests.

Wave 1 of Seam 1 (Glad-Labs/poindexter#667). Module authors test against this
instead of standing up the real kernel. The conformance test
(``tests/unit/test_platform_conformance.py``) pins ``FakePlatform`` and
``KernelPlatform`` to the *same* ``Platform`` contract, so a module that passes
against the fake behaves the same against the real kernel — the fake-kernel-drift
guard.

Importable from non-test code on purpose: any module's test suite can
``from plugins.fake_platform import FakePlatform``. Each capability records its
calls so a test can assert on them (e.g. ``fake.audit.writes``,
``fake.log.calls``).
"""

from __future__ import annotations

from typing import Any


class _FakeConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class _FakeSecret:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = dict(secrets)

    async def get(self, key: str, default: str | None = None) -> str | None:
        return self.secrets.get(key, default)


class _FakeDispatch:
    def __init__(self, response: Any = "") -> None:
        self.response = response
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.response


class _FakeConnection:
    def __init__(self) -> None:
        self.queries: list[tuple[Any, ...]] = []

    async def execute(self, *args: Any) -> str:
        self.queries.append(args)
        return "OK"

    async def fetch(self, *args: Any) -> list[Any]:
        self.queries.append(args)
        return []

    async def fetchrow(self, *args: Any) -> Any:
        self.queries.append(args)
        return None


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeDb:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.connection)


class _FakeLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, message: str, /, **fields: Any) -> None:
        self.calls.append((message, fields))


class _FakeMetric:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, dict[str, str]]] = []

    def __call__(self, name: str, value: float = 1.0, /, **labels: str) -> None:
        self.calls.append((name, value, labels))


class _FakeAudit:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    async def write(
        self,
        event_type: str,
        *,
        source: str,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        severity: str = "info",
    ) -> None:
        self.writes.append(
            {
                "event_type": event_type,
                "source": source,
                "details": details or {},
                "task_id": task_id,
                "severity": severity,
            }
        )


class FakePlatform:
    """In-memory ``Platform`` for tests. Structurally satisfies the Protocol;
    each capability records its calls for assertions."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        dispatch_response: Any = "",
    ) -> None:
        self._config = _FakeConfig(config or {})
        self._secret = _FakeSecret(secrets or {})
        self._dispatch = _FakeDispatch(dispatch_response)
        self._db = _FakeDb()
        self._log = _FakeLog()
        self._metric = _FakeMetric()
        self._audit = _FakeAudit()

    @property
    def config(self) -> _FakeConfig:
        return self._config

    @property
    def secret(self) -> _FakeSecret:
        return self._secret

    @property
    def dispatch(self) -> _FakeDispatch:
        return self._dispatch

    @property
    def db(self) -> _FakeDb:
        return self._db

    @property
    def log(self) -> _FakeLog:
        return self._log

    @property
    def metric(self) -> _FakeMetric:
        return self._metric

    @property
    def audit(self) -> _FakeAudit:
        return self._audit


__all__ = ["FakePlatform"]

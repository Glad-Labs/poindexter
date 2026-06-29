"""Unit tests for the LiveKit voice agent's tool surface.

Each tool in :mod:`services.voice_agent_livekit` has both a ``async def
fn(params)`` Pipecat-facing entry point and a ``_<name>_text()`` helper
that contains the actual business logic. The helpers exist precisely so
tests like these can assert on the spoken payload without faking a full
``FunctionCallParams`` (which requires an LLMService instance).

The Pipecat / LiveKit dependencies are stubbed by the existing
``test_voice_agent_service_mode`` module-level setup; importing it here
piggybacks on those stubs so this test file stays self-contained.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

# Trigger Pipecat / LiveKit stubs BEFORE importing voice_agent_livekit.
# The sibling service-mode test module's top-level call to
# ``_ensure_pipecat_stubs()`` is what makes ``import services.voice_agent_livekit``
# resolve without the real Pipecat / livekit packages installed.
# isort: off
from tests.unit.services import test_voice_agent_service_mode  # noqa: F401
import services.voice_agent_livekit as voice_agent_livekit  # noqa: E402
# isort: on


class _RecordingCallback:
    """Stand-in for Pipecat's ``FunctionCallResultCallback`` Protocol."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    async def __call__(self, result: Any, *, properties: Any = None) -> None:
        self.calls.append((result, properties))


def _fake_params(callback: _RecordingCallback, **arguments: Any) -> Any:
    """Build a duck-typed ``FunctionCallParams`` for the entry-point tests.

    Only ``params.result_callback`` and ``params.arguments`` are accessed
    by our handlers, so a SimpleNamespace is enough.
    """
    return types.SimpleNamespace(
        result_callback=callback,
        arguments=arguments,
    )


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_memory_text_summarises_top_hits(monkeypatch):
    async def _fake_get(path: str) -> dict[str, Any]:
        assert "/api/memory/search" in path
        assert "tailscale" in path
        return {
            "hits": [
                {"source_table": "memory", "text_preview": "Tailscale Funnel exposes brain to phone over MagicDNS"},
                {"source_table": "issues", "text_preview": "Tailscale-backed Telegram voice bridge documented"},
                {"source_table": "memory", "text_preview": "Use Tailscale ACLs not raw firewall rules"},
            ],
        }

    monkeypatch.setattr(voice_agent_livekit, "_worker_get", _fake_get)

    out = await voice_agent_livekit._search_memory_text("tailscale")
    assert "Top matches for tailscale" in out
    assert "from memory" in out
    assert "from issues" in out
    # Voice-first: result must not contain markdown / JSON / URLs.
    assert "{" not in out
    assert "http" not in out


@pytest.mark.asyncio
async def test_search_memory_text_handles_empty_query():
    out = await voice_agent_livekit._search_memory_text("")
    assert "phrase" in out.lower()


@pytest.mark.asyncio
async def test_search_memory_text_handles_no_hits(monkeypatch):
    async def _fake_get(_path: str) -> dict[str, Any]:
        return {"hits": []}

    monkeypatch.setattr(voice_agent_livekit, "_worker_get", _fake_get)
    out = await voice_agent_livekit._search_memory_text("obscure-topic")
    assert "nothing" in out.lower()


@pytest.mark.asyncio
async def test_search_memory_speaks_via_callback(monkeypatch):
    async def _fake_get(_path: str) -> dict[str, Any]:
        return {"hits": [{"source_table": "memory", "text_preview": "fact"}]}

    monkeypatch.setattr(voice_agent_livekit, "_worker_get", _fake_get)
    cb = _RecordingCallback()
    await voice_agent_livekit.search_memory(_fake_params(cb, query="fact"))
    assert len(cb.calls) == 1
    assert cb.calls[0][0] != "IN_PROGRESS"
    assert "fact" in cb.calls[0][0]


# ---------------------------------------------------------------------------
# list_recent_pipeline_tasks
# ---------------------------------------------------------------------------


class _FakeConn:
    """Async context-managed fake asyncpg connection.

    Backs the direct-DB voice tools that don't go through ``_worker_get``.
    Each instance is configured with ``rows`` / ``raise_on_fetch`` /
    ``raise_on_fetchrow`` etc; the close() call is recorded so tests can
    assert the connection is always released.
    """

    def __init__(
        self,
        *,
        fetch_rows: list[dict[str, Any]] | None = None,
        fetchrow_result: dict[str, Any] | None = None,
        raise_on_fetch: Exception | None = None,
        raise_on_fetchrow: Exception | None = None,
    ):
        self._fetch_rows = fetch_rows or []
        self._fetchrow_result = fetchrow_result
        self._raise_on_fetch = raise_on_fetch
        self._raise_on_fetchrow = raise_on_fetchrow
        self.closed = False

    async def fetch(self, *_a, **_kw):
        if self._raise_on_fetch:
            raise self._raise_on_fetch
        return self._fetch_rows

    async def fetchrow(self, *_a, **_kw):
        if self._raise_on_fetchrow:
            raise self._raise_on_fetchrow
        return self._fetchrow_result

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_db(monkeypatch):
    """Patch ``_connect_db`` with a fake conn for the direct-DB tools.

    Returns a setter the test calls with the fake conn it wants returned
    on the next ``_connect_db()`` invocation.
    """
    holder: dict[str, Any] = {"conn": None}

    async def _fake_connect_db():
        if holder["conn"] is None:
            raise RuntimeError("test forgot to set stub_db conn")
        return holder["conn"]

    monkeypatch.setattr(voice_agent_livekit, "_connect_db", _fake_connect_db)
    return holder


@pytest.mark.asyncio
async def test_list_recent_pipeline_tasks_text_groups_by_status(stub_db):
    stub_db["conn"] = _FakeConn(fetch_rows=[
        {"status": "completed"},
        {"status": "completed"},
        {"status": "awaiting_approval"},
        {"status": "running"},
        {"status": "running"},
    ])
    out = await voice_agent_livekit._list_recent_pipeline_tasks_text()
    assert "Latest 5 pipeline tasks" in out
    assert "2 completed" in out
    assert "2 running" in out
    assert "1 awaiting approval" in out
    # Underscores must be spelled out, not read literally.
    assert "_" not in out


@pytest.mark.asyncio
async def test_list_recent_pipeline_tasks_text_handles_empty(stub_db):
    stub_db["conn"] = _FakeConn(fetch_rows=[])
    out = await voice_agent_livekit._list_recent_pipeline_tasks_text()
    assert "no recent" in out.lower()


@pytest.mark.asyncio
async def test_list_recent_pipeline_tasks_text_handles_db_error(stub_db):
    stub_db["conn"] = _FakeConn(raise_on_fetch=RuntimeError("connection dropped"))
    out = await voice_agent_livekit._list_recent_pipeline_tasks_text()
    assert "couldn't" in out.lower() or "could not" in out.lower()


# ---------------------------------------------------------------------------
# get_audit_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_summary_text_reports_top_events(stub_db):
    stub_db["conn"] = _FakeConn(fetch_rows=[
        {"event_type": "task_started", "severity": "info", "n": 12},
        {"event_type": "qa_gate_passed", "severity": "info", "n": 8},
        {"event_type": "validator_failed", "severity": "warning", "n": 2},
    ])
    out = await voice_agent_livekit._get_audit_summary_text()
    assert "12 info task started" in out
    assert "8 info qa gate passed" in out
    assert "2 warning validator failed" in out


@pytest.mark.asyncio
async def test_get_audit_summary_text_handles_quiet_log(stub_db):
    stub_db["conn"] = _FakeConn(fetch_rows=[])
    out = await voice_agent_livekit._get_audit_summary_text()
    assert "quiet" in out.lower()


# ---------------------------------------------------------------------------
# find_similar_posts
# ---------------------------------------------------------------------------


class _FakeMemoryHit:
    def __init__(self, text_preview: str):
        self.text_preview = text_preview


@pytest.fixture
def stub_memory_client(monkeypatch):
    """Inject a poindexter.memory.client module with a controllable
    MemoryClient stub that records construction/connection/calls.
    """
    holder: dict[str, Any] = {
        "find_similar_posts_result": [],
        "store_calls": [],
        "raise_on_connect": None,
        "raise_on_find": None,
        "raise_on_store": None,
    }

    class _StubClient:
        def __init__(self, *_a, **_kw):
            self._closed = False

        async def connect(self):
            if holder["raise_on_connect"]:
                raise holder["raise_on_connect"]

        async def find_similar_posts(self, topic, *, limit=5, min_similarity=0.75):
            if holder["raise_on_find"]:
                raise holder["raise_on_find"]
            holder["last_topic"] = topic
            return holder["find_similar_posts_result"]

        async def store(self, **kwargs):
            if holder["raise_on_store"]:
                raise holder["raise_on_store"]
            holder["store_calls"].append(kwargs)
            return kwargs.get("source_id") or "memory/voice-stub"

        async def close(self):
            self._closed = True

    fake_module = types.ModuleType("poindexter.memory.client")
    fake_module.MemoryClient = _StubClient

    parent_module = types.ModuleType("poindexter.memory")
    parent_module.client = fake_module

    grandparent = sys.modules.get("poindexter") or types.ModuleType("poindexter")
    grandparent.memory = parent_module

    monkeypatch.setitem(sys.modules, "poindexter", grandparent)
    monkeypatch.setitem(sys.modules, "poindexter.memory", parent_module)
    monkeypatch.setitem(sys.modules, "poindexter.memory.client", fake_module)

    # Also short-circuit _ensure_brain_on_path (it tries to walk the
    # filesystem looking for brain/bootstrap.py and will fail under the
    # test runner's working directory).
    monkeypatch.setattr(voice_agent_livekit, "_ensure_brain_on_path", lambda: None)
    return holder


@pytest.mark.asyncio
async def test_find_similar_posts_text_summarises_titles(stub_memory_client):
    stub_memory_client["find_similar_posts_result"] = [
        _FakeMemoryHit("RTX 5090 power draw under sustained inference"),
        _FakeMemoryHit("Building a quiet 5090 rig with hardline cooling"),
        _FakeMemoryHit("5090 vs 4090 — local LLM throughput"),
    ]
    out = await voice_agent_livekit._find_similar_posts_text("RTX 5090 cooling")
    assert "RTX 5090 cooling" in out
    assert "5090" in out


@pytest.mark.asyncio
async def test_find_similar_posts_text_handles_no_hits(stub_memory_client):
    stub_memory_client["find_similar_posts_result"] = []
    out = await voice_agent_livekit._find_similar_posts_text("unique-topic")
    assert "No published posts" in out


@pytest.mark.asyncio
async def test_find_similar_posts_text_requires_topic():
    out = await voice_agent_livekit._find_similar_posts_text("")
    assert "topic" in out.lower()


# ---------------------------------------------------------------------------
# get_recent_pull_requests
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_pr_env(monkeypatch):
    """Patch the asyncpg pool + plugins.secrets so the PR tool runs without
    the real DB / GitHub. Returns an httpx response mapper the test fills.

    Repos used by the production tool now come from
    ``app_settings.voice_agent_pr_repos`` (the post-#485 cleanup).
    The fixture exposes ``responses["__repos__"]`` as the CSV string the
    fake connection returns; tests that exercise the PR-listing path set
    this alongside the URL-mapped response payloads. Default is an
    empty string so tests that don't care about repos get the "no
    repos configured" branch.
    """
    # Fake plugins.secrets.get_secret returning a token (or empty string).
    fake_secrets = types.ModuleType("plugins.secrets")

    async def _get_secret(_conn, key):
        if key == "gh_token":
            return "fake-token"
        return None

    fake_secrets.get_secret = _get_secret
    monkeypatch.setitem(sys.modules, "plugins.secrets", fake_secrets)

    # Fake brain.bootstrap so resolve_database_url returns a sentinel.
    fake_brain = types.ModuleType("brain")
    fake_bootstrap = types.ModuleType("brain.bootstrap")
    fake_bootstrap.resolve_database_url = lambda **_kw: "postgres://stub"
    fake_bootstrap.require_database_url = lambda **_kw: "postgres://stub"
    monkeypatch.setitem(sys.modules, "brain", fake_brain)
    monkeypatch.setitem(sys.modules, "brain.bootstrap", fake_bootstrap)

    # ``__captured_headers__`` collects the headers dict passed to each
    # outbound GitHub request so tests can assert the crawler User-Agent on
    # the wire. It never matches a URL needle, so the response-dispatch loop
    # skips it (same as ``__repos__``).
    responses: dict[str, Any] = {"__repos__": "", "__captured_headers__": []}

    # Fake asyncpg.create_pool returning a pool whose acquire() yields a
    # dummy connection. ``_Conn.fetchrow`` serves the
    # ``voice_agent_pr_repos`` setting from ``responses["__repos__"]`` so
    # individual tests can configure the repo list.
    class _Conn:
        async def fetchrow(self_inner, query, *args):
            if "voice_agent_pr_repos" in query:
                return {"value": responses.get("__repos__", "")}
            return None

    class _AcquireCtx:
        async def __aenter__(self_inner):
            return _Conn()

        async def __aexit__(self_inner, *_exc):
            return None

    class _Pool:
        def acquire(self_inner):
            return _AcquireCtx()

        async def close(self_inner):
            return None

    async def _create_pool(*_a, **_kw):
        return _Pool()

    fake_asyncpg = types.ModuleType("asyncpg")
    fake_asyncpg.create_pool = _create_pool
    monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)

    # Disable the brain-on-path walker (it would try to add a real path).
    monkeypatch.setattr(voice_agent_livekit, "_ensure_brain_on_path", lambda: None)

    # ``responses`` is declared above (with ``__repos__`` slot) so the
    # fake conn and fake httpx share the same mapping.

    class _FakeResp:
        def __init__(self, status_code: int, body: Any):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    class _FakeAsyncClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def get(self, url, headers=None):
            responses["__captured_headers__"].append(headers)
            for needle, response in responses.items():
                if needle in url:
                    return _FakeResp(200, response)
            return _FakeResp(404, {})

    # Patch the httpx the tool sees.
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.AsyncClient = _FakeAsyncClient
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    return responses


@pytest.mark.asyncio
async def test_get_recent_pull_requests_text_lists_top_three(stub_pr_env):
    # poindexter#485 follow-up: repos now come from
    # ``app_settings.voice_agent_pr_repos`` (CSV). Configure the stub
    # via ``__repos__`` and URL-needles via the same dict.
    stub_pr_env["__repos__"] = "test-org/repo-a,test-org/repo-b"
    stub_pr_env["test-org/repo-a/pulls"] = [
        {
            "number": 294,
            "title": "fix(voice): wire result_callback through every tool",
            "merged_at": "2026-05-06T18:00:00Z",
        },
        {
            "number": 290,
            "title": "feat(brain): self-heal on stuck tasks",
            "merged_at": "2026-05-06T08:00:00Z",
        },
    ]
    stub_pr_env["test-org/repo-b/pulls"] = [
        {
            "number": 287,
            "title": "rewrite test topics CLI",
            "merged_at": "2026-05-06T16:00:00Z",
        },
    ]

    out = await voice_agent_livekit._get_recent_pull_requests_text()
    assert "PR 294" in out
    assert "repo-a" in out
    assert "PR 287" in out
    assert "repo-b" in out
    # Spec: no URLs in spoken output.
    assert "http" not in out


@pytest.mark.asyncio
async def test_get_recent_pull_requests_text_when_nothing_merged(stub_pr_env):
    stub_pr_env["__repos__"] = "test-org/repo-a,test-org/repo-b"
    stub_pr_env["test-org/repo-a/pulls"] = [
        {"number": 100, "title": "wip", "merged_at": None},
    ]
    stub_pr_env["test-org/repo-b/pulls"] = []

    out = await voice_agent_livekit._get_recent_pull_requests_text()
    assert "No merged" in out


@pytest.mark.asyncio
async def test_get_recent_pull_requests_text_no_repos_configured(stub_pr_env):
    """poindexter#485 follow-up: when ``app_settings.voice_agent_pr_repos``
    is unset / empty, the tool reports the misconfiguration to the
    operator instead of querying GitHub. New contract — OSS-safe
    default behavior."""
    stub_pr_env["__repos__"] = ""  # explicit default for clarity
    out = await voice_agent_livekit._get_recent_pull_requests_text()
    assert "voice_agent_pr_repos" in out
    assert "app_settings" in out


@pytest.mark.asyncio
async def test_get_recent_pull_requests_sends_crawler_user_agent(stub_pr_env):
    """The GitHub PR tool identifies with the shared crawler UA
    (``utils.crawler_ua.build_crawler_ua``) rather than the bare
    ``poindexter-voice-agent`` token. This standalone tool has no reachable
    ``SiteConfig`` (the caller passes only Pipecat params and the DB conn
    closes before the UA is built) and the contact URL is moot for a
    GitHub-API client, so it sends the contact-less form — the OSS leak
    guard. Asserted on the wire via the fixture's header capture; mirrors
    the ``TestUserAgent`` pattern in ``test_check_published_links_job``.
    """
    stub_pr_env["__repos__"] = "test-org/repo-a"
    stub_pr_env["test-org/repo-a/pulls"] = [
        {"number": 1, "title": "feat: x", "merged_at": "2026-05-06T18:00:00Z"},
    ]

    await voice_agent_livekit._get_recent_pull_requests_text()

    captured = stub_pr_env["__captured_headers__"]
    assert captured  # at least one GitHub request was issued
    ua = captured[0]["User-Agent"]
    assert ua == "Mozilla/5.0 (compatible; PoindexterVoiceAgent/1.0)"


# ---------------------------------------------------------------------------
# get_brain_decisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_brain_decisions_text_summarises_recent(stub_db):
    stub_db["conn"] = _FakeConn(fetch_rows=[
        {"decision": "Restart worker after stuck task detected",
         "reasoning": "Worker idle 15 minutes; queue backed up"},
        {"decision": "Reduce concurrency to 2 due to high GPU temp",
         "reasoning": "GPU 84C above safety threshold"},
    ])
    out = await voice_agent_livekit._get_brain_decisions_text()
    assert "Recent brain decisions" in out
    assert "Restart worker" in out
    assert "Reduce concurrency" in out


@pytest.mark.asyncio
async def test_get_brain_decisions_text_handles_quiet_brain(stub_db):
    stub_db["conn"] = _FakeConn(fetch_rows=[])
    out = await voice_agent_livekit._get_brain_decisions_text()
    assert "quiet" in out.lower()


# ---------------------------------------------------------------------------
# submit_dev_diary_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_dev_diary_note_text_persists_note(stub_db):
    stub_db["conn"] = _FakeConn(fetchrow_result={"id": 42})
    out = await voice_agent_livekit._submit_dev_diary_note_text(
        "voice agent finally landed", "triumph",
    )
    assert "Saved diary note 42" in out
    assert "triumph" in out
    assert "voice agent" in out


@pytest.mark.asyncio
async def test_submit_dev_diary_note_text_rejects_unknown_mood():
    out = await voice_agent_livekit._submit_dev_diary_note_text(
        "test", "ecstatic",
    )
    assert "mood" in out.lower()
    # Must not have called the DB.
    assert "Saved diary note" not in out


@pytest.mark.asyncio
async def test_submit_dev_diary_note_text_rejects_empty():
    out = await voice_agent_livekit._submit_dev_diary_note_text("", None)
    assert "note" in out.lower()


@pytest.mark.asyncio
async def test_submit_dev_diary_note_text_handles_missing_mood(stub_db):
    stub_db["conn"] = _FakeConn(fetchrow_result={"id": 7})
    out = await voice_agent_livekit._submit_dev_diary_note_text(
        "ran the new tests, everything green", None,
    )
    assert "Saved diary note 7" in out
    assert "mood" not in out


# ---------------------------------------------------------------------------
# store_memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_memory_text_persists_via_memory_client(stub_memory_client):
    out = await voice_agent_livekit._store_memory_text(
        "Matt prefers local Ollama for content generation by default",
    )
    assert "Saved to memory" in out
    assert len(stub_memory_client["store_calls"]) == 1
    call = stub_memory_client["store_calls"][0]
    assert call["writer"] == "user"
    assert call["source_table"] == "memory"
    assert "Ollama" in call["text"]


@pytest.mark.asyncio
async def test_store_memory_text_requires_content():
    out = await voice_agent_livekit._store_memory_text("")
    assert "save" in out.lower() or "remember" in out.lower()


# ---------------------------------------------------------------------------
# Surface contract — registration order + protocol
# ---------------------------------------------------------------------------


def test_default_tools_includes_all_eleven():
    """All 11 voice tools (3 existing + 6 new RO + 2 input) must be listed.

    Registration order matters: existing first (operators have built up
    intuition for which tools fire on which phrases), then new read-only
    alphabetical, then the input-requiring tools last as a safety
    convention (input == mutating, so it's the boundary).
    """
    names = [fn.__name__ for fn in voice_agent_livekit._DEFAULT_TOOLS]
    assert names == [
        "check_pipeline_health",
        "get_published_post_count",
        "get_ai_spending_status",
        "find_similar_posts",
        "get_audit_summary",
        "get_brain_decisions",
        "get_recent_pull_requests",
        "list_recent_pipeline_tasks",
        "search_memory",
        "store_memory",
        "submit_dev_diary_note",
    ]


def test_every_tool_has_a_text_helper():
    """Spec: every ``async def fn(params)`` tool has a ``_<name>_text()``
    helper exposing the spoken-string side without the Pipecat callback.

    The two input-requiring tools (``submit_dev_diary_note``,
    ``store_memory``) take additional positional args in their helper
    form, so we only check existence + callability here.
    """
    for fn in voice_agent_livekit._DEFAULT_TOOLS:
        helper_name = f"_{fn.__name__}_text"
        helper = getattr(voice_agent_livekit, helper_name, None)
        assert helper is not None, (
            f"{fn.__name__} is missing its {helper_name} helper. "
            "Add the split so unit tests can assert on the payload "
            "without faking FunctionCallParams."
        )
        assert callable(helper)


@pytest.mark.asyncio
async def test_extract_query_arg_handles_aliases():
    """The LLM might call any of these arg names — accept all of them."""
    cb = _RecordingCallback()
    for name in ("query", "q", "topic", "text"):
        params = _fake_params(cb, **{name: "value"})
        assert voice_agent_livekit._extract_query_arg(params) == "value"


@pytest.mark.asyncio
async def test_extract_query_arg_returns_empty_when_missing():
    cb = _RecordingCallback()
    params = _fake_params(cb)
    assert voice_agent_livekit._extract_query_arg(params) == ""

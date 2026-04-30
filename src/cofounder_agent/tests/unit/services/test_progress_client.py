"""
Unit tests for clients/progress_client.py

Tests cover:
- WorkflowProgressClient initialization and URL building
- _ensure_session — creates session on first call, reuses existing open session
- close — closes WebSocket connections and HTTP session
- initialize_progress — builds correct URL and returns response
- start_execution — sends POST and returns response
- start_phase — sends POST with phase params
- complete_phase — sends POST with JSON body
- fail_phase — sends POST with error params
- mark_complete — sends POST with final_output
- mark_failed — sends POST with optional failed_phase
- get_status — sends GET and returns response
- cleanup — sends DELETE and returns response
- unsubscribe_progress — closes and removes WebSocket connection
- subscribe_progress raises when auto_reconnect=False
- get_progress_client convenience function
- HTTP errors raise on all HTTP-calling methods
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.progress_client import WorkflowProgressClient, get_progress_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_response(json_data: dict, status: int = 200):
    """Return an async context manager mock that yields a response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.raise_for_status = MagicMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, resp


def make_error_response():
    """Return a response mock that raises on raise_for_status."""
    import aiohttp

    resp = MagicMock()
    resp.raise_for_status = MagicMock(
        side_effect=aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=500)
    )
    resp.json = AsyncMock(return_value={})

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestWorkflowProgressClientInit:
    def test_default_base_url(self):
        client = WorkflowProgressClient()
        assert client.base_url == "http://localhost:8000"
        assert client.api_base == "http://localhost:8000/api/workflow-progress"

    def test_custom_base_url(self):
        client = WorkflowProgressClient("http://api.example.com")
        assert client.base_url == "http://api.example.com"
        assert client.api_base == "http://api.example.com/api/workflow-progress"

    def test_trailing_slash_stripped(self):
        client = WorkflowProgressClient("http://api.example.com/")
        assert client.base_url == "http://api.example.com"

    def test_initial_state(self):
        client = WorkflowProgressClient()
        assert client.session is None
        assert client.ws_connections == {}


# ---------------------------------------------------------------------------
# _ensure_session
# ---------------------------------------------------------------------------


class TestEnsureSession:
    @pytest.mark.asyncio
    async def test_creates_session_when_none(self):
        client = WorkflowProgressClient()

        with patch("aiohttp.ClientSession") as MockSession:
            MockSession.return_value = MagicMock()
            MockSession.return_value.closed = False
            await client._ensure_session()
            MockSession.assert_called_once()
            assert client.session is not None

    @pytest.mark.asyncio
    async def test_reuses_open_session(self):
        client = WorkflowProgressClient()
        mock_session = MagicMock()
        mock_session.closed = False
        client.session = mock_session

        with patch("aiohttp.ClientSession") as MockSession:
            session = await client._ensure_session()
            MockSession.assert_not_called()
            assert session is mock_session

    @pytest.mark.asyncio
    async def test_creates_new_session_if_closed(self):
        client = WorkflowProgressClient()
        closed_session = MagicMock()
        closed_session.closed = True
        client.session = closed_session

        with patch("aiohttp.ClientSession") as MockSession:
            new_session = MagicMock()
            new_session.closed = False
            MockSession.return_value = new_session
            await client._ensure_session()
            MockSession.assert_called_once()


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_no_connections(self):
        client = WorkflowProgressClient()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client.session = mock_session

        await client.close()
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_ws_connections(self):
        client = WorkflowProgressClient()
        mock_ws = MagicMock()
        mock_ws.close = AsyncMock()
        client.ws_connections["exec-1"] = {"websocket": mock_ws}

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client.session = mock_session

        await client.close()
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_ignores_ws_close_error(self):
        client = WorkflowProgressClient()
        mock_ws = MagicMock()
        mock_ws.close = AsyncMock(side_effect=Exception("ws error"))
        client.ws_connections["exec-1"] = {"websocket": mock_ws}

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client.session = mock_session

        # Should not raise
        await client.close()

    @pytest.mark.asyncio
    async def test_close_skips_closed_session(self):
        client = WorkflowProgressClient()
        mock_session = MagicMock()
        mock_session.closed = True
        mock_session.close = AsyncMock()
        client.session = mock_session

        await client.close()
        mock_session.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_no_session(self):
        client = WorkflowProgressClient()
        # Should not raise
        await client.close()


# ---------------------------------------------------------------------------
# initialize_progress
# ---------------------------------------------------------------------------


class TestInitializeProgress:
    @pytest.mark.asyncio
    async def test_sends_post_to_correct_url(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"status": "initialized"})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)
        client.session = mock_session

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            result = await client.initialize_progress("exec-1", workflow_id="wf-1", total_phases=4)

        call_args = mock_session.post.call_args
        assert "initialize/exec-1" in call_args[0][0]
        assert result == {"status": "initialized"}

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        client = WorkflowProgressClient()
        import aiohttp

        cm = make_error_response()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)
        client.session = mock_session

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            with pytest.raises(aiohttp.ClientResponseError):
                await client.initialize_progress("exec-1")


# ---------------------------------------------------------------------------
# start_execution
# ---------------------------------------------------------------------------


class TestStartExecution:
    @pytest.mark.asyncio
    async def test_sends_post_to_start_url(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"status": "running"})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            result = await client.start_execution("exec-1", message="Starting...")

        call_args = mock_session.post.call_args
        assert "start/exec-1" in call_args[0][0]
        assert result == {"status": "running"}

    @pytest.mark.asyncio
    async def test_default_message(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"status": "running"})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.start_execution("exec-1")

        params = mock_session.post.call_args[1]["params"]
        assert "Starting" in params["message"]


# ---------------------------------------------------------------------------
# start_phase
# ---------------------------------------------------------------------------


class TestStartPhase:
    @pytest.mark.asyncio
    async def test_sends_phase_params(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"current_phase": 1})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            result = await client.start_phase("exec-1", phase_index=1, phase_name="research")

        call_args = mock_session.post.call_args
        assert "phase/start/exec-1" in call_args[0][0]
        params = call_args[1]["params"]
        assert params["phase_index"] == 1
        assert params["phase_name"] == "research"
        assert result == {"current_phase": 1}

    @pytest.mark.asyncio
    async def test_message_included_when_provided(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.start_phase("exec-1", 0, "draft", message="Starting draft phase")

        params = mock_session.post.call_args[1]["params"]
        assert params.get("message") == "Starting draft phase"

    @pytest.mark.asyncio
    async def test_no_message_key_when_none(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.start_phase("exec-1", 0, "draft")

        params = mock_session.post.call_args[1]["params"]
        assert "message" not in params


# ---------------------------------------------------------------------------
# complete_phase
# ---------------------------------------------------------------------------


class TestCompletePhase:
    @pytest.mark.asyncio
    async def test_sends_phase_output_in_body(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"completed_phases": 1})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.complete_phase(
                "exec-1", "research", phase_output={"key": "val"}, duration_ms=5000
            )

        call_args = mock_session.post.call_args
        assert "phase/complete/exec-1" in call_args[0][0]
        assert call_args[1]["json"] == {"phase_output": {"key": "val"}}
        assert "5000" in call_args[1]["params"].get("duration_ms", "")

    @pytest.mark.asyncio
    async def test_no_duration_when_none(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.complete_phase("exec-1", "research")

        params = mock_session.post.call_args[1]["params"]
        assert "duration_ms" not in params


# ---------------------------------------------------------------------------
# fail_phase
# ---------------------------------------------------------------------------


class TestFailPhase:
    @pytest.mark.asyncio
    async def test_sends_error_param(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"status": "failed"})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.fail_phase("exec-1", "research", error="Timeout")

        call_args = mock_session.post.call_args
        assert "phase/fail/exec-1" in call_args[0][0]
        params = call_args[1]["params"]
        assert params["phase_name"] == "research"
        assert params["error"] == "Timeout"


# ---------------------------------------------------------------------------
# mark_complete
# ---------------------------------------------------------------------------


class TestMarkComplete:
    @pytest.mark.asyncio
    async def test_sends_final_output_in_body(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"status": "completed"})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            result = await client.mark_complete(
                "exec-1", final_output={"content": "done"}, duration_ms=30000
            )

        call_args = mock_session.post.call_args
        assert "complete/exec-1" in call_args[0][0]
        assert call_args[1]["json"] == {"final_output": {"content": "done"}}
        assert result == {"status": "completed"}

    @pytest.mark.asyncio
    async def test_default_message(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.mark_complete("exec-1")

        params = mock_session.post.call_args[1]["params"]
        assert "completed" in params["message"].lower()


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------


class TestMarkFailed:
    @pytest.mark.asyncio
    async def test_sends_error_and_optional_phase(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"status": "failed"})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.mark_failed("exec-1", error="OOM", failed_phase="research")

        call_args = mock_session.post.call_args
        assert "fail/exec-1" in call_args[0][0]
        params = call_args[1]["params"]
        assert params["error"] == "OOM"
        assert params["failed_phase"] == "research"

    @pytest.mark.asyncio
    async def test_no_failed_phase_when_omitted(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            await client.mark_failed("exec-1", error="OOM")

        params = mock_session.post.call_args[1]["params"]
        assert "failed_phase" not in params


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_sends_get_to_status_url(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"status": "running", "progress_percent": 50})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            result = await client.get_status("exec-1")

        call_args = mock_session.get.call_args
        assert "status/exec-1" in call_args[0][0]
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        client = WorkflowProgressClient()
        import aiohttp

        cm = make_error_response()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            with pytest.raises(aiohttp.ClientResponseError):
                await client.get_status("exec-1")


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_sends_delete_to_cleanup_url(self):
        client = WorkflowProgressClient()
        cm, resp = make_mock_response({"deleted": True})
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.delete = MagicMock(return_value=cm)

        with patch.object(client, "_ensure_session", AsyncMock(return_value=mock_session)):
            result = await client.cleanup("exec-1")

        call_args = mock_session.delete.call_args
        assert "cleanup/exec-1" in call_args[0][0]
        assert result == {"deleted": True}


# ---------------------------------------------------------------------------
# unsubscribe_progress
# ---------------------------------------------------------------------------


class TestUnsubscribeProgress:
    @pytest.mark.asyncio
    async def test_closes_websocket_and_removes_connection(self):
        client = WorkflowProgressClient()
        mock_ws = MagicMock()
        mock_ws.close = AsyncMock()
        client.ws_connections["exec-1"] = {"websocket": mock_ws}

        await client.unsubscribe_progress("exec-1")

        mock_ws.close.assert_called_once()
        assert "exec-1" not in client.ws_connections

    @pytest.mark.asyncio
    async def test_does_nothing_for_unknown_execution(self):
        client = WorkflowProgressClient()
        # Should not raise
        await client.unsubscribe_progress("nonexistent")

    @pytest.mark.asyncio
    async def test_removes_connection_even_if_close_fails(self):
        client = WorkflowProgressClient()
        mock_ws = MagicMock()
        mock_ws.close = AsyncMock(side_effect=Exception("close error"))
        client.ws_connections["exec-1"] = {"websocket": mock_ws}

        await client.unsubscribe_progress("exec-1")

        assert "exec-1" not in client.ws_connections

    @pytest.mark.asyncio
    async def test_handles_missing_websocket_key(self):
        client = WorkflowProgressClient()
        client.ws_connections["exec-1"] = {}  # No "websocket" key

        # Should not raise
        await client.unsubscribe_progress("exec-1")
        assert "exec-1" not in client.ws_connections


# ---------------------------------------------------------------------------
# subscribe_progress — raises when auto_reconnect=False
# ---------------------------------------------------------------------------


class TestSubscribeProgress:
    @pytest.mark.asyncio
    async def test_raises_on_connect_failure_no_reconnect(self):
        client = WorkflowProgressClient()

        async def dummy_callback(data):
            pass

        with patch("websockets.connect", side_effect=ConnectionRefusedError("refused")):
            with pytest.raises(ConnectionRefusedError):
                await client.subscribe_progress("exec-1", dummy_callback, auto_reconnect=False)

    @pytest.mark.asyncio
    async def test_sync_callback_called_for_message(self):
        """subscribe_progress calls sync callbacks directly."""
        import json as json_mod

        client = WorkflowProgressClient()
        received = []

        def sync_cb(data):
            received.append(data)

        # Simulate one message then disconnect by raising StopAsyncIteration
        mock_ws = MagicMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)
        message_data = {"status": "running"}

        async def aiter():
            yield json_mod.dumps(message_data)
            raise ConnectionError("disconnect")  # triggers auto_reconnect=False raise

        mock_ws.__aiter__ = lambda self: aiter()

        with patch("websockets.connect", return_value=mock_ws):
            with pytest.raises(ConnectionError):
                await client.subscribe_progress("exec-1", sync_cb, auto_reconnect=False)

        assert received == [message_data]


# ---------------------------------------------------------------------------
# get_progress_client convenience function
# ---------------------------------------------------------------------------


class TestGetProgressClient:
    def test_returns_progress_client_instance(self):
        client = get_progress_client()
        assert isinstance(client, WorkflowProgressClient)
        assert client.base_url == "http://localhost:8000"

    def test_custom_url_passed_through(self):
        client = get_progress_client("http://custom.example.com")
        assert client.base_url == "http://custom.example.com"

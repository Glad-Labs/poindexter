"""
Unit tests for services/email_publisher.py

Tests EmailPublisher initialization, publish, send_newsletter, and
send_notification. SMTP calls are mocked to avoid real network I/O.
The aiosmtplib and html2text packages are optional; they are mocked here
because they may not be installed in the dev/CI environment.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub out optional dependencies before importing the module under test.
# This ensures tests run even when aiosmtplib/html2text are not installed.
# ---------------------------------------------------------------------------

def _install_stub(name: str, stub: ModuleType):
    """Insert stub into sys.modules if the real module is absent."""
    if name not in sys.modules:
        sys.modules[name] = stub


# aiosmtplib stub: provides the SMTP async context manager
_aiosmtplib_stub = ModuleType("aiosmtplib")
_mock_smtp_cls = MagicMock()
setattr(_aiosmtplib_stub, "SMTP", _mock_smtp_cls)
_install_stub("aiosmtplib", _aiosmtplib_stub)

# html2text stub (imported at module level in email_publisher but not used in tests)
_html2text_stub = ModuleType("html2text")
_install_stub("html2text", _html2text_stub)

# Now safe to import
from services.email_publisher import EmailPublisher  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unconfigured_publisher(monkeypatch) -> EmailPublisher:
    """Publisher with no SMTP env vars set."""
    for var in ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "SMTP_PORT"]:
        monkeypatch.delenv(var, raising=False)
    return EmailPublisher()


@pytest.fixture
def configured_publisher(monkeypatch) -> EmailPublisher:
    """Publisher with all required SMTP env vars set."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_FROM", "from@example.com")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    return EmailPublisher()


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestEmailPublisherInit:
    def test_available_true_when_configured(self, configured_publisher):
        assert configured_publisher.available is True

    def test_available_false_when_unconfigured(self, unconfigured_publisher):
        assert unconfigured_publisher.available is False

    def test_smtp_port_defaults_to_587(self, unconfigured_publisher):
        assert unconfigured_publisher.smtp_port == 587

    def test_email_from_falls_back_to_smtp_user(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SMTP_USER", "user@test.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.delenv("EMAIL_FROM", raising=False)
        publisher = EmailPublisher()
        assert publisher.email_from == "user@test.com"

    def test_use_tls_defaults_true(self, configured_publisher):
        assert configured_publisher.use_tls is True

    def test_use_tls_false_when_set(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SMTP_USER", "user@test.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("EMAIL_FROM", "from@test.com")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        publisher = EmailPublisher()
        assert publisher.use_tls is False

    def test_smtp_port_parsed_from_env(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("SMTP_USER", "user@test.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pass")
        monkeypatch.setenv("EMAIL_FROM", "from@test.com")
        publisher = EmailPublisher()
        assert publisher.smtp_port == 465


# ---------------------------------------------------------------------------
# publish()
# ---------------------------------------------------------------------------


class TestEmailPublisherPublish:
    @pytest.mark.asyncio
    async def test_not_configured_returns_failure(self, unconfigured_publisher):
        result = await unconfigured_publisher.publish(
            subject="Test", content="Body", recipient_emails=["a@example.com"]
        )
        assert result["success"] is False
        assert "not configured" in result["error"].lower()
        assert result["recipients"] == 0

    @pytest.mark.asyncio
    async def test_empty_recipients_returns_failure(self, configured_publisher):
        result = await configured_publisher.publish(
            subject="Test", content="Body", recipient_emails=[]
        )
        assert result["success"] is False
        assert "No recipient" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_send(self, configured_publisher):
        """Mock aiosmtplib.SMTP context manager to simulate success."""
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock(return_value=False)
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock(return_value=({}, "OK"))

        import aiosmtplib  # type: ignore[import]
        with patch.object(aiosmtplib, "SMTP", return_value=mock_smtp):
            result = await configured_publisher.publish(
                subject="Hello",
                content="Plain text body",
                recipient_emails=["recipient@example.com"],
            )

        assert result["success"] is True
        assert result["recipients"] == 1
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_multiple_recipients(self, configured_publisher):
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock(return_value=False)
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock(return_value=({}, "OK"))

        import aiosmtplib  # type: ignore[import]
        with patch.object(aiosmtplib, "SMTP", return_value=mock_smtp):
            result = await configured_publisher.publish(
                subject="Newsletter",
                content="Body",
                recipient_emails=["a@e.com", "b@e.com", "c@e.com"],
            )

        assert result["success"] is True
        assert result["recipients"] == 3

    @pytest.mark.asyncio
    async def test_smtp_exception_returns_failure(self, configured_publisher):
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))
        mock_smtp.__aexit__ = AsyncMock(return_value=False)

        import aiosmtplib  # type: ignore[import]
        with patch.object(aiosmtplib, "SMTP", return_value=mock_smtp):
            result = await configured_publisher.publish(
                subject="Test",
                content="Body",
                recipient_emails=["a@example.com"],
            )

        assert result["success"] is False
        assert "Email send failed" in result["error"]

    @pytest.mark.asyncio
    async def test_html_content_included_when_provided(self, configured_publisher):
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock(return_value=False)
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock(return_value=({}, "OK"))

        import aiosmtplib  # type: ignore[import]
        with patch.object(aiosmtplib, "SMTP", return_value=mock_smtp):
            result = await configured_publisher.publish(
                subject="Rich Email",
                content="Plain text",
                recipient_emails=["r@e.com"],
                html_content="<h1>Rich HTML</h1>",
            )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_from_name_used_in_from_field(self, configured_publisher):
        """When from_name is set, the From header includes it."""
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock(return_value=False)
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock(return_value=({}, "OK"))

        import aiosmtplib  # type: ignore[import]
        with patch.object(aiosmtplib, "SMTP", return_value=mock_smtp):
            result = await configured_publisher.publish(
                subject="From Named Sender",
                content="Body",
                recipient_emails=["r@e.com"],
                from_name="Glad Labs",
            )

        assert result["success"] is True


# ---------------------------------------------------------------------------
# send_newsletter()
# ---------------------------------------------------------------------------


class TestEmailPublisherNewsletter:
    @pytest.mark.asyncio
    async def test_not_configured_returns_failure(self, unconfigured_publisher):
        result = await unconfigured_publisher.send_newsletter(
            subject="Newsletter", content="Body", list_name="tech-list"
        )
        assert result["success"] is False
        assert "not configured" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_configured_returns_success(self, configured_publisher):
        result = await configured_publisher.send_newsletter(
            subject="Weekly Update",
            content="This week in AI...",
            list_name="weekly-digest",
        )
        assert result["success"] is True
        assert result["list"] == "weekly-digest"
        assert result["error"] is None


# ---------------------------------------------------------------------------
# send_notification()
# ---------------------------------------------------------------------------


class TestEmailPublisherNotification:
    @pytest.mark.asyncio
    async def test_not_configured_returns_failure(self, unconfigured_publisher):
        result = await unconfigured_publisher.send_notification(
            recipient="user@example.com",
            title="Alert",
            message="Something happened",
        )
        assert result["success"] is False
        assert "not configured" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_successful_notification(self, configured_publisher):
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock(return_value=False)
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock(return_value=({}, "OK"))

        import aiosmtplib  # type: ignore[import]
        with patch.object(aiosmtplib, "SMTP", return_value=mock_smtp):
            result = await configured_publisher.send_notification(
                recipient="user@example.com",
                title="Task Complete",
                message="Your task finished.",
            )

        assert result["success"] is True
        assert result["recipients"] == 1

    @pytest.mark.asyncio
    async def test_notification_with_action_url(self, configured_publisher):
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
        mock_smtp.__aexit__ = AsyncMock(return_value=False)
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock(return_value=({}, "OK"))

        import aiosmtplib  # type: ignore[import]
        with patch.object(aiosmtplib, "SMTP", return_value=mock_smtp):
            result = await configured_publisher.send_notification(
                recipient="user@example.com",
                title="View Results",
                message="Your content is ready.",
                action_url="https://app.example.com/tasks/123",
            )

        assert result["success"] is True

"""
Unit tests for webhooks_schemas.py

Tests field validation and model behaviour for webhook schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.webhooks_schemas import (
    ContentWebhookPayload,
    WebhookEntry,
    WebhookResponse,
)


# ---------------------------------------------------------------------------
# WebhookEntry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhookEntry:
    def test_valid_with_title(self):
        entry = WebhookEntry(id=42, title="My Blog Post")
        assert entry.id == 42
        assert entry.title == "My Blog Post"

    def test_valid_without_title(self):
        entry = WebhookEntry(id=1)  # type: ignore[call-arg]
        assert entry.title is None

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            WebhookEntry(title="Post")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ContentWebhookPayload
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentWebhookPayload:
    def _valid(self, **kwargs):
        defaults = {
            "event": "entry.create",
            "model": "article",
            "entry": WebhookEntry(id=1, title="New Post"),
        }
        defaults.update(kwargs)
        return ContentWebhookPayload(**defaults)

    def test_valid(self):
        payload = self._valid()
        assert payload.event == "entry.create"
        assert payload.model == "article"
        assert payload.entry.id == 1

    def test_all_event_types(self):
        for event in ["entry.create", "entry.publish", "entry.unpublish", "entry.delete"]:
            payload = self._valid(event=event)
            assert payload.event == event

    def test_missing_event_raises(self):
        with pytest.raises(ValidationError):
            ContentWebhookPayload(  # type: ignore[call-arg]
                model="article",
                entry=WebhookEntry(id=1),  # type: ignore[call-arg]
            )

    def test_missing_model_raises(self):
        with pytest.raises(ValidationError):
            ContentWebhookPayload(  # type: ignore[call-arg]
                event="entry.create",
                entry=WebhookEntry(id=1),  # type: ignore[call-arg]
            )

    def test_missing_entry_raises(self):
        with pytest.raises(ValidationError):
            ContentWebhookPayload(  # type: ignore[call-arg]
                event="entry.create",
                model="article",
            )


# ---------------------------------------------------------------------------
# WebhookResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhookResponse:
    def test_valid(self):
        resp = WebhookResponse(  # type: ignore[call-arg]
            event="entry.create",
            entry_id=42,
        )
        assert resp.status == "received"
        assert resp.message is None

    def test_with_message(self):
        resp = WebhookResponse(  # type: ignore[call-arg]
            event="entry.delete",
            entry_id=99,
            message="Cache invalidated",
        )
        assert resp.message == "Cache invalidated"

    def test_custom_status(self):
        resp = WebhookResponse(  # type: ignore[call-arg]
            status="processed",
            event="entry.publish",
            entry_id=10,
        )
        assert resp.status == "processed"

    def test_missing_event_raises(self):
        with pytest.raises(ValidationError):
            WebhookResponse(entry_id=1)  # type: ignore[call-arg]

    def test_missing_entry_id_raises(self):
        with pytest.raises(ValidationError):
            WebhookResponse(event="entry.create")  # type: ignore[call-arg]

"""Unit tests for services/sentry_relay.py.

Pins what makes the drain safe to leave running unattended: the DSN key
goes back on as ``?sentry_key=`` (GlitchTip will not read it from the
envelope header), an envelope is acked only once GlitchTip has answered, a
verdict is acked while an outage is not, and nothing from the relay's side
(the bearer, the envelope body) leaks into GlitchTip requests or reasons.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from poindexter.services.sentry_relay import drain_sentry_relay

RELAY = "https://relay.example.com"
GLITCHTIP = "http://gt.local:8000"
# Fake, low-entropy key: a real one is an operator identifier, and a
# high-entropy literal reads as a credential to secret scanners.
KEY = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ENVELOPE = b'{"event_id":"e1","dsn":"https://k@relay.example.com/2"}\n{"type":"event"}\n{"message":"boom"}\n'


class FakeSiteConfig:
    def __init__(self, settings: dict[str, str] | None = None, secret: str = "s3cret"):
        self._settings = {
            "sentry_relay_url": RELAY,
            "glitchtip_base_url": GLITCHTIP,
            **(settings or {}),
        }
        self._secret = secret

    def get(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)

    async def get_secret(self, key: str, default: str = "") -> str:
        return self._secret if key == "sentry_relay_secret" else default


def row(id_: int, *, body: bytes = ENVELOPE, project_id: str = "2", key: str = KEY) -> dict[str, Any]:
    return {
        "id": id_,
        "received_at": "2026-09-28T17:00:00.000Z",
        "project_id": project_id,
        "public_key": key,
        "item_types": ["event"],
        "body": base64.b64encode(body).decode(),
    }


class Relay:
    """A fake relay + GlitchTip behind one httpx.MockTransport.

    ``queue`` is what /pending serves (oldest first, paged by ``limit``);
    /ack removes ids from it. ``glitchtip`` answers each envelope POST in
    order; an ``Exception`` entry is raised instead of answered.
    """

    def __init__(self, queue: list[dict[str, Any]], glitchtip: list[Any] | None = None,
                 *, expired: int = 0):
        self.queue = list(queue)
        self.glitchtip_answers = list(glitchtip or [])
        self.expired = expired
        self.calls: list[tuple[str, str]] = []
        self.glitchtip_requests: list[httpx.Request] = []
        self.acked: list[int] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        host = f"{request.url.scheme}://{request.url.netloc.decode()}"
        self.calls.append((host, request.url.path))
        if host == RELAY:
            assert request.headers.get("Authorization") == "Bearer s3cret"
            if request.url.path == "/pending":
                limit = int(request.url.params.get("limit", "25"))
                expired, self.expired = self.expired, 0
                return httpx.Response(200, json={
                    "envelopes": self.queue[:limit],
                    "backlog": len(self.queue),
                    "expired": expired,
                })
            if request.url.path == "/ack":
                ids = json.loads(request.content)["ids"]
                before = len(self.queue)
                self.queue = [r for r in self.queue if r["id"] not in ids]
                self.acked.extend(ids)
                return httpx.Response(200, json={"removed": before - len(self.queue)})
        if host == GLITCHTIP:
            self.glitchtip_requests.append(request)
            answer = self.glitchtip_answers.pop(0) if self.glitchtip_answers else 200
            if isinstance(answer, Exception):
                raise answer
            return httpx.Response(answer, json={"detail": "Denied"} if answer == 403 else {})
        raise AssertionError(f"unexpected request {request.method} {request.url}")


async def test_forwards_with_the_key_as_a_query_parameter_then_acks():
    relay = Relay([row(1)])
    out = await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())

    assert (out.pulled, out.forwarded, out.acked, out.backlog) == (1, 1, 1, 0)
    assert out.errors == []
    [sent] = relay.glitchtip_requests
    assert sent.url.path == "/api/2/envelope/"
    # GlitchTip authenticates ingest ONLY from ?sentry_key= or X-Sentry-Auth.
    assert sent.url.params["sentry_key"] == KEY
    assert sent.content == ENVELOPE
    assert sent.headers["Content-Type"] == "application/x-sentry-envelope"
    # The relay's bearer is for the relay; it must never reach GlitchTip.
    assert "Authorization" not in sent.headers
    # Ack only after GlitchTip answered.
    assert [path for _, path in relay.calls] == ["/pending", "/api/2/envelope/", "/ack"]


async def test_binary_envelope_bytes_arrive_unchanged():
    body = ENVELOPE + b'{"type":"attachment","length":4}\n\x00\xff\n\x80\n'
    relay = Relay([row(1, body=body)])
    await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())
    assert relay.glitchtip_requests[0].content == body


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
async def test_a_client_error_is_acked_and_reported_not_retried(status):
    relay = Relay([row(1)], glitchtip=[status])
    out = await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())

    assert out.rejected == 1
    assert out.forwarded == 0
    assert relay.acked == [1]
    assert out.errors == []
    assert f"GlitchTip answered {status} for project 2" in out.rejections[0]
    # The reason can end up in Discord; it must not carry the visitor's report.
    assert "boom" not in out.rejections[0]


@pytest.mark.parametrize("answer", [429, 500, 503, httpx.ConnectError("refused")])
async def test_an_outage_leaves_the_queue_alone_and_stops_the_pass(answer):
    relay = Relay([row(1), row(2), row(3)], glitchtip=[answer])
    out = await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())

    assert out.deferred == 1
    assert out.forwarded == 0
    assert relay.acked == []
    assert len(relay.queue) == 3
    # One attempt, not three: the rest would meet the same outage.
    assert len(relay.glitchtip_requests) == 1
    assert len(out.errors) == 1


async def test_rows_settled_before_an_outage_are_still_acked():
    relay = Relay([row(1), row(2), row(3)], glitchtip=[200, 503])
    out = await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())

    assert out.forwarded == 1
    assert out.deferred == 1
    assert relay.acked == [1]
    assert [r["id"] for r in relay.queue] == [2, 3]
    assert out.backlog == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"project_id": "2/../0"},
        {"project_id": ""},
        {"key": "not-a-key"},
        {"body": b""},
    ],
)
async def test_a_malformed_row_is_dropped_without_calling_glitchtip(bad):
    kwargs: dict[str, Any] = {}
    if "project_id" in bad:
        kwargs["project_id"] = bad["project_id"]
    if "key" in bad:
        kwargs["key"] = bad["key"]
    if "body" in bad:
        kwargs["body"] = bad["body"]
    relay = Relay([row(1, **kwargs)])
    out = await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())

    assert out.rejected == 1
    assert relay.glitchtip_requests == []
    assert relay.acked == [1]


async def test_a_row_whose_body_is_not_base64_is_dropped():
    bad = row(1)
    bad["body"] = "!!! not base64 !!!"
    relay = Relay([bad])
    out = await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())
    assert out.rejected == 1
    assert relay.glitchtip_requests == []


async def test_pages_until_the_queue_is_empty():
    relay = Relay([row(i) for i in range(1, 6)])
    out = await drain_sentry_relay(
        FakeSiteConfig({"sentry_relay_drain_batch_size": "2"}), transport=relay.transport()
    )
    assert out.forwarded == 5
    assert relay.queue == []
    assert [path for _, path in relay.calls].count("/pending") == 3


async def test_stops_after_max_batches():
    relay = Relay([row(i) for i in range(1, 6)])
    out = await drain_sentry_relay(
        FakeSiteConfig({
            "sentry_relay_drain_batch_size": "2",
            "sentry_relay_drain_max_batches": "1",
        }),
        transport=relay.transport(),
    )
    assert out.forwarded == 2
    assert out.backlog == 3


async def test_reports_envelopes_the_relay_expired():
    relay = Relay([], expired=4)
    out = await drain_sentry_relay(FakeSiteConfig(), transport=relay.transport())
    assert out.expired == 4
    assert out.pulled == 0


async def test_no_relay_configured_is_a_quiet_noop():
    relay = Relay([row(1)])
    out = await drain_sentry_relay(
        FakeSiteConfig({"sentry_relay_url": ""}), transport=relay.transport()
    )
    assert out.configured is False
    assert out.errors == []
    assert relay.calls == []


async def test_missing_secret_reports_instead_of_silently_passing():
    relay = Relay([row(1)])
    out = await drain_sentry_relay(FakeSiteConfig(secret=""), transport=relay.transport())
    assert out.configured is True
    assert out.errors == ["sentry_relay_secret is not set"]
    assert relay.calls == []


async def test_an_unreachable_relay_raises_for_the_job_to_report():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    with pytest.raises(httpx.HTTPStatusError):
        await drain_sentry_relay(FakeSiteConfig(), transport=httpx.MockTransport(handler))


async def test_defaults_to_the_compose_glitchtip_when_unset():
    relay = Relay([row(1)])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "glitchtip-web":
            assert request.url.port == 8000
            return httpx.Response(200, json={})
        return relay.transport().handle_request(request)

    out = await drain_sentry_relay(
        FakeSiteConfig({"glitchtip_base_url": ""}), transport=httpx.MockTransport(handler)
    )
    assert out.forwarded == 1

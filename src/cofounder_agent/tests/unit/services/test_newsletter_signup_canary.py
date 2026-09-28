"""Unit tests for services/newsletter_signup_canary.py.

The canary is the only detector that can tell "nobody signed up" from "the
form is broken". These pin the three things it must get right:

- It exercises the REAL path (a POST to the public endpoint, like the form),
  then checks with the worker's own key that the contact reached the segment
  the worker syncs. A 200 alone is not proof: a route pointed at another
  Resend team or segment answers 200 too.
- It always cleans up, including after a failure, so the canary never
  becomes a subscriber.
- A switched-on canary that cannot run is an error, not a silent pass.
"""

from __future__ import annotations

import json

import httpx
import pytest

from poindexter.services import newsletter_signup_canary as canary
from tests.unit.services._newsletter_fakes import SEGMENT, FakeSiteConfig

URL = "https://site.example/api/newsletter/subscribe"
CANARY = "delivered+signup-canary@resend.dev"


def config(**over: str) -> FakeSiteConfig:
    values = {
        "newsletter_signup_canary_url": URL,
        "newsletter_signup_canary_email": CANARY,
        "newsletter_signup_canary_attempts": "2",
        "newsletter_signup_canary_retry_seconds": "30",
    }
    values.update(over)
    return FakeSiteConfig(values)


class Fake:
    """Route + Resend in one MockTransport, recording every request."""

    def __init__(self, *, route=None, segments=None, delete=200):
        # route: list of responses/exceptions served in order (last repeats)
        self.route = route or [httpx.Response(200, json={"success": True})]
        self.segments = segments if segments is not None else [SEGMENT]
        self.delete_status = delete
        self.requests: list[httpx.Request] = []
        self._route_i = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "site.example":
            item = self.route[min(self._route_i, len(self.route) - 1)]
            self._route_i += 1
            if isinstance(item, Exception):
                raise item
            return item
        assert request.url.host == "api.resend.com", request.url
        if request.method == "GET" and request.url.path.endswith("/segments"):
            data = [{"id": s, "name": "General"} for s in self.segments]
            return httpx.Response(200, json={"object": "list", "data": data})
        if request.method == "DELETE":
            return httpx.Response(self.delete_status, json={"deleted": True})
        raise AssertionError(f"unexpected Resend call {request.method} {request.url}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def calls(self, method: str, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.host == host]


class Pauses:
    def __init__(self):
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


async def run(sc, fake, pauses=None, **kw):
    return await canary.run_signup_canary(
        sc, transport=fake.transport, pause=pauses or Pauses(), **kw
    )


async def test_off_when_no_url_is_configured():
    fake = Fake()
    outcome = await run(config(newsletter_signup_canary_url=""), fake)
    assert outcome is None
    assert fake.requests == []


async def test_healthy_run_posts_like_the_form_verifies_and_cleans_up():
    fake = Fake()
    outcome = await run(config(), fake)
    assert outcome.healthy
    assert (outcome.http_status, outcome.attempts) == (200, 1)
    assert outcome.cleaned_up is True
    assert outcome.problem is None

    [post] = fake.calls("POST", "site.example")
    assert str(post.url) == URL
    assert json.loads(post.content) == {"email": CANARY, "first_name": "Canary"}

    [check] = fake.calls("GET", "api.resend.com")
    # The address sits in the path; @ and + must stay literal for Resend.
    assert check.url.raw_path.decode() == f"/contacts/{CANARY}/segments"
    assert check.headers["Authorization"] == "Bearer re_test"
    assert len(fake.calls("DELETE", "api.resend.com")) == 1


async def test_a_failing_route_is_retried_then_reported_and_still_cleaned_up():
    fake = Fake(route=[httpx.Response(503, json={
        "success": False,
        "detail": "We could not save your subscription. Please try again shortly.",
    })])
    pauses = Pauses()
    outcome = await run(config(), fake, pauses)
    assert not outcome.healthy
    assert outcome.attempts == 2
    assert pauses.waits == [30.0]
    assert "HTTP 503" in outcome.problem
    assert "could not save" in outcome.problem
    # No verification against Resend when the route already failed...
    assert fake.calls("GET", "api.resend.com") == []
    # ...but the cleanup still runs.
    assert len(fake.calls("DELETE", "api.resend.com")) == 1


async def test_a_retry_that_succeeds_is_healthy():
    fake = Fake(route=[
        httpx.ConnectTimeout("cold start"),
        httpx.Response(200, json={"success": True}),
    ])
    outcome = await run(config(), fake)
    assert outcome.healthy
    assert outcome.attempts == 2


async def test_an_unreachable_route_says_no_response():
    fake = Fake(route=[httpx.ConnectError("dns")])
    outcome = await run(config(newsletter_signup_canary_attempts="1"), fake)
    assert not outcome.healthy
    assert outcome.http_status is None
    assert "no response" in outcome.problem


async def test_a_200_without_success_true_is_not_a_capture():
    fake = Fake(route=[httpx.Response(200, text="<html>maintenance</html>")])
    outcome = await run(config(newsletter_signup_canary_attempts="1"), fake)
    assert not outcome.route_ok


async def test_success_in_the_wrong_segment_is_caught():
    """The route answered 200, but its RESEND_AUDIENCE_ID is not the segment
    the worker syncs, so the worker would never see the signup."""
    fake = Fake(segments=["some-other-segment"])
    pauses = Pauses()
    outcome = await run(config(), fake, pauses)
    assert outcome.route_ok and not outcome.in_segment
    assert not outcome.healthy
    assert SEGMENT in outcome.problem
    assert "RESEND_AUDIENCE_ID" in outcome.problem
    assert len(fake.calls("GET", "api.resend.com")) == 3  # bounded re-checks
    assert len(fake.calls("DELETE", "api.resend.com")) == 1


@pytest.mark.parametrize(("status", "cleaned"), [(404, True), (500, False)])
async def test_cleanup_status_is_reported(status, cleaned):
    outcome = await run(config(), Fake(delete=status))
    assert outcome.healthy
    assert outcome.cleaned_up is cleaned


async def test_the_url_argument_overrides_the_setting():
    fake = Fake()
    outcome = await run(config(newsletter_signup_canary_url=""), fake, url=URL)
    assert outcome is not None and outcome.healthy


@pytest.mark.parametrize(
    ("over", "api_key", "needle"),
    [
        ({"newsletter_signup_canary_url": "ftp://site.example/x"}, "re_test", "http(s)"),
        ({"newsletter_signup_canary_email": "not an address"}, "re_test", "plain address"),
        ({"newsletter_signup_canary_email": "a@b.c/../x"}, "re_test", "plain address"),
        ({"resend_audience_id": ""}, "re_test", "resend_audience_id is not set"),
        ({"resend_audience_id": "abc/def"}, "re_test", "plain Resend id token"),
        ({}, "", "resend_api_key"),
    ],
)
async def test_a_switched_on_canary_that_cannot_run_raises(over, api_key, needle):
    sc = config(**over)
    sc.api_key = api_key
    fake = Fake()
    with pytest.raises(canary.CanaryConfigError) as exc_info:
        await run(sc, fake)
    assert needle in str(exc_info.value)
    assert fake.requests == []  # nothing sent when misconfigured


def test_metrics_shape():
    outcome = canary.SignupCanaryOutcome(route_ok=True, in_segment=True, http_status=200)
    metrics = outcome.as_metrics()
    assert metrics["healthy"] is True
    assert set(metrics) == {
        "healthy", "route_ok", "in_segment", "cleaned_up",
        "http_status", "attempts", "latency_ms",
    }

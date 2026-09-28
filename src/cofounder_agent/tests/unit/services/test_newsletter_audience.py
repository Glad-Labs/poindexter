"""Unit tests for services/newsletter_audience.py.

The public signup form captures into a Resend segment and this module pulls
that segment into ``newsletter_subscribers``. What these pin, in order of what
it costs to get wrong:

1. Opt-out precedence. An address the owned table has unsubscribed is never
   re-subscribed by the pull; a Resend-side opt-out IS applied. Getting either
   backwards mails someone who asked to leave.
2. Every new row is sendable: a fresh 43-char unsubscribe token, verified on
   signup, the real signup time.
3. A truncated read is reported, never passed off as the whole segment.
4. The canary and malformed addresses never reach the list.

The SQL itself runs against a real migrated database in
``tests/integration_db/test_newsletter_audience_sync_sql.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from poindexter.services import newsletter_audience as na
from tests.unit._nonempty import nonempty
from tests.unit.services._newsletter_fakes import SEGMENT, FakeSiteConfig

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConn:
    """Mimics the three statements the sync issues, with the real semantics:
    case-insensitive lookup, ``ON CONFLICT (email) DO NOTHING`` on the raw
    column, and the ``unsubscribed_at IS NULL`` guard on the update."""

    def __init__(self, db: FakeDb):
        self.db = db

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        assert "lower(email) = lower($1)" in query, query
        want = str(args[0]).lower()
        hits = [r for r in self.db.rows if r["email"].lower() == want]
        return min(hits, key=lambda r: r["id"]) if hits else None

    async def execute(self, query: str, *args: Any) -> str:
        if self.db.fail_on and self.db.fail_on in json.dumps([str(a) for a in args]):
            raise RuntimeError("simulated write failure")
        if query.lstrip().startswith("UPDATE newsletter_subscribers"):
            row_id, reason = args
            for row in self.db.rows:
                if row["id"] == row_id and row["unsubscribed_at"] is None:
                    row["unsubscribed_at"] = "now"
                    row["unsubscribe_reason"] = reason
                    return "UPDATE 1"
            return "UPDATE 0"
        if query.lstrip().startswith("INSERT INTO newsletter_subscribers"):
            assert "ON CONFLICT (email) DO NOTHING" in query
            email, first, last, subscribed_at, token = args
            if any(r["email"] == email for r in self.db.rows):
                return "INSERT 0 0"
            self.db.rows.append({
                "id": len(self.db.rows) + 100,
                "email": email,
                "first_name": first,
                "last_name": last,
                "subscribed_at": subscribed_at,
                "verified": True,
                "unsubscribe_token": token,
                "unsubscribed_at": None,
                "unsubscribe_reason": None,
            })
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


class _Acquire:
    def __init__(self, conn):
        self._c = conn

    async def __aenter__(self):
        return self._c

    async def __aexit__(self, *exc):
        return None


class FakeDb:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.fail_on: str | None = None

    def acquire(self):
        return _Acquire(FakeConn(self))

    def row(self, email: str) -> dict[str, Any]:
        return next(r for r in self.rows if r["email"].lower() == email.lower())


def owned(row_id: int, email: str, *, unsubscribed: bool = False) -> dict[str, Any]:
    return {
        "id": row_id,
        "email": email,
        "first_name": None,
        "last_name": None,
        "subscribed_at": None,
        "verified": True,
        "unsubscribe_token": "t" * 43,
        "unsubscribed_at": "earlier" if unsubscribed else None,
        "unsubscribe_reason": "relay" if unsubscribed else None,
    }


def contact(
    email: str,
    *,
    cid: str | None = None,
    unsubscribed: bool = False,
    first_name: str | None = "Ada",
    created_at: str = "2026-09-28 15:58:09.965043+00",
) -> dict[str, Any]:
    return {
        "id": cid or f"c-{email}",
        "email": email,
        "first_name": first_name,
        "last_name": None,
        "created_at": created_at,
        "unsubscribed": unsubscribed,
    }


def segment_transport(*pages: dict[str, Any], seen: list[httpx.Request] | None = None):
    """Serve the given segment-contact pages in order, recording requests."""
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        assert request.url.path == f"/segments/{SEGMENT}/contacts", request.url
        page = pages[min(state["i"], len(pages) - 1)]
        state["i"] += 1
        return httpx.Response(200, json=page)

    return httpx.MockTransport(handler)


def page(*contacts: dict[str, Any], has_more: bool = False) -> dict[str, Any]:
    return {"object": "list", "has_more": has_more, "data": list(contacts)}


async def _no_pause(_seconds: float) -> None:
    return None


async def run_sync(db: FakeDb, sc: FakeSiteConfig, *pages, dry_run=False, seen=None):
    return await na.sync_segment_to_subscribers(
        db, sc, dry_run=dry_run, transport=segment_transport(*pages, seen=seen),
        pause=_no_pause,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_minted_token_is_a_43_char_base64url_credential():
    tokens = {na.mint_unsubscribe_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in nonempty(tokens, "minted tokens"):
        assert len(token) == 43
        assert all(c.isalnum() or c in "-_" for c in token)


def test_segment_id_pattern_accepts_ids_and_rejects_path_shapes():
    """The id is spliced into a URL path (CodeQL py/partial-ssrf #462)."""
    ok = ["78261eea-8f8b-4381-83c6-79fa7120f1cf", "aud_123", "ABC-def_9"]
    bad = ["../contacts", "abc/def", "x?y=1", "", "a b", "é"]
    assert all(na.SEGMENT_ID_RE.fullmatch(v) for v in ok)
    assert not any(na.SEGMENT_ID_RE.fullmatch(v) for v in bad)


def test_resolve_segment_id():
    assert na.resolve_segment_id(FakeSiteConfig()) == SEGMENT
    assert na.resolve_segment_id(FakeSiteConfig({"resend_audience_id": "  "})) is None
    with pytest.raises(na.AudienceConfigError):
        na.resolve_segment_id(FakeSiteConfig({"resend_audience_id": "../contacts"}))


def test_canary_email_is_normalised():
    sc = FakeSiteConfig({"newsletter_signup_canary_email": "  Delivered+X@Resend.dev "})
    assert na.canary_email(sc) == "delivered+x@resend.dev"
    assert na.canary_email(FakeSiteConfig()) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-28 15:58:09.965043+00", datetime(2026, 9, 28, 15, 58, 9, 965043, timezone.utc)),
        ("2026-10-06 23:47:56.678+00", datetime(2026, 10, 6, 23, 47, 56, 678000, timezone.utc)),
        ("2026-09-28T15:58:09Z", datetime(2026, 9, 28, 15, 58, 9, tzinfo=timezone.utc)),
        # A naive value is read as UTC rather than the host's local time.
        ("2026-09-28 15:58:09", datetime(2026, 9, 28, 15, 58, 9, tzinfo=timezone.utc)),
        ("not a date", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_resend_timestamp(raw, expected):
    assert na.parse_resend_timestamp(raw) == expected


# ---------------------------------------------------------------------------
# upsert / mirror
# ---------------------------------------------------------------------------


async def test_upsert_contact_puts_the_contact_in_the_segment_as_fresh_consent():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"object": "contact", "id": "c1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await na.upsert_contact(
            client, "re_k", segment_id=SEGMENT, email="a@b.com", first_name="A"
        )
    assert resp.status_code == 201
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/contacts"
    body = json.loads(req.content)
    assert body == {
        "email": "a@b.com",
        "unsubscribed": False,
        "segments": [{"id": SEGMENT}],
        "first_name": "A",
    }
    assert req.headers["Authorization"] == "Bearer re_k"
    assert "Mozilla" in req.headers["User-Agent"]  # Cloudflare 1010 guard


async def test_mirror_skips_without_reading_the_key_when_unconfigured():
    sc = FakeSiteConfig({"resend_audience_id": ""})

    def handler(request):  # pragma: no cover — must not be called
        raise AssertionError("no Resend call when unconfigured")

    await na.mirror_signup_to_segment(
        sc, email="a@b.com", first_name=None, last_name=None,
        transport=httpx.MockTransport(handler),
    )
    assert sc.secret_reads == []


async def test_mirror_skips_an_invalid_segment_id():
    sc = FakeSiteConfig({"resend_audience_id": "abc/def"})

    def handler(request):  # pragma: no cover — must not be called
        raise AssertionError("a path-shaped id must never reach a URL")

    await na.mirror_signup_to_segment(
        sc, email="a@b.com", first_name=None, last_name=None,
        transport=httpx.MockTransport(handler),
    )


async def test_mirror_skips_when_the_key_is_missing():
    sc = FakeSiteConfig(api_key="")

    def handler(request):  # pragma: no cover — must not be called
        raise AssertionError("no Resend call without a key")

    await na.mirror_signup_to_segment(
        sc, email="a@b.com", first_name=None, last_name=None,
        transport=httpx.MockTransport(handler),
    )


async def test_mirror_upserts_into_the_segment():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json={"object": "contact", "id": "c1"})

    await na.mirror_signup_to_segment(
        FakeSiteConfig(), email="a@b.com", first_name="A", last_name="B",
        transport=httpx.MockTransport(handler),
    )
    body = json.loads(seen[0].content)
    assert body["segments"] == [{"id": SEGMENT}]
    assert (body["first_name"], body["last_name"]) == ("A", "B")


@pytest.mark.parametrize("failure", ["http_error", "network"])
async def test_mirror_never_raises(failure):
    """The owned row is the system of record: a Resend failure must not fail
    the signup that triggered the mirror."""

    def handler(request):
        if failure == "network":
            raise httpx.ConnectError("network down")
        return httpx.Response(422, json={"message": "nope"})

    await na.mirror_signup_to_segment(
        FakeSiteConfig(), email="a@b.com", first_name=None, last_name=None,
        transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------------------
# list_segment_contacts
# ---------------------------------------------------------------------------


async def test_listing_follows_the_after_cursor():
    seen: list[httpx.Request] = []
    transport = segment_transport(
        page(contact("a@x.com", cid="c1"), contact("b@x.com", cid="c2"), has_more=True),
        page(contact("c@x.com", cid="c3")),
        seen=seen,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        contacts, truncated = await na.list_segment_contacts(
            client, "re_k", SEGMENT, max_pages=5, pause=_no_pause
        )
    assert [c["id"] for c in contacts] == ["c1", "c2", "c3"]
    assert truncated is False
    assert seen[0].url.params.get("limit") == "100"
    assert "after" not in seen[0].url.params
    assert seen[1].url.params.get("after") == "c2"


async def test_listing_reports_truncation_at_the_page_cap():
    transport = segment_transport(page(contact("a@x.com", cid="c1"), has_more=True))
    async with httpx.AsyncClient(transport=transport) as client:
        contacts, truncated = await na.list_segment_contacts(
            client, "re_k", SEGMENT, max_pages=2, pause=_no_pause
        )
    assert len(contacts) == 2
    assert truncated is True


async def test_listing_waits_out_a_rate_limit():
    calls = {"n": 0}
    waits: list[float] = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={})
        return httpx.Response(200, json=page(contact("a@x.com")))

    async def record(seconds: float) -> None:
        waits.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        contacts, truncated = await na.list_segment_contacts(
            client, "re_k", SEGMENT, max_pages=5, pause=record
        )
    assert len(contacts) == 1 and truncated is False
    assert waits == [3.0]


async def test_listing_raises_on_a_failed_read():
    """A 404 (segment not in this Resend team) must not read as an empty list."""
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"message": "nf"}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await na.list_segment_contacts(
                client, "re_k", SEGMENT, max_pages=5, pause=_no_pause
            )


# ---------------------------------------------------------------------------
# sync_segment_to_subscribers
# ---------------------------------------------------------------------------


async def test_unconfigured_is_a_no_op():
    sc = FakeSiteConfig({"resend_audience_id": ""})

    def handler(request):  # pragma: no cover — must not be called
        raise AssertionError("no Resend call when unconfigured")

    outcome = await na.sync_segment_to_subscribers(
        FakeDb(), sc, transport=httpx.MockTransport(handler), pause=_no_pause
    )
    assert outcome.segment_id is None
    assert outcome.errors == []


async def test_missing_key_is_an_error():
    outcome = await run_sync(FakeDb(), FakeSiteConfig(api_key=""), page())
    assert outcome.errors == ["resend_api_key is not set"]


async def test_new_contact_is_imported_ready_to_mail():
    db = FakeDb()
    outcome = await run_sync(
        db, FakeSiteConfig(), page(contact("New@Example.com", first_name="  Grace  "))
    )
    assert outcome.imported == 1 and outcome.changes == 1
    row = db.row("new@example.com")
    assert row["email"] == "New@Example.com"  # stored as captured
    assert row["verified"] is True
    assert len(row["unsubscribe_token"]) == 43
    assert row["first_name"] == "Grace"
    # The real signup time, not the time the pull happened to run.
    assert row["subscribed_at"] == datetime(2026, 9, 28, 15, 58, 9, 965043, timezone.utc)


async def test_names_are_clipped_to_the_column_width():
    db = FakeDb()
    await run_sync(db, FakeSiteConfig(), page(contact("a@x.com", first_name="N" * 300)))
    assert len(db.row("a@x.com")["first_name"]) == 100


async def test_existing_subscriber_matches_case_insensitively():
    db = FakeDb([owned(1, "Ada@Example.com")])
    outcome = await run_sync(db, FakeSiteConfig(), page(contact("ada@example.com")))
    assert outcome.imported == 0
    assert outcome.already_subscribed == 1
    assert len(db.rows) == 1


async def test_the_owned_opt_out_wins_over_an_active_contact():
    """The relay unsubscribed this address; Resend still lists it active. The
    pull must NOT re-subscribe it. That would mail someone who asked to leave."""
    db = FakeDb([owned(1, "gone@example.com", unsubscribed=True)])
    outcome = await run_sync(db, FakeSiteConfig(), page(contact("gone@example.com")))
    assert outcome.opted_out_kept == 1
    assert outcome.imported == 0 and outcome.changes == 0
    assert db.row("gone@example.com")["unsubscribed_at"] == "earlier"


async def test_a_resend_side_opt_out_unsubscribes_the_owned_row():
    db = FakeDb([owned(1, "leaving@example.com")])
    outcome = await run_sync(
        db, FakeSiteConfig(), page(contact("leaving@example.com", unsubscribed=True))
    )
    assert outcome.unsubscribes_applied == 1 and outcome.changes == 1
    row = db.row("leaving@example.com")
    assert row["unsubscribed_at"] is not None
    assert row["unsubscribe_reason"] == na.RESEND_UNSUBSCRIBE_REASON


async def test_an_unsubscribed_contact_is_never_imported():
    db = FakeDb()
    outcome = await run_sync(
        db, FakeSiteConfig(), page(contact("never@example.com", unsubscribed=True))
    )
    assert outcome.skipped_unsubscribed == 1
    assert db.rows == []


async def test_an_opt_out_already_applied_is_left_alone():
    db = FakeDb([owned(1, "gone@example.com", unsubscribed=True)])
    outcome = await run_sync(
        db, FakeSiteConfig(), page(contact("gone@example.com", unsubscribed=True))
    )
    assert outcome.already_unsubscribed == 1
    assert db.row("gone@example.com")["unsubscribe_reason"] == "relay"  # untouched


@pytest.mark.parametrize("bad", ["", "not-an-address", "a@b", "a b@c.com", "x" * 250 + "@example.com"])
async def test_malformed_addresses_are_skipped(bad):
    db = FakeDb()
    outcome = await run_sync(db, FakeSiteConfig(), page(contact(bad)))
    assert outcome.skipped_invalid == 1
    assert db.rows == []


async def test_the_signup_canary_never_joins_the_list():
    db = FakeDb()
    sc = FakeSiteConfig({"newsletter_signup_canary_email": "delivered+signup-canary@resend.dev"})
    outcome = await run_sync(
        db, sc, page(contact("Delivered+Signup-Canary@resend.dev"), contact("real@example.com"))
    )
    assert outcome.skipped_canary == 1
    assert outcome.imported == 1
    assert [r["email"] for r in db.rows] == ["real@example.com"]


async def test_dry_run_counts_but_writes_nothing():
    db = FakeDb([owned(1, "leaving@example.com")])
    outcome = await run_sync(
        db, FakeSiteConfig(),
        page(contact("new@example.com"), contact("leaving@example.com", unsubscribed=True)),
        dry_run=True,
    )
    assert outcome.dry_run is True
    assert outcome.imported == 1
    assert outcome.unsubscribes_applied == 1
    assert [r["email"] for r in db.rows] == ["leaving@example.com"]
    assert db.row("leaving@example.com")["unsubscribed_at"] is None
    assert outcome.summary().startswith("2 contact(s) in the segment: would import 1")


async def test_one_bad_contact_does_not_strand_the_rest():
    db = FakeDb()
    db.fail_on = "boom@example.com"
    outcome = await run_sync(
        db, FakeSiteConfig(),
        page(contact("boom@example.com", cid="c-boom"), contact("fine@example.com")),
    )
    assert outcome.imported == 1
    assert len(outcome.errors) == 1
    # The error names the Resend contact id, never the address: these strings
    # become a finding body that ships to Discord.
    assert "c-boom" in outcome.errors[0]
    assert "boom@example.com" not in outcome.errors[0]


async def test_a_truncated_read_is_an_error_but_still_applies_what_it_read():
    db = FakeDb()
    sc = FakeSiteConfig({"newsletter_audience_sync_max_pages": "1"})
    outcome = await run_sync(db, sc, page(contact("a@x.com", cid="c1"), has_more=True))
    assert outcome.truncated is True
    assert outcome.imported == 1
    assert "newsletter_audience_sync_max_pages" in outcome.errors[0]


async def test_a_second_pull_changes_nothing():
    db = FakeDb()
    first = await run_sync(db, FakeSiteConfig(), page(contact("a@x.com")))
    second = await run_sync(db, FakeSiteConfig(), page(contact("a@x.com")))
    assert (first.imported, second.imported) == (1, 0)
    assert second.already_subscribed == 1
    assert len(db.rows) == 1


def test_metrics_carry_every_counter():
    metrics = na.AudienceSyncOutcome(imported=2, errors=["x"]).as_metrics()
    assert metrics["imported"] == 2
    assert metrics["errors"] == 1
    assert {
        "contacts_seen", "already_subscribed", "already_unsubscribed",
        "opted_out_kept", "unsubscribes_applied", "skipped_unsubscribed",
        "skipped_invalid", "skipped_canary", "truncated", "dry_run",
    } <= set(metrics)

"""Unit tests for services/pro_delivery.py (glad-labs-stack#3216).

Everything runs against a stateful in-memory fake of the two
``pro_subscriptions`` / ``revenue_events`` tables plus an
``httpx.MockTransport`` standing in for the Lemon Squeezy + GitHub APIs —
no network, no Postgres. The cases pin the access policy's observable
behavior: invite on purchase, revoke on expiry, cancelled-keeps-access,
missing-username degrades to a finding, and idempotency across repeat
passes.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import services.pro_delivery as pro_delivery
from services.pro_delivery import (
    ProDeliveryConfigError,
    ProDeliveryService,
    cli_link,
    normalize_github_username,
    run_sync,
)
from services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeConn:
    """Dispatches the exact queries pro_delivery issues onto dict state."""

    def __init__(self, db: FakeDb):
        self.db = db

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "SELECT 1 FROM pro_subscriptions" in query:
            return 1 if args[0] in self.db.subs else None
        if "SELECT github_username FROM pro_subscriptions" in query:
            row = self.db.subs.get(args[0])
            return None if row is None else row["github_username"]
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "SELECT github_username, github_invited_at" in query:
            row = self.db.subs.get(args[0])
            if row is None:
                return None
            return {
                "github_username": row["github_username"],
                "github_invited_at": row["github_invited_at"],
                "github_revoked_at": row["github_revoked_at"],
            }
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM pro_subscriptions" in query and "WHERE subscription_id = $1" in query:
            ref = str(args[0]).lower()
            out = []
            for sid, row in self.db.subs.items():
                if (
                    sid == args[0]
                    or sid.startswith(args[0])
                    or (row["customer_email"] or "").lower() == ref
                ):
                    out.append({"subscription_id": sid, **row})
            return out
        if "GROUP BY status" in query:
            return []
        if "ORDER BY last_seen_at" in query:
            return []
        if "SELECT key, value, is_secret FROM app_settings" in query:
            return [
                {"key": k, "value": v, "is_secret": s}
                for k, (v, s) in self.db.settings.items()
            ]
        raise AssertionError(f"unexpected fetch: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO pro_subscriptions" in query:
            (sub_id, order_id, status, product_id, variant_id, email, name,
             username, ends_at, renews_at, raw) = args
            existing = self.db.subs.get(sub_id)
            if existing is None:
                self.db.subs[sub_id] = {
                    "order_id": order_id,
                    "status": status,
                    "customer_email": email,
                    "customer_name": name,
                    "github_username": username,
                    "github_invited_at": None,
                    "github_revoked_at": None,
                    "ends_at": ends_at,
                    "renews_at": renews_at,
                    "raw": raw,
                }
            else:
                existing.update(
                    order_id=order_id,
                    status=status,
                    customer_email=email,
                    ends_at=ends_at,
                    renews_at=renews_at,
                    raw=raw,
                )
                # COALESCE(pro_subscriptions.github_username, EXCLUDED...)
                if existing["github_username"] is None:
                    existing["github_username"] = username
            return "INSERT 0 1"
        if "SET github_invited_at = NOW()" in query:
            row = self.db.subs[args[0]]
            row["github_invited_at"] = "now"
            row["github_revoked_at"] = None
            return "UPDATE 1"
        if "SET github_revoked_at = NOW()" in query:
            self.db.subs[args[0]]["github_revoked_at"] = "now"
            return "UPDATE 1"
        if "INSERT INTO revenue_events" in query:
            external_id = args[4]
            if any(r["external_id"] == external_id for r in self.db.revenue):
                return "INSERT 0 0"
            self.db.revenue.append(
                {"external_id": external_id, "amount_usd": args[0]}
            )
            return "INSERT 0 1"
        if "SET github_username = $2" in query:
            row = self.db.subs[args[0]]
            row["github_username"] = args[1]
            row["github_invited_at"] = None
            row["github_revoked_at"] = None
            return "UPDATE 1"
        if "SET github_username = NULL" in query:
            row = self.db.subs[args[0]]
            row["github_username"] = None
            if row["github_invited_at"] is not None:
                row["github_revoked_at"] = "now"
            return "UPDATE 1"
        if "INSERT INTO app_settings" in query:
            key, value = args[0], args[1]
            prior = self.db.settings.get(key)
            self.db.settings[key] = (value, prior[1] if prior else False)
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


class _Acquire:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeDb:
    def __init__(self) -> None:
        self.subs: dict[str, dict[str, Any]] = {}
        self.revenue: list[dict[str, Any]] = []
        self.settings: dict[str, tuple[str, bool]] = {}  # key -> (value, is_secret)
        self._conn = FakeConn(self)

    def acquire(self) -> _Acquire:
        return _Acquire(self._conn)


class GithubRecorder:
    """MockTransport handler for both LS and GitHub, recording GitHub calls."""

    def __init__(self, ls_page: dict[str, Any]):
        self.ls_page = ls_page
        self.invites: list[str] = []
        self.removals: list[str] = []
        self.invitation_cancels: list[str] = []
        self.pending_invitations: list[dict[str, Any]] = []
        self.fail_invite_for: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://api.lemonsqueezy.com/v1/subscriptions"):
            return httpx.Response(200, json=self.ls_page)
        if "/repos/" in url and "/invitations" in url and request.method == "GET":
            return httpx.Response(200, json=self.pending_invitations)
        if "/repos/" in url and "/invitations/" in url and request.method == "DELETE":
            self.invitation_cancels.append(url.rsplit("/", 1)[-1])
            return httpx.Response(204)
        if "/collaborators/" in url:
            username = url.rsplit("/", 1)[-1]
            if request.method == "PUT":
                if username in self.fail_invite_for:
                    return httpx.Response(500, text="boom")
                self.invites.append(username)
                return httpx.Response(201, json={"id": 1})
            if request.method == "DELETE":
                self.removals.append(username)
                return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {url}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def ls_sub(
    sub_id: str = "101",
    status: str = "active",
    order_id: int = 900,
    email: str = "buyer@example.com",
    custom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "status": status,
        "order_id": order_id,
        "product_id": 42,
        "variant_id": 7,
        "user_email": email,
        "user_name": "Buyer",
        "customer_id": 555,
        "ends_at": None,
        "renews_at": "2026-09-15T00:00:00.000000Z",
    }
    if custom is not None:
        attrs["custom_data"] = custom
    return {"type": "subscriptions", "id": sub_id, "attributes": attrs}


def ls_order(
    order_id: str = "900",
    custom: dict[str, Any] | None = None,
    total: int = 1900,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {"total": total, "currency": "USD"}
    if custom is not None:
        attrs["custom_data"] = custom
    return {"type": "orders", "id": order_id, "attributes": attrs}


def make_page(*subs: dict[str, Any], orders: list[dict[str, Any]] | None = None):
    return {"data": list(subs), "included": orders or [], "links": {}}


@pytest.fixture()
def site_config(monkeypatch: pytest.MonkeyPatch) -> SiteConfig:
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "ls-test-key")
    monkeypatch.setenv("PRO_DELIVERY_GITHUB_TOKEN", "gh-test-token")
    return SiteConfig(
        initial_config={
            "pro_delivery_enabled": "true",
            "pro_delivery_github_repo": "Glad-Labs/poindexter-pro",
        }
    )


@pytest.fixture()
def findings(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        pro_delivery, "emit_finding", lambda **kw: captured.append(kw)
    )
    return captured


async def _sync(page, site_config, db=None):
    db = db or FakeDb()
    recorder = GithubRecorder(page)
    outcome = await run_sync(
        db, site_config, transport=httpx.MockTransport(recorder)
    )
    return outcome, recorder, db


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("octocat", "octocat"),
        ("@octocat", "octocat"),
        ("  @Octo-Cat  ", "Octo-Cat"),
        ("https://github.com/octocat", "octocat"),
        ("github.com/octocat/", "octocat"),
        ("in--valid", None),
        ("-leading", None),
        ("trailing-", None),
        ("", None),
        (None, None),
        ("a" * 40, None),
    ],
)
def test_normalize_github_username(raw, expected):
    assert normalize_github_username(raw) == expected


# ---------------------------------------------------------------------------
# sync behavior
# ---------------------------------------------------------------------------


async def test_active_sub_with_username_is_invited_and_revenue_recorded(
    site_config, findings
):
    page = make_page(
        ls_sub(custom={"github_username": "@octocat"}),
        orders=[ls_order()],
    )
    outcome, recorder, db = await _sync(page, site_config)

    assert outcome.invited == ["octocat"]
    assert recorder.invites == ["octocat"]
    assert db.subs["101"]["github_invited_at"] is not None
    assert outcome.revenue_rows == 1
    assert db.revenue[0]["external_id"] == "ls_order_900"
    assert db.revenue[0]["amount_usd"] == pytest.approx(19.0)
    assert findings == []


async def test_username_from_included_order_custom_data(site_config, findings):
    page = make_page(
        ls_sub(),  # no custom_data on the subscription itself
        orders=[ls_order(custom={"github_username": "order-carrier"})],
    )
    outcome, recorder, _db = await _sync(page, site_config)
    assert recorder.invites == ["order-carrier"]
    assert outcome.missing_username == []


async def test_second_pass_is_idempotent(site_config, findings):
    page = make_page(
        ls_sub(custom={"github_username": "octocat"}), orders=[ls_order()]
    )
    db = FakeDb()
    await _sync(page, site_config, db=db)
    outcome2, recorder2, _ = await _sync(page, site_config, db=db)

    assert outcome2.invited == []           # no re-invite while delivered
    assert recorder2.invites == []
    assert outcome2.revenue_rows == 0       # ls_order_900 already recorded
    assert len(db.revenue) == 1


async def test_expired_sub_is_revoked_once(site_config, findings):
    active = make_page(
        ls_sub(custom={"github_username": "octocat"}), orders=[ls_order()]
    )
    db = FakeDb()
    await _sync(active, site_config, db=db)

    expired = make_page(
        ls_sub(status="expired", custom={"github_username": "octocat"}),
        orders=[ls_order()],
    )
    outcome, recorder, _ = await _sync(expired, site_config, db=db)
    assert outcome.revoked == ["octocat"]
    assert recorder.removals == ["octocat"]
    assert db.subs["101"]["github_revoked_at"] is not None

    # third pass: still expired — no second removal
    outcome3, recorder3, _ = await _sync(expired, site_config, db=db)
    assert outcome3.revoked == []
    assert recorder3.removals == []


async def test_cancelled_sub_keeps_access_until_expired(site_config, findings):
    """LS holds a cancelled sub in `cancelled` until ends_at passes — the
    buyer paid through the period, so access stays."""
    db = FakeDb()
    await _sync(
        make_page(ls_sub(custom={"github_username": "octocat"}), orders=[ls_order()]),
        site_config,
        db=db,
    )
    outcome, recorder, _ = await _sync(
        make_page(
            ls_sub(status="cancelled", custom={"github_username": "octocat"}),
            orders=[ls_order()],
        ),
        site_config,
        db=db,
    )
    assert outcome.revoked == []
    assert recorder.removals == []
    assert db.subs["101"]["github_revoked_at"] is None


async def test_missing_username_emits_actionable_finding(site_config, findings):
    page = make_page(ls_sub(), orders=[ls_order()])
    outcome, recorder, _db = await _sync(page, site_config)

    assert outcome.missing_username == ["101"]
    assert recorder.invites == []
    assert len(findings) == 1
    finding = findings[0]
    assert finding["kind"] == "pro_delivery_action_needed"
    assert "poindexter pro link 101" in finding["body"]
    assert finding["dedup_key"] == "pro_delivery_username_101"


async def test_unknown_status_touches_nothing(site_config, findings):
    page = make_page(
        ls_sub(status="some_future_status", custom={"github_username": "octocat"}),
        orders=[ls_order()],
    )
    outcome, recorder, db = await _sync(page, site_config)
    assert recorder.invites == [] and recorder.removals == []
    assert outcome.invited == [] and outcome.revoked == []
    assert db.subs["101"]["status"] == "some_future_status"  # still recorded


async def test_reactivated_after_revoke_is_reinvited(site_config, findings):
    db = FakeDb()
    await _sync(
        make_page(ls_sub(custom={"github_username": "octocat"}), orders=[ls_order()]),
        site_config,
        db=db,
    )
    await _sync(
        make_page(ls_sub(status="expired", custom={"github_username": "octocat"}),
                  orders=[ls_order()]),
        site_config,
        db=db,
    )
    outcome, recorder, _ = await _sync(
        make_page(ls_sub(status="active", custom={"github_username": "octocat"}),
                  orders=[ls_order()]),
        site_config,
        db=db,
    )
    assert outcome.invited == ["octocat"]
    assert recorder.invites == ["octocat"]
    assert db.subs["101"]["github_revoked_at"] is None


async def test_per_subscription_error_isolation(site_config, findings):
    page = make_page(
        ls_sub(sub_id="101", custom={"github_username": "broken-user"}),
        ls_sub(sub_id="102", order_id=901, email="two@example.com",
               custom={"github_username": "fine-user"}),
        orders=[ls_order("900"), ls_order("901")],
    )
    db = FakeDb()
    recorder = GithubRecorder(page)
    recorder.fail_invite_for = {"broken-user"}
    outcome = await run_sync(
        db, site_config, transport=httpx.MockTransport(recorder)
    )

    assert outcome.invited == ["fine-user"]          # 102 delivered anyway
    assert len(outcome.errors) == 1 and "101" in outcome.errors[0]
    assert any(f["kind"] == "pro_delivery_error" for f in findings)
    assert db.subs["101"]["github_invited_at"] is None  # stamp NOT set on failure


async def test_operator_set_username_wins_over_ls(site_config, findings):
    db = FakeDb()
    page = make_page(ls_sub(custom={"github_username": "from-ls"}), orders=[ls_order()])
    await _sync(page, site_config, db=db)
    db.subs["101"]["github_username"] = "operator-set"
    await _sync(page, site_config, db=db)
    assert db.subs["101"]["github_username"] == "operator-set"


async def test_missing_config_fails_loud_naming_every_key(monkeypatch):
    monkeypatch.delenv("LEMON_SQUEEZY_API_KEY", raising=False)
    monkeypatch.delenv("PRO_DELIVERY_GITHUB_TOKEN", raising=False)
    service = ProDeliveryService(
        pool=FakeDb(), site_config=SiteConfig(initial_config={})
    )
    with pytest.raises(ProDeliveryConfigError) as exc:
        await service.sync()
    msg = str(exc.value)
    assert "lemon_squeezy_api_key" in msg
    assert "pro_delivery_github_token" in msg
    assert "pro_delivery_github_repo" in msg


# ---------------------------------------------------------------------------
# cli_link
# ---------------------------------------------------------------------------


async def test_cli_link_attaches_username_and_delivers_now(site_config, findings):
    db = FakeDb()
    page = make_page(ls_sub(), orders=[ls_order()])  # no username anywhere
    await _sync(page, site_config, db=db)
    assert db.subs["101"]["github_username"] is None

    recorder = GithubRecorder(page)
    payload = await cli_link(
        db, site_config, "buyer@example.com", "@octocat",
        transport=httpx.MockTransport(recorder),
    )
    assert payload["ok"] is True
    assert payload["github_username"] == "octocat"
    assert payload["invited"] is True
    assert recorder.invites == ["octocat"]


async def test_cli_link_rejects_invalid_username(site_config):
    db = FakeDb()
    db.subs["101"] = {
        "order_id": "900", "status": "active",
        "customer_email": "buyer@example.com", "customer_name": "Buyer",
        "github_username": None, "github_invited_at": None,
        "github_revoked_at": None, "ends_at": None, "renews_at": None, "raw": "{}",
    }
    with pytest.raises(ValueError, match="not a valid GitHub username"):
        await cli_link(db, site_config, "101", "in--valid")


# ---------------------------------------------------------------------------
# pro apply — buyer-side seed diff/adopt
# ---------------------------------------------------------------------------

_APPLY_DEFAULTS = {
    "qa_overall_score_threshold": "70",
    "writer_temperature": "0.7",
    "writer_model": "llama3",
    "gpu_max_parallel": "1",
    "social_drafts_enabled": "false",
    "secret_thing": "",
}
_APPLY_METADATA = {"writer_model": {"value_type": "model"}}

_APPLY_SEED = {
    "qa_overall_score_threshold": "78",   # buyer on stock -> adoptable
    "writer_temperature": "0.8",          # buyer customized -> conflict
    "writer_model": "claude-sonnet-5",    # model pin -> review-held
    "gpu_max_parallel": "2",              # hardware hint -> review-held
    "social_drafts_enabled": "false",     # matches default (no live row) -> identical
    "mystery_overlay_key": "x",           # engine doesn't know it
    "secret_thing": "v",                  # live row is secret -> skipped
}


def _apply_db() -> FakeDb:
    db = FakeDb()
    db.settings = {
        "qa_overall_score_threshold": ("70", False),  # stock
        "writer_temperature": ("0.9", False),         # customized
        "secret_thing": ("enc:v1:x", True),
        # writer_model / gpu_max_parallel / social_drafts_enabled have no
        # live rows — the lazy seeder hasn't touched them, so their
        # effective value is the OSS default.
    }
    return db


def test_classify_seed_buckets_every_case():
    plan = pro_delivery.classify_seed(
        _APPLY_SEED, _apply_db().settings, _APPLY_DEFAULTS, _APPLY_METADATA
    )
    assert set(plan.adoptable) == {"qa_overall_score_threshold"}
    assert plan.adoptable["qa_overall_score_threshold"] == ("70", "78")
    assert set(plan.review) == {"writer_model", "gpu_max_parallel"}
    assert set(plan.conflicts) == {"writer_temperature"}
    assert plan.conflicts["writer_temperature"] == ("0.9", "0.8")
    assert plan.identical == 1
    assert plan.unknown_keys == ["mystery_overlay_key"]
    assert plan.secret_skipped == ["secret_thing"]


def test_resolve_seed_path_accepts_dir_and_file_and_fails_loud(tmp_path):
    repo = tmp_path / "poindexter-pro"
    (repo / "config").mkdir(parents=True)
    seed_file = repo / "config" / "seed-settings.json"
    seed_file.write_text("{}")

    assert pro_delivery.resolve_seed_path(str(repo)) == seed_file
    assert pro_delivery.resolve_seed_path(str(seed_file)) == seed_file
    with pytest.raises(ValueError, match="tried:.*nowhere"):
        pro_delivery.resolve_seed_path(str(tmp_path / "nowhere"))


def _write_seed(tmp_path) -> str:
    seed_file = tmp_path / "seed-settings.json"
    seed_file.write_text(json.dumps(_APPLY_SEED))
    return str(seed_file)


async def test_cli_apply_dry_run_writes_nothing(tmp_path):
    db = _apply_db()
    before = dict(db.settings)
    payload = await pro_delivery.cli_apply(
        db, _write_seed(tmp_path),
        defaults=_APPLY_DEFAULTS, metadata=_APPLY_METADATA,
    )
    assert payload["dry_run"] is True
    assert payload["applied"] == []
    assert db.settings == before
    assert payload["counts"]["adoptable"] == 1
    assert payload["counts"]["review_held"] == 2
    assert payload["counts"]["conflicts_kept"] == 1


async def test_cli_apply_default_adopts_only_stock_keys(tmp_path):
    db = _apply_db()
    payload = await pro_delivery.cli_apply(
        db, _write_seed(tmp_path), apply=True,
        defaults=_APPLY_DEFAULTS, metadata=_APPLY_METADATA,
    )
    assert payload["applied"] == ["qa_overall_score_threshold"]
    assert db.settings["qa_overall_score_threshold"] == ("78", False)
    # The operator's own tuning and the hardware-tuned pins are untouched.
    assert db.settings["writer_temperature"] == ("0.9", False)
    assert "writer_model" not in db.settings


async def test_cli_apply_escalation_flags_widen_the_write_set(tmp_path):
    db = _apply_db()
    payload = await pro_delivery.cli_apply(
        db, _write_seed(tmp_path), apply=True,
        include_models=True, overwrite_conflicts=True,
        defaults=_APPLY_DEFAULTS, metadata=_APPLY_METADATA,
    )
    assert set(payload["applied"]) == {
        "qa_overall_score_threshold", "writer_model", "gpu_max_parallel",
        "writer_temperature",
    }
    assert db.settings["writer_model"] == ("claude-sonnet-5", False)
    assert db.settings["writer_temperature"] == ("0.8", False)
    # Secrets and unknown keys are never written, even at full escalation.
    assert db.settings["secret_thing"] == ("enc:v1:x", True)
    assert "mystery_overlay_key" not in db.settings


# ---------------------------------------------------------------------------
# webhook-relay lookup (infrastructure/cloudflare/ls-webhook-relay)
#
# LS only exposes checkout custom_data in webhook payloads (verified live
# 2026-08-26, order 9315803) — the relay Worker parks it in CF KV and the
# sync reads it back. These cases pin the lookup contract: sub-key then
# order-key, fail-open on relay trouble, never consulted once a username is
# known, and half-set config fails loud.
# ---------------------------------------------------------------------------


class RelayRecorder(GithubRecorder):
    """GithubRecorder + a fake relay-Worker /lookup endpoint."""

    def __init__(
        self,
        ls_page: dict[str, Any],
        kv: dict[str, str] | None = None,
        kv_status_override: int | None = None,
    ):
        super().__init__(ls_page)
        self.kv = kv or {}
        self.kv_requests: list[str] = []
        self.kv_status_override = kv_status_override

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://relay.test/"):
            assert "/lookup/" in url
            # Read auth is the SHARED webhook signing secret — no CF token.
            assert (
                request.headers["Authorization"] == "Bearer whsec-test-secret"
            )
            key = url.rsplit("/lookup/", 1)[-1]
            self.kv_requests.append(key)
            if self.kv_status_override is not None:
                return httpx.Response(self.kv_status_override, text="relay boom")
            if key in self.kv:
                return httpx.Response(200, text=self.kv[key])
            return httpx.Response(404)
        return super().__call__(request)


@pytest.fixture()
def relay_site_config(monkeypatch: pytest.MonkeyPatch) -> SiteConfig:
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "ls-test-key")
    monkeypatch.setenv("PRO_DELIVERY_GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("LEMON_SQUEEZY_WEBHOOK_SECRET", "whsec-test-secret")
    return SiteConfig(
        initial_config={
            "pro_delivery_enabled": "true",
            "pro_delivery_github_repo": "Glad-Labs/poindexter-pro",
            "pro_delivery_relay_url": "https://relay.test",
        }
    )


def _kv_value(username: str) -> str:
    return json.dumps(
        {
            "event_name": "subscription_created",
            "order_id": "900",
            "subscription_id": "101",
            "custom_data": {"github_username": username},
            "stored_at": "2026-08-27T02:45:49Z",
        }
    )


async def _sync_relay(page, site_config, db=None, kv=None, kv_status_override=None):
    db = db or FakeDb()
    recorder = RelayRecorder(page, kv=kv, kv_status_override=kv_status_override)
    outcome = await run_sync(
        db, site_config, transport=httpx.MockTransport(recorder)
    )
    return outcome, recorder, db


async def test_relay_fills_missing_username_and_delivers(
    relay_site_config, findings
):
    page = make_page(ls_sub(), orders=[ls_order()])  # no username in REST
    kv = {"sub:101": _kv_value("relay-octocat")}
    outcome, recorder, db = await _sync_relay(page, relay_site_config, kv=kv)

    assert recorder.kv_requests == ["sub:101"]
    assert recorder.invites == ["relay-octocat"]
    assert outcome.invited == ["relay-octocat"]
    assert db.subs["101"]["github_username"] == "relay-octocat"
    assert findings == []


async def test_relay_falls_through_sub_key_to_order_key(
    relay_site_config, findings
):
    page = make_page(ls_sub(), orders=[ls_order()])
    kv = {"order:900": _kv_value("order-relay")}
    outcome, recorder, _db = await _sync_relay(page, relay_site_config, kv=kv)

    assert recorder.kv_requests == ["sub:101", "order:900"]
    assert recorder.invites == ["order-relay"]
    assert findings == []


async def test_relay_miss_degrades_to_manual_finding(relay_site_config, findings):
    page = make_page(ls_sub(), orders=[ls_order()])
    outcome, recorder, _db = await _sync_relay(page, relay_site_config, kv={})

    assert recorder.kv_requests == ["sub:101", "order:900"]
    assert outcome.missing_username == ["101"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "pro_delivery_action_needed"


async def test_relay_error_fails_open_to_manual_finding(
    relay_site_config, findings
):
    page = make_page(ls_sub(), orders=[ls_order()])
    outcome, recorder, _db = await _sync_relay(
        page, relay_site_config, kv_status_override=500
    )

    # A relay outage is a degraded lookup, not a sync failure: the designed
    # manual path fires and no per-subscription error is recorded.
    assert outcome.errors == []
    assert outcome.missing_username == ["101"]
    assert findings[0]["kind"] == "pro_delivery_action_needed"


async def test_relay_garbage_username_degrades_to_manual(
    relay_site_config, findings
):
    page = make_page(ls_sub(), orders=[ls_order()])
    kv = {"sub:101": _kv_value("in--valid")}
    outcome, recorder, _db = await _sync_relay(page, relay_site_config, kv=kv)

    # Invalid username in KV normalizes to None → keeps probing, then the
    # manual finding — a typo'd checkout field must not invite a stranger.
    assert recorder.kv_requests == ["sub:101", "order:900"]
    assert outcome.missing_username == ["101"]
    assert findings[0]["kind"] == "pro_delivery_action_needed"


async def test_relay_not_consulted_once_username_known(
    relay_site_config, findings
):
    page = make_page(ls_sub(), orders=[ls_order()])
    kv = {"sub:101": _kv_value("relay-octocat")}
    db = FakeDb()
    await _sync_relay(page, relay_site_config, db=db, kv=kv)
    outcome2, recorder2, _ = await _sync_relay(
        page, relay_site_config, db=db, kv=kv
    )

    # Steady state: the DB pre-check short-circuits, zero KV reads.
    assert recorder2.kv_requests == []
    assert outcome2.invited == []


async def test_relay_not_consulted_for_revoke_status_rows(
    relay_site_config, findings
):
    page = make_page(ls_sub(status="expired"), orders=[ls_order()])
    _outcome, recorder, _db = await _sync_relay(
        page, relay_site_config, kv={"sub:101": _kv_value("relay-octocat")}
    )
    # An expired sub can't be delivered to — don't burn KV reads on it.
    assert recorder.kv_requests == []


async def test_relay_disabled_makes_no_kv_requests(site_config, findings):
    page = make_page(ls_sub(), orders=[ls_order()])
    _outcome, recorder, _db = await _sync(page, site_config)
    # The plain fixture has no relay url; the recorder would assert on any
    # relay.test call, so reaching the finding proves no lookup traffic.
    assert findings[0]["kind"] == "pro_delivery_action_needed"


async def test_relay_halfset_config_fails_loud(monkeypatch):
    # relay_url set but the shared webhook secret deleted afterwards —
    # register couldn't have run without it, so this is a broken state the
    # operator must hear about, not silently degraded delivery.
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "ls-test-key")
    monkeypatch.setenv("PRO_DELIVERY_GITHUB_TOKEN", "gh-test-token")
    monkeypatch.delenv("LEMON_SQUEEZY_WEBHOOK_SECRET", raising=False)
    service = ProDeliveryService(
        pool=FakeDb(),
        site_config=SiteConfig(
            initial_config={
                "pro_delivery_enabled": "true",
                "pro_delivery_github_repo": "Glad-Labs/poindexter-pro",
                "pro_delivery_relay_url": "https://relay.test",
            }
        ),
    )
    with pytest.raises(ProDeliveryConfigError) as exc:
        await service.sync()
    assert "lemon_squeezy_webhook_secret" in str(exc.value)


# ---------------------------------------------------------------------------
# `pro relay register` / `remove` / `status` — LS webhook management
# ---------------------------------------------------------------------------


class LsAdminRecorder:
    """MockTransport handler for the LS webhooks/stores admin endpoints."""

    def __init__(
        self,
        webhooks: list[dict[str, Any]] | None = None,
        stores: list[dict[str, Any]] | None = None,
        delete_status: int = 204,
    ):
        self.webhooks = webhooks or []
        self.stores = (
            stores if stores is not None else [{"type": "stores", "id": "340440"}]
        )
        self.delete_status = delete_status
        self.created: list[dict[str, Any]] = []
        self.patched: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method
        if url.startswith("https://api.lemonsqueezy.com/v1/stores"):
            return httpx.Response(200, json={"data": self.stores})
        if url.startswith("https://api.lemonsqueezy.com/v1/webhooks"):
            if method == "GET":
                return httpx.Response(
                    200, json={"data": self.webhooks, "links": {}}
                )
            if method == "POST":
                body = json.loads(request.content)
                self.created.append(body)
                attrs = body["data"]["attributes"]
                return httpx.Response(
                    201,
                    json={
                        "data": {
                            "type": "webhooks",
                            "id": "555",
                            "attributes": {
                                "url": attrs["url"],
                                "events": attrs["events"],
                                "last_sent_at": None,
                            },
                        }
                    },
                )
            if method == "PATCH":
                wid = url.rsplit("/", 1)[-1]
                body = json.loads(request.content)
                self.patched.append((wid, body))
                attrs = body["data"]["attributes"]
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "type": "webhooks",
                            "id": wid,
                            "attributes": {
                                "url": attrs["url"],
                                "events": attrs["events"],
                                "last_sent_at": None,
                            },
                        }
                    },
                )
            if method == "DELETE":
                self.deleted.append(url.rsplit("/", 1)[-1])
                return httpx.Response(self.delete_status)
        raise AssertionError(f"unexpected request: {method} {url}")


def _stale_hook(hook_id: str = "93977", url: str = "https://dead.example/hook"):
    return {
        "type": "webhooks",
        "id": hook_id,
        "attributes": {
            "url": url,
            "events": ["order_created"],
            "last_sent_at": "2026-08-27T02:47:16.000000Z",
        },
    }


@pytest.fixture()
def relay_admin_site_config(monkeypatch: pytest.MonkeyPatch) -> SiteConfig:
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "ls-test-key")
    monkeypatch.setenv("LEMON_SQUEEZY_WEBHOOK_SECRET", "whsec-test-secret")
    return SiteConfig(initial_config={})


RELAY_URL = "https://ls-webhook-relay.example.workers.dev"


async def test_relay_register_creates_webhook_and_persists_url(
    relay_admin_site_config,
):
    db = FakeDb()
    recorder = LsAdminRecorder()
    payload = await pro_delivery.cli_relay_register(
        db, relay_admin_site_config, RELAY_URL,
        transport=httpx.MockTransport(recorder),
    )

    assert payload["action"] == "created"
    assert payload["webhook"]["url"] == RELAY_URL
    assert payload["other_webhooks"] == []
    [created] = recorder.created
    attrs = created["data"]["attributes"]
    assert attrs["events"] == list(pro_delivery.RELAY_WEBHOOK_EVENTS)
    assert attrs["secret"] == "whsec-test-secret"
    rel = created["data"]["relationships"]["store"]["data"]
    assert rel == {"type": "stores", "id": "340440"}
    assert db.settings["pro_delivery_relay_url"] == (RELAY_URL, False)


async def test_relay_register_updates_existing_same_url(relay_admin_site_config):
    recorder = LsAdminRecorder(webhooks=[_stale_hook("77", RELAY_URL)])
    payload = await pro_delivery.cli_relay_register(
        FakeDb(), relay_admin_site_config, RELAY_URL,
        transport=httpx.MockTransport(recorder),
    )
    assert payload["action"] == "updated"
    assert recorder.created == []
    [(wid, body)] = recorder.patched
    assert wid == "77"
    assert body["data"]["attributes"]["events"] == list(
        pro_delivery.RELAY_WEBHOOK_EVENTS
    )


async def test_relay_register_surfaces_stale_webhooks(relay_admin_site_config):
    recorder = LsAdminRecorder(webhooks=[_stale_hook()])
    payload = await pro_delivery.cli_relay_register(
        FakeDb(), relay_admin_site_config, RELAY_URL,
        transport=httpx.MockTransport(recorder),
    )
    assert payload["action"] == "created"
    [other] = payload["other_webhooks"]
    assert other["id"] == "93977"
    assert other["url"] == "https://dead.example/hook"


async def test_relay_register_rejects_non_https(relay_admin_site_config):
    with pytest.raises(ValueError, match="https"):
        await pro_delivery.cli_relay_register(
            FakeDb(), relay_admin_site_config, "http://insecure.example",
            transport=httpx.MockTransport(LsAdminRecorder()),
        )


async def test_relay_register_rejects_out_of_range_secret(monkeypatch):
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "ls-test-key")
    monkeypatch.setenv("LEMON_SQUEEZY_WEBHOOK_SECRET", "x" * 41)
    with pytest.raises(ValueError, match="6-40"):
        await pro_delivery.cli_relay_register(
            FakeDb(), SiteConfig(initial_config={}), RELAY_URL,
            transport=httpx.MockTransport(LsAdminRecorder()),
        )


async def test_relay_register_multiple_stores_requires_setting(
    relay_admin_site_config,
):
    recorder = LsAdminRecorder(
        stores=[
            {"type": "stores", "id": "1"},
            {"type": "stores", "id": "2"},
        ]
    )
    with pytest.raises(ValueError, match="pro_delivery_ls_store_id"):
        await pro_delivery.cli_relay_register(
            FakeDb(), relay_admin_site_config, RELAY_URL,
            transport=httpx.MockTransport(recorder),
        )


async def test_relay_remove_deletes_and_tolerates_gone(relay_admin_site_config):
    recorder = LsAdminRecorder()
    payload = await pro_delivery.cli_relay_remove(
        FakeDb(), relay_admin_site_config, "93977",
        transport=httpx.MockTransport(recorder),
    )
    assert payload == {"ok": True, "webhook_id": "93977", "existed": True}
    assert recorder.deleted == ["93977"]

    gone = LsAdminRecorder(delete_status=404)
    payload = await pro_delivery.cli_relay_remove(
        FakeDb(), relay_admin_site_config, "93977",
        transport=httpx.MockTransport(gone),
    )
    assert payload["existed"] is False


async def test_relay_status_reports_both_halves(monkeypatch):
    monkeypatch.setenv("LEMON_SQUEEZY_API_KEY", "ls-test-key")
    monkeypatch.setenv("LEMON_SQUEEZY_WEBHOOK_SECRET", "whsec-test-secret")
    sc = SiteConfig(initial_config={"pro_delivery_relay_url": RELAY_URL})
    recorder = LsAdminRecorder(webhooks=[_stale_hook()])
    payload = await pro_delivery.cli_relay_status(
        FakeDb(), sc, transport=httpx.MockTransport(recorder)
    )
    assert payload["config"]["pro_delivery_relay_url"] == RELAY_URL
    assert payload["config"]["lemon_squeezy_webhook_secret_set"] is True
    [hook] = payload["ls_webhooks"]
    assert hook["id"] == "93977"


async def test_relay_status_without_ls_key_degrades(monkeypatch):
    monkeypatch.delenv("LEMON_SQUEEZY_API_KEY", raising=False)
    monkeypatch.delenv("LEMON_SQUEEZY_WEBHOOK_SECRET", raising=False)
    payload = await pro_delivery.cli_relay_status(
        FakeDb(), SiteConfig(initial_config={})
    )
    assert "lemon_squeezy_api_key" in payload["ls_webhooks"]

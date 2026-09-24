"""image_text_scan — per-kind policy, honest verdicts, the /scan transport.

The contracts under test, each earned by an outage:

* text policy is per KIND, and an unknown kind is not scanned (a chart full of
  axis labels must never be "rejected for text");
* ``unavailable`` is never ``pass`` — a scan that could not run reports
  ``text_chars=None``, not 0 (poindexter#1004);
* the transport retries a restart window but never a verdict, and reports a
  server that predates ``/scan`` instead of retrying it.

The backend tests drive ``ImageGenServerScanBackend`` through an
``httpx.MockTransport`` — no socket is opened.
"""
from __future__ import annotations

import json

import httpx
import pytest

from poindexter.services import image_ocr_gate
from poindexter.services import image_text_scan as its
from poindexter.services.image_text_scan import (
    ImageGenServerScanBackend,
    ImageTextScan,
    RawTextScan,
    TextScanSettings,
    TextScanUnavailable,
    infer_image_kind_from_url,
    parse_kind_policy,
    policy_for_kind,
    scan_image_text,
    should_exclude,
)


class _SC:
    def __init__(self, values: dict | None = None) -> None:
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)


class _FakeBackend:
    name = "fake"

    def __init__(self, result: RawTextScan | Exception) -> None:
        self.result = result
        self.calls: list[tuple[bytes, float]] = []

    async def scan(self, image: bytes, *, min_confidence: float) -> RawTextScan:
        self.calls.append((image, min_confidence))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKindPolicy:
    def test_every_declared_provider_kind_has_the_intended_policy(self):
        assert policy_for_kind("generate") == its.POLICY_FORBIDDEN
        assert policy_for_kind("chart") == its.POLICY_EXPECTED
        assert policy_for_kind("screenshot") == its.POLICY_EXPECTED
        assert policy_for_kind("composed") == its.POLICY_EXPECTED
        assert policy_for_kind("search") == its.POLICY_NOT_APPLICABLE

    def test_every_registered_provider_kind_is_covered(self):
        """A provider that ships a new kind must get a deliberate policy — the
        default for an unknown kind is "not scanned", which is safe but
        silent, so this test is where the decision gets forced."""
        from poindexter.services.image_providers import (
            ai_generation,
            chart,
            flux_schnell,
            image_gen,
            pexels,
            pexels_video,
            screenshot,
        )

        kinds = {
            ai_generation.AIGenerationProvider.kind,
            chart.ChartProvider.kind,
            flux_schnell.FluxSchnellProvider.kind,
            image_gen.ImageGenProvider.kind,
            pexels.PexelsProvider.kind,
            pexels_video.PexelsVideoProvider.kind,
            screenshot.ScreenshotProvider.kind,
        }
        assert kinds <= set(its.DEFAULT_KIND_POLICY), kinds - set(its.DEFAULT_KIND_POLICY)

    @pytest.mark.parametrize("kind", [None, "", "video", "brand-new-kind"])
    def test_unknown_kind_is_not_scanned_rather_than_forbidden(self, kind):
        assert policy_for_kind(kind) is None

    def test_kind_is_case_and_space_insensitive(self):
        assert policy_for_kind(" Generate ") == its.POLICY_FORBIDDEN

    def test_setting_overrides_listed_kinds_only(self):
        sc = _SC({"image_text_kind_policy": "chart=forbidden,newkind=expected"})
        assert policy_for_kind("chart", sc) == its.POLICY_FORBIDDEN
        assert policy_for_kind("newkind", sc) == its.POLICY_EXPECTED
        # Unlisted kinds keep their code default.
        assert policy_for_kind("generate", sc) == its.POLICY_FORBIDDEN

    def test_malformed_entries_degrade_only_themselves(self):
        parsed = parse_kind_policy("generate=forbidden, chart=sometimes, =x, junk, search=expected")
        assert parsed == {"generate": "forbidden", "search": "expected"}


@pytest.mark.unit
class TestInferKindFromUrl:
    @pytest.mark.parametrize("url,kind", [
        ("https://cdn.x/images/charts/ab12.png", "chart"),
        ("https://cdn.x/images/screenshots/qa-rails-1234.png", "screenshot"),
        ("https://cdn.x/images/featured/brand-243f3123-aa.jpg", "composed"),
        ("https://cdn.x/images/featured/243f3123-711387d7.webp", "generate"),
        ("https://cdn.x/images/inline/abcdef123456.png", "generate"),
        ("https://images.pexels.com/photos/1/pexels-photo-1.jpeg", "search"),
    ])
    def test_known_prefixes(self, url, kind):
        assert infer_image_kind_from_url(url) == kind

    @pytest.mark.parametrize("url", [None, "", "https://r2.example.dev/a.png"])
    def test_unrecognised_url_says_nothing(self, url):
        assert infer_image_kind_from_url(url) is None


# ---------------------------------------------------------------------------
# scan_image_text — the verdict contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestScanImageText:
    async def test_expected_kind_is_not_sent_to_the_scanner(self):
        backend = _FakeBackend(RawTextScan(500, 80.0, 30))
        scan = await scan_image_text(b"img", kind="chart", backend=backend)
        assert scan.status == its.STATUS_NOT_SCANNED
        assert scan.policy == its.POLICY_EXPECTED
        assert scan.text_chars is None
        assert backend.calls == []

    async def test_unknown_kind_is_not_scanned(self):
        backend = _FakeBackend(RawTextScan(500, 80.0, 30))
        scan = await scan_image_text(b"img", kind="hologram", backend=backend)
        assert scan.status == its.STATUS_NOT_SCANNED
        assert scan.policy is None
        assert backend.calls == []

    async def test_disabled_gate_makes_no_claim(self):
        backend = _FakeBackend(RawTextScan(0, 0.0, 0))
        scan = await scan_image_text(
            b"img", kind="generate", backend=backend,
            site_config=_SC({"image_ocr_gate_enabled": "false"}),
        )
        assert scan.status == its.STATUS_DISABLED
        assert not scan.measured
        assert backend.calls == []

    async def test_under_threshold_passes_with_measured_coverage(self):
        backend = _FakeBackend(RawTextScan(4, 1.5, 1))
        scan = await scan_image_text(b"img", kind="generate", backend=backend)
        assert scan.status == its.STATUS_PASS
        assert scan.measured
        assert (scan.text_chars, scan.coverage_pct, scan.max_chars) == (4, 1.5, 6)

    async def test_over_threshold_fails(self):
        backend = _FakeBackend(RawTextScan(10, 38.0, 2))
        scan = await scan_image_text(b"img", kind="generate", backend=backend)
        assert scan.status == its.STATUS_FAIL
        assert scan.coverage_pct == 38.0

    async def test_threshold_and_confidence_are_the_gate_settings(self):
        """One rule, one set of knobs: the same image_ocr_gate_* rows the
        server applies to its own renders."""
        backend = _FakeBackend(RawTextScan(10, 5.0, 2))
        scan = await scan_image_text(
            b"img", kind="generate", backend=backend,
            site_config=_SC({
                "image_ocr_gate_max_chars": "12",
                "image_ocr_gate_min_confidence": "0.55",
            }),
        )
        assert scan.status == its.STATUS_PASS
        assert backend.calls[0][1] == pytest.approx(0.55)

    async def test_backend_failure_is_unavailable_never_clean(self):
        backend = _FakeBackend(TextScanUnavailable("server down"))
        scan = await scan_image_text(b"img", kind="generate", backend=backend)
        assert scan.status == its.STATUS_UNAVAILABLE
        assert scan.text_chars is None
        assert scan.coverage_pct is None
        assert not scan.measured
        assert "server down" in scan.reason

    async def test_unexpected_backend_exception_is_unavailable(self):
        backend = _FakeBackend(RuntimeError("boom"))
        scan = await scan_image_text(b"img", kind="generate", backend=backend)
        assert scan.status == its.STATUS_UNAVAILABLE

    async def test_unreadable_path_is_unavailable(self, tmp_path):
        backend = _FakeBackend(RawTextScan(0, 0.0, 0))
        scan = await scan_image_text(
            tmp_path / "missing.png", kind="generate", backend=backend,
        )
        assert scan.status == its.STATUS_UNAVAILABLE
        assert backend.calls == []

    async def test_path_input_sends_the_file_bytes(self, tmp_path):
        f = tmp_path / "x.png"
        f.write_bytes(b"PIXELS")
        backend = _FakeBackend(RawTextScan(0, 0.0, 0))
        await scan_image_text(str(f), kind="generate", backend=backend)
        assert backend.calls[0][0] == b"PIXELS"

    async def test_default_backend_is_isolated_in_unit_tests(self):
        """The conftest stub makes the real backend report unavailable — the
        degraded path — rather than reaching image-gen."""
        scan = await scan_image_text(b"img", kind="generate")
        assert scan.status == its.STATUS_UNAVAILABLE


@pytest.mark.unit
class TestShouldExclude:
    def _s(self, **kw) -> TextScanSettings:
        return TextScanSettings(**kw)

    def test_fail_blocks_under_enforce(self):
        assert should_exclude(ImageTextScan(status="fail"), self._s(enforce=True))

    def test_fail_only_annotates_when_enforce_is_off(self):
        assert not should_exclude(ImageTextScan(status="fail"), self._s(enforce=False))

    def test_unavailable_passes_by_default(self):
        """A broken OCR dep must not become a total image outage."""
        assert not should_exclude(ImageTextScan(status="unavailable"), self._s())

    def test_unavailable_blocks_when_fail_closed(self):
        assert should_exclude(
            ImageTextScan(status="unavailable"),
            self._s(fail_closed_when_unavailable=True),
        )

    @pytest.mark.parametrize("status", ["pass", "disabled", "not_scanned"])
    def test_other_statuses_never_block(self, status):
        assert not should_exclude(
            ImageTextScan(status=status), self._s(fail_closed_when_unavailable=True),
        )


@pytest.mark.unit
class TestSettings:
    def test_server_url_falls_back_to_image_gen_server_url(self):
        cfg = TextScanSettings.from_site_config(_SC({"image_gen_server_url": "http://ig:1/"}))
        assert cfg.server_url == "http://ig:1"

    def test_dedicated_url_wins(self):
        cfg = TextScanSettings.from_site_config(_SC({
            "image_gen_server_url": "http://ig:1",
            "image_text_scan_server_url": "http://ocr:2",
        }))
        assert cfg.server_url == "http://ocr:2"

    def test_malformed_values_fall_back_per_field(self):
        cfg = TextScanSettings.from_site_config(_SC({
            "image_ocr_gate_max_chars": "lots",
            "image_text_scan_attempts": "0",
            "image_ocr_gate_min_confidence": "7",
        }))
        assert cfg.max_chars == 6
        assert cfg.attempts == 1  # clamped, never zero attempts
        assert cfg.min_confidence == 1.0

    def test_to_dict_drops_unset_fields(self):
        d = ImageTextScan(status="unavailable", kind="generate", reason="x").to_dict()
        assert d == {"status": "unavailable", "kind": "generate", "reason": "x"}
        assert "text_chars" not in d  # absent, never a fabricated 0


@pytest.mark.unit
def test_image_ocr_gate_reexports_the_same_objects():
    """Backcompat: existing imports keep working and share identity."""
    assert image_ocr_gate.is_ocr_gate_rejection is its.is_ocr_gate_rejection
    assert image_ocr_gate.describe_ocr_gate_rejection is its.describe_ocr_gate_rejection
    assert image_ocr_gate.safe_json is its.safe_json
    assert image_ocr_gate.OCR_GATE_REJECTED_STATUS == 422
    assert image_ocr_gate.OCR_GATE_REJECTED_ERROR == "ocr_gate_rejected"


# ---------------------------------------------------------------------------
# ImageGenServerScanBackend — transport contract via httpx.MockTransport
# ---------------------------------------------------------------------------


def _backend(handler, **settings) -> tuple[ImageGenServerScanBackend, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _h(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    client = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    cfg = TextScanSettings(server_url="http://ig:9836", backoff_s=0.0, **settings)
    return ImageGenServerScanBackend(cfg, client=client), seen


def _ok(chars=3, coverage=2.5):
    return httpx.Response(200, json={
        "text_chars": chars, "text_coverage_pct": coverage, "detections": 1,
    })


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.real_image_text_scan
class TestImageGenServerBackend:
    async def test_posts_raw_bytes_with_confidence(self):
        backend, seen = _backend(lambda r, n: _ok())
        raw = await backend.scan(b"PNGBYTES", min_confidence=0.4)
        assert raw == RawTextScan(3, 2.5, 1)
        req = seen[0]
        assert req.url.path == "/scan"
        assert req.url.params["min_confidence"] == "0.4"
        assert req.content == b"PNGBYTES"

    async def test_restart_window_is_retried(self):
        def handler(r, n):
            if n < 3:
                raise httpx.ConnectError("refused", request=r)
            return _ok()

        backend, seen = _backend(handler, attempts=4)
        raw = await backend.scan(b"x", min_confidence=0.3)
        assert raw.text_chars == 3
        assert len(seen) == 3

    async def test_bare_503_is_retried_then_reported(self):
        backend, seen = _backend(lambda r, n: httpx.Response(503), attempts=3)
        with pytest.raises(TextScanUnavailable, match="after 3 attempt"):
            await backend.scan(b"x", min_confidence=0.3)
        assert len(seen) == 3

    async def test_ocr_engine_failure_is_not_retried(self):
        body = {"detail": {"error": "ocr_unavailable", "message": "no easyocr"}}
        backend, seen = _backend(lambda r, n: httpx.Response(503, json=body), attempts=4)
        with pytest.raises(TextScanUnavailable, match="ocr_unavailable"):
            await backend.scan(b"x", min_confidence=0.3)
        assert len(seen) == 1

    async def test_server_without_scan_endpoint_says_rebuild(self):
        backend, seen = _backend(lambda r, n: httpx.Response(404), attempts=4)
        with pytest.raises(TextScanUnavailable, match="predates POST /scan"):
            await backend.scan(b"x", min_confidence=0.3)
        assert len(seen) == 1

    async def test_request_rejection_is_not_retried(self):
        body = {"detail": {"error": "not_an_image"}}
        backend, seen = _backend(lambda r, n: httpx.Response(400, json=body), attempts=4)
        with pytest.raises(TextScanUnavailable, match="not_an_image"):
            await backend.scan(b"x", min_confidence=0.3)
        assert len(seen) == 1

    @pytest.mark.parametrize("payload", [
        {"text_chars": 3},
        {"text_chars": "many", "text_coverage_pct": 1.0},
        {"text_chars": -1, "text_coverage_pct": 1.0},
        ["not", "a", "dict"],
    ])
    async def test_unreadable_200_is_unavailable_not_zero(self, payload):
        backend, _ = _backend(
            lambda r, n: httpx.Response(200, content=json.dumps(payload).encode()),
        )
        with pytest.raises(TextScanUnavailable):
            await backend.scan(b"x", min_confidence=0.3)

    async def test_coverage_is_clamped(self):
        backend, _ = _backend(lambda r, n: _ok(coverage=140.0))
        raw = await backend.scan(b"x", min_confidence=0.3)
        assert raw.coverage_pct == 100.0

    async def test_end_to_end_through_scan_image_text(self):
        backend, _ = _backend(lambda r, n: _ok(chars=40, coverage=33.0))
        scan = await scan_image_text(b"x", kind="generate", backend=backend)
        assert scan.status == its.STATUS_FAIL
        assert scan.backend == "image_gen_server"
        assert scan.coverage_pct == 33.0

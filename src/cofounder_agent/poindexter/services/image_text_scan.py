"""Does this rendered image carry text it shouldn't? — answered for ANY image.

The text-leakage check used to exist in exactly one place: inside
``scripts/image-gen-server.py``'s ``/generate``, welded to that server's own
renders. Everything else went unscanned. The featured fan-out renders four
candidates — ``zimage`` on image-gen plus ``schnell`` / ``klein`` / ``qwen`` on
ComfyUI — and only zimage faced the gate: over 30 days it was ejected from 19
of 74 contests as ``ocr_gate_rejected`` (26%), while the three that were never
held to the rule won 77% of heroes. Their "text discipline" was a
positive-prompt clause measured NOT to work (25.33 leaked chars/image with it
applied) and a vision judge that scored a hero 95 while citing its gibberish
headline "TÝMENEITUR" as "its title" (task 243f3123).

This module is the backend-agnostic answer. Give it an image — a path or
bytes — and the *kind* of provider that produced it, and it returns a measured
:class:`ImageTextScan`: leaked character count AND the share of the frame
covered by text (from the OCR bounding boxes the old gate threw away).

Four rules it keeps, each earned by an outage:

**Text policy is per kind, and an unknown kind is NOT scanned.** Image
providers declare a ``kind`` (``generate`` / ``chart`` / ``screenshot`` /
``search``, see ``plugins/image_provider.py``). Charts and screenshots are
*supposed* to be full of text; a blanket no-text rule would reject every
``[CHART:]`` render and every operator-surface screenshot. So text is
forbidden for ``generate``, expected for ``chart`` / ``screenshot`` /
``composed`` (the brand hero — real type set in HTML), and not applicable for
``search`` (a stock photo's street sign is a real sign, not a generator
defect). A kind this table does not know gets ``not_scanned`` — adding a
provider must never silently start rejecting its output. Operators override
per kind with ``image_text_kind_policy``.

**``unavailable`` is not ``pass``.** A scan that could not run records
``text_chars=None`` and status ``unavailable`` — "could not verify", never
"verified clean". Conflating the two is exactly what let a silently-missing
easyocr report a passing gate on every image it never scanned
(poindexter#1004: 67 of 693 renders shipped leaking, one with "Poindexter
Philosophy" across the top). Whether an unverifiable image is *blocked* is
``image_ocr_gate_fail_closed_when_unavailable`` (default false: a broken OCR
dependency must not become a total image outage) — see :func:`should_exclude`.

**An OCR rejection is a verdict, not a window.** The ordinary render failures
callers retry — image-gen restarting for a VRAM reclaim, a GPU lock timeout —
clear in seconds. A rejection does not clear: the image-gen server already
re-rolled the seed ``image_ocr_gate_max_attempts`` times before returning HTTP
422, and a fresh scan of the same pixels returns the same count. Callers must
treat :data:`OCR_GATE_REJECTED_STATUS` / a ``fail`` scan as terminal and take
their no-image path. (The scan *transport* is different — a connection refused
while image-gen restarts IS a window, and :class:`ImageGenServerScanBackend`
retries it.)

**One rule, one set of knobs.** The verdict threshold is the same
``image_ocr_gate_max_chars`` the server gate applies to its own renders, with
the same ``image_ocr_gate_min_confidence``, ``_enabled`` and ``_enforce`` — so
a fan-out contest judges all four candidates by the identical rule rather than
a second, drifting copy of it.

Where the scanner runs: ``POST {image_gen_server_url}/scan`` (override with
``image_text_scan_server_url``). The reader there is already resident and
CPU-only, so scanning competes with no render for VRAM and the worker image
does not grow easyocr + torch. That is a coupling — if image-gen is down,
scanning is ``unavailable`` — and it is behind :class:`TextScanBackend` so the
engine can move without touching a consumer.

This module lives in ``services/`` (substrate), not ``modules/content/``, so
``services/`` callers (the fan-out, the video renderer, ``flux_schnell``) can
use it without importing backwards across the module boundary.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# /generate HTTP-422 vocabulary (formerly services/image_ocr_gate.py — that
# module now re-exports these names for backcompat).
# ---------------------------------------------------------------------------

#: HTTP status the image-gen server returns when a render is blocked by the
#: gate. 422 rather than 5xx: the render itself succeeded, its *content* is
#: unacceptable, and the request is not worth repeating unchanged.
OCR_GATE_REJECTED_STATUS = 422

#: ``detail.error`` marker on the rejection body. Checked (rather than
#: assuming every 422 is a gate rejection) so an unrelated future 422 from
#: this endpoint can't be silently read as a text-leak verdict.
OCR_GATE_REJECTED_ERROR = "ocr_gate_rejected"


def is_ocr_gate_rejection(status_code: int, body: Any) -> bool:
    """Whether this ``/generate`` response is an OCR-gate content rejection.

    ``body`` is the parsed JSON response (or anything at all — a non-dict,
    a parse failure sentinel, ``None``); only the documented shape matches.
    """
    if status_code != OCR_GATE_REJECTED_STATUS:
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    return isinstance(detail, dict) and detail.get("error") == OCR_GATE_REJECTED_ERROR


def describe_ocr_gate_rejection(body: Any) -> str:
    """One-line, log-safe summary of a rejection body.

    Falls back to a generic phrase rather than raising or returning ``''``:
    this only ever feeds an operator-facing log line, and a malformed body
    still needs to say *something* about why an image went missing.
    """
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return "OCR text-leakage gate rejected the render (no detail returned)"
    status = detail.get("ocr_gate_status", "?")
    chars = detail.get("ocr_text_chars")
    threshold = detail.get("threshold", "?")
    attempts = detail.get("ocr_gate_attempts", "?")
    return (
        f"OCR text-leakage gate rejected the render "
        f"(status={status}, chars={'unavailable' if chars is None else chars}, "
        f"threshold={threshold}, server attempts={attempts})"
    )


def safe_json(resp: Any) -> Any:
    """``resp.json()`` that returns ``None`` instead of raising.

    A rejection body is only ever used to explain a failure that has already
    happened, so a malformed/empty body must degrade to "no detail" rather
    than replace the real outcome with a JSON parse error — the caller still
    has to take its no-image path either way.

    Broad on purpose (the response object is whatever the HTTP client hands
    back), but not silent: an image-gen response that won't parse means the
    caller is about to report a render failure it can't explain, and that is
    worth an operator seeing.
    """
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — degraded diagnostics, logged below
        logger.warning(
            "[OCR-GATE] could not parse image-gen response body (%s: %s) — "
            "the render failure below will be reported without detail",
            type(exc).__name__, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Per-kind text policy
# ---------------------------------------------------------------------------

KIND_GENERATE = "generate"
KIND_CHART = "chart"
KIND_SCREENSHOT = "screenshot"
#: Composed from real type, not generated — ``services/brand_hero.py``.
KIND_COMPOSED = "composed"
KIND_SEARCH = "search"

#: Text in the frame is a defect — the generator was told to draw none.
POLICY_FORBIDDEN = "forbidden"
#: Text is the content (a chart's axes, a dashboard screenshot, a brand mark).
POLICY_EXPECTED = "expected"
#: Not ours to judge — a stock photo's signage is a real sign.
POLICY_NOT_APPLICABLE = "not_applicable"

_POLICIES = frozenset({POLICY_FORBIDDEN, POLICY_EXPECTED, POLICY_NOT_APPLICABLE})

#: Code-side table. ``image_text_kind_policy`` overlays it per kind; a kind in
#: neither is unknown and is not scanned (see :func:`policy_for_kind`).
DEFAULT_KIND_POLICY: dict[str, str] = {
    KIND_GENERATE: POLICY_FORBIDDEN,
    KIND_CHART: POLICY_EXPECTED,
    KIND_SCREENSHOT: POLICY_EXPECTED,
    KIND_COMPOSED: POLICY_EXPECTED,
    KIND_SEARCH: POLICY_NOT_APPLICABLE,
}

_KIND_POLICY_KEY = "image_text_kind_policy"


def parse_kind_policy(raw: Any) -> dict[str, str]:
    """Parse ``kind=policy,kind=policy`` into a dict, dropping bad entries.

    A malformed entry is logged and skipped rather than raising: one typo in
    the setting must degrade that kind to its code default, never disable
    scanning for every kind.
    """
    out: dict[str, str] = {}
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        kind, sep, policy = part.partition("=")
        kind, policy = kind.strip().lower(), policy.strip().lower()
        if not sep or not kind or policy not in _POLICIES:
            logger.warning(
                "[TEXT-SCAN] ignoring malformed %s entry %r — expected "
                "kind=<%s>", _KIND_POLICY_KEY, part, "|".join(sorted(_POLICIES)),
            )
            continue
        out[kind] = policy
    return out


def resolve_kind_policy(site_config: Any) -> dict[str, str]:
    """The effective kind→policy table: code defaults overlaid by the setting."""
    table = dict(DEFAULT_KIND_POLICY)
    table.update(parse_kind_policy(_get(site_config, _KIND_POLICY_KEY, "")))
    return table


def policy_for_kind(kind: str | None, site_config: Any = None) -> str | None:
    """Text policy for a provider kind, or ``None`` for an unknown kind.

    ``None`` means *do not scan*. The safe default for a kind nobody declared
    a policy for is to make no claim about it — defaulting to ``forbidden``
    would let a newly added provider start having its output rejected with
    no one having decided that.
    """
    if not kind:
        return None
    return resolve_kind_policy(site_config).get(str(kind).strip().lower())


def infer_image_kind_from_url(url: str | None) -> str | None:
    """Best-effort provider kind for an image known only by its URL.

    For consumers that meet an image after upload — the qa.vision rail sees
    URLs, not provider results. Keyed on the object-store prefixes each
    provider writes under (``images/charts/`` in ``image_providers/chart.py``,
    ``images/screenshots/`` in ``screenshot.py``, ``images/featured/brand-`` in
    ``post_edit_service.brand_hero``) and the Pexels CDN host. Returns ``None``
    when the URL says nothing — and ``None`` means not scanned, so an
    unrecognised URL keeps whatever the caller did before.
    """
    u = str(url or "").lower()
    if not u:
        return None
    if "/images/charts/" in u:
        return KIND_CHART
    if "/images/screenshots/" in u:
        return KIND_SCREENSHOT
    if "/images/featured/brand-" in u:
        return KIND_COMPOSED
    if "pexels.com" in u:
        return KIND_SEARCH
    if "/images/featured/" in u or "/images/inline/" in u:
        return KIND_GENERATE
    return None


# ---------------------------------------------------------------------------
# Result + settings
# ---------------------------------------------------------------------------

#: Same strings the image-gen server's gate reports (OCR_STATUS_* there) — the
#: console and Grafana read both producers' rows with one vocabulary.
STATUS_PASS = "pass"                # scanned, at or under max_chars
STATUS_FAIL = "fail"                # scanned, over max_chars
STATUS_UNAVAILABLE = "unavailable"  # could not scan — NOT a pass
STATUS_DISABLED = "disabled"        # image_ocr_gate_enabled=false; no claim
#: The kind's policy says there is nothing to check (expected / n/a / unknown).
STATUS_NOT_SCANNED = "not_scanned"


@dataclass(frozen=True)
class ImageTextScan:
    """One image's text verdict.

    ``text_chars`` / ``coverage_pct`` are ``None`` unless the image was
    actually scanned. Readers must never coerce ``None`` to 0 — that is the
    whole difference between ``unavailable`` and ``pass``.
    """

    status: str
    kind: str | None = None
    policy: str | None = None
    text_chars: int | None = None
    coverage_pct: float | None = None
    detections: int | None = None
    max_chars: int | None = None
    backend: str = ""
    reason: str = ""

    @property
    def measured(self) -> bool:
        """True only when a scanner actually looked at the pixels."""
        return self.status in (STATUS_PASS, STATUS_FAIL)

    def to_dict(self) -> dict[str, Any]:
        """Compact JSON-able form for audit rows (``None`` fields dropped)."""
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


@dataclass(frozen=True)
class TextScanSettings:
    """Every knob a scan reads, resolved once per call from site_config."""

    enabled: bool = True
    max_chars: int = 6
    min_confidence: float = 0.3
    enforce: bool = True
    fail_closed_when_unavailable: bool = False
    server_url: str = "http://image-gen-server:9836"
    timeout_s: float = 90.0
    attempts: int = 4
    backoff_s: float = 5.0

    @classmethod
    def from_site_config(cls, site_config: Any) -> TextScanSettings:
        d = cls()
        server_url = str(_get(site_config, "image_text_scan_server_url", "") or "").strip()
        if not server_url:
            server_url = str(
                _get(site_config, "image_gen_server_url", d.server_url) or d.server_url,
            ).strip()
        return cls(
            enabled=_bool(site_config, "image_ocr_gate_enabled", d.enabled),
            max_chars=max(0, _int(site_config, "image_ocr_gate_max_chars", d.max_chars)),
            min_confidence=min(1.0, max(0.0, _float(
                site_config, "image_ocr_gate_min_confidence", d.min_confidence))),
            enforce=_bool(site_config, "image_ocr_gate_enforce", d.enforce),
            fail_closed_when_unavailable=_bool(
                site_config, "image_ocr_gate_fail_closed_when_unavailable",
                d.fail_closed_when_unavailable),
            server_url=server_url.rstrip("/"),
            timeout_s=max(1.0, _float(
                site_config, "image_text_scan_timeout_seconds", d.timeout_s)),
            attempts=max(1, _int(site_config, "image_text_scan_attempts", d.attempts)),
            backoff_s=max(0.0, _float(
                site_config, "image_text_scan_retry_backoff_seconds", d.backoff_s)),
        )


def should_exclude(scan: ImageTextScan, settings: TextScanSettings) -> bool:
    """Whether this verdict must keep the image out (of a contest, a post).

    Mirrors the server's ``should_reject_render`` exactly, so a ComfyUI
    candidate and a zimage render are held to the same rule:

    * ``fail`` blocks under ``image_ocr_gate_enforce`` — the gate doing its job.
    * ``unavailable`` blocks only when the operator ALSO opted into
      ``image_ocr_gate_fail_closed_when_unavailable``; otherwise a broken OCR
      dependency would turn into a total image outage. The ``unavailable``
      status still travels with the image, so the degradation stays visible.
    * everything else (pass / disabled / not_scanned) never blocks.
    """
    if scan.status == STATUS_FAIL:
        return settings.enforce
    if scan.status == STATUS_UNAVAILABLE:
        return settings.enforce and settings.fail_closed_when_unavailable
    return False


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawTextScan:
    """What a backend measured, before any policy is applied."""

    text_chars: int
    coverage_pct: float
    detections: int


class TextScanUnavailable(Exception):
    """The backend could not produce a measurement. Never means "clean"."""


class TextScanBackend(Protocol):
    """Anything that can OCR an image. The seam that lets the engine move."""

    name: str

    async def scan(self, image: bytes, *, min_confidence: float) -> RawTextScan: ...


class ImageGenServerScanBackend:
    """``POST {server_url}/scan`` on the image-gen server.

    Retries the transport, never the verdict. A connection error or a bare
    502/503/504 is the server restarting — the fan-out hard-unloads image-gen
    (a process exit) right before rendering the ComfyUI candidates, so the
    first scans can land while it is still coming back. A 503 carrying
    ``ocr_unavailable`` is the OCR engine itself failing and is reported at
    once; so is a 404, which means the running server predates ``/scan`` and
    needs a rebuild — retrying either just delays the honest answer.
    """

    name = "image_gen_server"

    def __init__(
        self, settings: TextScanSettings, *, client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    async def scan(self, image: bytes, *, min_confidence: float) -> RawTextScan:
        s = self._settings
        url = f"{s.server_url}/scan"
        last = "no attempt made"
        for attempt in range(1, s.attempts + 1):
            try:
                resp = await self._post(url, image, min_confidence)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = describe_exception(exc)
            else:
                if resp.status_code == 200:
                    return _parse_scan_body(resp)
                body = safe_json(resp)
                detail = body.get("detail") if isinstance(body, dict) else None
                error = detail.get("error") if isinstance(detail, dict) else None
                if resp.status_code == 404:
                    raise TextScanUnavailable(
                        f"{url} returned 404 — the running image-gen server "
                        "predates POST /scan; rebuild poindexter-image-gen-server",
                    )
                if resp.status_code in (502, 503, 504) and error != "ocr_unavailable":
                    last = f"HTTP {resp.status_code}"
                else:
                    msg = detail.get("message") if isinstance(detail, dict) else None
                    raise TextScanUnavailable(
                        f"{url} returned HTTP {resp.status_code}"
                        f" ({error or 'no error code'}{': ' + str(msg) if msg else ''})",
                    )
            if attempt < s.attempts:
                await asyncio.sleep(s.backoff_s)
        raise TextScanUnavailable(
            f"{url} unreachable after {s.attempts} attempt(s): {last}",
        )

    async def _post(self, url: str, image: bytes, min_confidence: float) -> httpx.Response:
        kwargs: dict[str, Any] = {
            "content": image,
            "params": {"min_confidence": min_confidence},
            "headers": {"Content-Type": "application/octet-stream"},
            "timeout": httpx.Timeout(self._settings.timeout_s, connect=5.0),
        }
        if self._client is not None:
            return await self._client.post(url, **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.post(url, **kwargs)


def _parse_scan_body(resp: httpx.Response) -> RawTextScan:
    body = safe_json(resp)
    if not isinstance(body, dict):
        raise TextScanUnavailable("scan response was not a JSON object")
    try:
        chars = int(body["text_chars"])
        coverage = float(body["text_coverage_pct"])
        detections = int(body.get("detections", 0) or 0)
    except (KeyError, TypeError, ValueError) as exc:
        # A 200 we cannot read is still "could not verify" — inventing a 0 out
        # of a malformed body is the #1004 failure in a new place.
        raise TextScanUnavailable(
            f"scan response missing text_chars/text_coverage_pct ({describe_exception(exc)})",
        ) from exc
    if chars < 0 or coverage != coverage:  # negative / NaN
        raise TextScanUnavailable(f"scan response out of range: chars={chars} coverage={coverage}")
    return RawTextScan(
        text_chars=chars, coverage_pct=max(0.0, min(100.0, coverage)), detections=detections,
    )


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


async def scan_image_text(
    image: str | os.PathLike[str] | bytes,
    *,
    kind: str | None,
    site_config: Any = None,
    backend: TextScanBackend | None = None,
    settings: TextScanSettings | None = None,
) -> ImageTextScan:
    """Scan one image for text its kind forbids. Never raises.

    ``image`` is a local path or the image bytes. ``kind`` is the producing
    provider's ``kind``; only a kind whose policy is ``forbidden`` is sent to
    the scanner — the rest come back ``not_scanned`` with the policy recorded,
    so a caller can tell "we checked" from "there was nothing to check".

    Every failure — unreadable file, unreachable scanner, malformed answer —
    returns ``unavailable`` with the cause in ``reason``. Nothing here
    returns ``pass`` for pixels no scanner looked at.
    """
    policy = policy_for_kind(kind, site_config)
    if policy != POLICY_FORBIDDEN:
        return ImageTextScan(
            status=STATUS_NOT_SCANNED, kind=kind, policy=policy,
            reason=(
                f"kind {kind!r} has no text policy — not scanned"
                if policy is None else f"text is {policy} for kind {kind!r}"
            ),
        )
    cfg = settings or TextScanSettings.from_site_config(site_config)
    if not cfg.enabled:
        return ImageTextScan(
            status=STATUS_DISABLED, kind=kind, policy=policy,
            reason="image_ocr_gate_enabled=false",
        )
    engine = backend or ImageGenServerScanBackend(cfg)
    try:
        data = image if isinstance(image, bytes) else await asyncio.to_thread(
            Path(image).read_bytes,
        )
        raw = await engine.scan(data, min_confidence=cfg.min_confidence)
    except Exception as exc:  # noqa: BLE001 — every failure is "could not verify"
        reason = describe_exception(exc)
        logger.warning(
            "[TEXT-SCAN] could not scan %s image (%s) — recorded as UNAVAILABLE "
            "(unverified), not as clean", kind, reason,
        )
        return ImageTextScan(
            status=STATUS_UNAVAILABLE, kind=kind, policy=policy,
            max_chars=cfg.max_chars, backend=getattr(engine, "name", ""),
            reason=reason[:300],
        )
    return ImageTextScan(
        status=STATUS_PASS if raw.text_chars <= cfg.max_chars else STATUS_FAIL,
        kind=kind,
        policy=policy,
        text_chars=raw.text_chars,
        coverage_pct=raw.coverage_pct,
        detections=raw.detections,
        max_chars=cfg.max_chars,
        backend=getattr(engine, "name", ""),
    )


# ---------------------------------------------------------------------------
# site_config readers — tolerant of SiteConfig, a plain dict, or None
# ---------------------------------------------------------------------------


def _get(site_config: Any, key: str, default: Any) -> Any:
    if site_config is None:
        return default
    try:
        val = site_config.get(key, default)
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not
        # decide a scan's fate; the documented code default applies.
        return default
    return default if val in (None, "") else val


def _bool(site_config: Any, key: str, default: bool) -> bool:
    val = _get(site_config, key, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(float(_get(site_config, key, default)))
    except (TypeError, ValueError):
        return default


def _float(site_config: Any, key: str, default: float) -> float:
    try:
        return float(_get(site_config, key, default))
    except (TypeError, ValueError):
        return default


__all__ = [
    "DEFAULT_KIND_POLICY",
    "KIND_CHART",
    "KIND_COMPOSED",
    "KIND_GENERATE",
    "KIND_SCREENSHOT",
    "KIND_SEARCH",
    "OCR_GATE_REJECTED_ERROR",
    "OCR_GATE_REJECTED_STATUS",
    "POLICY_EXPECTED",
    "POLICY_FORBIDDEN",
    "POLICY_NOT_APPLICABLE",
    "STATUS_DISABLED",
    "STATUS_FAIL",
    "STATUS_NOT_SCANNED",
    "STATUS_PASS",
    "STATUS_UNAVAILABLE",
    "ImageGenServerScanBackend",
    "ImageTextScan",
    "RawTextScan",
    "TextScanBackend",
    "TextScanSettings",
    "TextScanUnavailable",
    "describe_ocr_gate_rejection",
    "infer_image_kind_from_url",
    "is_ocr_gate_rejection",
    "parse_kind_policy",
    "policy_for_kind",
    "resolve_kind_policy",
    "safe_json",
    "scan_image_text",
    "should_exclude",
]

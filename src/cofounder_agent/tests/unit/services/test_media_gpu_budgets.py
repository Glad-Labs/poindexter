"""Media-stage GPU wait budgets — poindexter#914 P2 caller migration, group 2.

Group 1 migrated the fail-soft QA rails. Group 2 migrates the three media
stages, which differ in two ways that matter:

- They call ``gpu.lock()`` DIRECTLY rather than going through
  ``dispatch_complete``, so the budget is passed at the lock, not the dispatch.
- Their own holds are far heavier than a judge call (``gpu_lease_stats``
  p90: media_scripts 33.6s, video_review 65.5s, video_director 134.0s), so the
  budget sits higher than the rails' 45s.

What justifies migrating them at all is that none of them can fail a post:
``generate_media_scripts`` is explicitly non-critical and preserves any podcast
script built so far, and both video stages return ``None`` so the post ships
without a shot list. Blocking, by contrast, costs the whole article — an
unbudgeted media stage queued behind a render is what stranded a finished post
past the 30-min stale-reclaim, which then re-ran the pipeline from the draft.

Pinned here:

1. The budget sits in the measured gap between waitable LLM traffic and
   skippable render holds, and ``0`` restores the legacy unbounded contract.
2. Every media ``gpu.lock`` call passes the budget at ``background`` priority.
3. ``GpuBusyError`` fails SOFT at each site, and is caught AHEAD of the broad
   handler so contention is never logged as a dispatch fault.
4. A contention skip gets its own finding kind, separate from the QA rails'.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.site_config import SiteConfig

try:  # pragma: no cover - import shape differs only under partial installs
    from unittest.mock import patch
except ImportError:  # pragma: no cover
    raise

_STAGES_DIR = (
    Path(__file__).resolve().parents[3] / "modules" / "content" / "stages"
)
_MEDIA_STAGE_FILES = (
    "generate_media_scripts.py",
    "generate_video_shot_list.py",
    "review_video_shot_list.py",
)


def _with_settings(**cfg):
    """Register a SiteConfig on the process container gpu_scheduler reads."""
    return patch(
        "services.gpu_scheduler._sc",
        return_value=SiteConfig(initial_config=cfg),
    )


# ---------------------------------------------------------------------------
# Budget resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_budget_defaults_between_llm_and_render_holds():
    """120s must sit ABOVE the ordinary LLM traffic media should queue behind
    and BELOW every long holder it should skip behind — otherwise media either
    skips constantly or never skips at all.

    Soak p90s that bracket it (gpu_lease_stats, 07-26..30): generate_content
    105.3s must be waitable; qa_rewrite 210.5s, featured_image 228.7s,
    inline_image_batch 240.0s and media_render 383.5s must be skippable.
    """
    from services.gpu_scheduler import media_wait_budget_s

    with _with_settings():
        budget = media_wait_budget_s()

    assert budget == 120.0
    assert budget > 105.3, "must outwait generate_content p90 (ordinary traffic)"
    assert budget < 210.5, "must skip behind a qa_rewrite hold"
    assert budget < 228.7, "must skip behind a featured_image render"
    assert budget < 383.5, "must skip behind a media_render"


@pytest.mark.unit
def test_budget_is_operator_tunable():
    from services.gpu_scheduler import media_wait_budget_s

    with _with_settings(gpu_sched_media_max_wait_s="240"):
        assert media_wait_budget_s() == 240.0


@pytest.mark.unit
def test_zero_restores_legacy_unbounded_contract():
    """The escape hatch: 0 means None, which is the un-migrated behaviour."""
    from services.gpu_scheduler import media_wait_budget_s

    with _with_settings(gpu_sched_media_max_wait_s="0"):
        assert media_wait_budget_s() is None


@pytest.mark.unit
def test_media_budget_exceeds_qa_rail_budget():
    """Media does real generation work where a rail does a judge call, so its
    budget must not silently inherit the rails' much tighter 45s."""
    from services.gpu_scheduler import media_wait_budget_s, qa_rail_wait_budget_s

    with _with_settings():
        assert media_wait_budget_s() > qa_rail_wait_budget_s()


# ---------------------------------------------------------------------------
# Call-site wiring (AST — the budget must reach every media gpu.lock)
# ---------------------------------------------------------------------------


def _gpu_lock_calls(path: Path) -> list[ast.Call]:
    """Every ``gpu.lock(...)`` call in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "lock"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "gpu"
    ]


@pytest.mark.unit
@pytest.mark.parametrize("filename", _MEDIA_STAGE_FILES)
def test_every_media_gpu_lock_passes_the_budget(filename):
    """A missed lock site is invisible at runtime — it just keeps the old
    unbounded wait — so pin every one of them rather than a sample."""
    path = _STAGES_DIR / filename
    calls = _gpu_lock_calls(path)
    assert calls, f"no gpu.lock() calls found in {filename}"

    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "max_wait_s" in kwargs, (
            f"{filename}:{call.lineno} gpu.lock() without max_wait_s — this "
            "site keeps the legacy unbounded wait and can still burn the 900s "
            "lock ceiling behind a render."
        )
        assert "priority" in kwargs, (
            f"{filename}:{call.lineno} gpu.lock() without priority"
        )


@pytest.mark.unit
@pytest.mark.parametrize("filename", _MEDIA_STAGE_FILES)
def test_media_locks_use_background_priority(filename):
    """Media is the lowest-value work in the queue; it must not outrank the
    pipeline traffic it is supposed to wait behind."""
    path = _STAGES_DIR / filename
    for call in _gpu_lock_calls(path):
        for kw in call.keywords:
            if kw.arg == "priority":
                assert isinstance(kw.value, ast.Constant)
                assert kw.value.value == "background", (
                    f"{filename}:{call.lineno} priority={kw.value.value!r}"
                )


@pytest.mark.unit
@pytest.mark.parametrize("filename", _MEDIA_STAGE_FILES)
def test_media_locks_use_the_media_budget_helper(filename):
    """Guards against a hardcoded literal drifting from the DB setting."""
    text = (_STAGES_DIR / filename).read_text(encoding="utf-8")
    assert "media_wait_budget_s()" in text, (
        f"{filename} must source its budget from media_wait_budget_s() so the "
        "gpu_sched_media_max_wait_s setting stays the single control."
    )


# ---------------------------------------------------------------------------
# Fail-soft handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("filename", _MEDIA_STAGE_FILES)
def test_gpu_busy_is_caught_before_the_broad_handler(filename):
    """``except GpuBusyError`` must precede ``except Exception`` in the same
    try, or the broad handler swallows contention first and every skip is
    logged as a dispatch fault — which is exactly the conflation this split
    exists to prevent.
    """
    tree = ast.parse((_STAGES_DIR / filename).read_text(encoding="utf-8"))

    def _names(handler: ast.ExceptHandler) -> set[str]:
        t = handler.type
        if isinstance(t, ast.Name):
            return {t.id}
        if isinstance(t, ast.Tuple):
            return {e.id for e in t.elts if isinstance(e, ast.Name)}
        return set()

    found_guarded_try = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        order = [_names(h) for h in node.handlers]
        busy_idx = next(
            (i for i, n in enumerate(order) if "GpuBusyError" in n), None,
        )
        if busy_idx is None:
            continue
        found_guarded_try = True
        broad_idx = next(
            (i for i, n in enumerate(order) if "Exception" in n), None,
        )
        if broad_idx is not None:
            assert busy_idx < broad_idx, (
                f"{filename}: `except Exception` at handler {broad_idx} "
                f"precedes `except GpuBusyError` at {busy_idx} — contention "
                "would be caught as a generic dispatch failure."
            )

    assert found_guarded_try, (
        f"{filename} passes a wait budget but never handles GpuBusyError — "
        "admission would raise straight into the pipeline, turning a bounded "
        "skip into a stage crash."
    )


@pytest.mark.unit
@pytest.mark.parametrize("filename", _MEDIA_STAGE_FILES)
def test_contention_skip_reports_its_own_finding(filename):
    """A skip and a real failure otherwise look identical downstream."""
    text = (_STAGES_DIR / filename).read_text(encoding="utf-8")
    assert "surface_media_gpu_busy_skip" in text


@pytest.mark.unit
def test_media_skip_finding_kind_is_distinct_from_qa_rails():
    """Separate kinds because the remedies differ: a QA skip means review is
    being crowded out, a media skip means the post shipped without a podcast
    script or shot list."""
    from modules.content.stages._media_gpu_skip import surface_media_gpu_busy_skip

    class _Busy:
        reason = "holder_eta_exceeds_budget"
        eta_seconds = 230.0

    captured = {}

    with patch("utils.findings.emit_finding", lambda **kw: captured.update(kw)):
        surface_media_gpu_busy_skip("media_scripts", _Busy(), task_id="t-3")

    # Assert the emitted kind, not the source text — the module docstring
    # legitimately names the QA kind it mirrors.
    assert captured["kind"] == "media_gpu_busy_skip"
    assert captured["kind"] != "qa_rail_gpu_busy_skip"
    assert captured["dedup_key"].startswith("media_gpu_busy_skip:")


@pytest.mark.unit
def test_skip_finding_never_raises_into_the_caller():
    """The finding is telemetry on a degraded path — if emitting it could
    raise, a contention skip would become the stage crash it exists to avoid.
    """
    from modules.content.stages._media_gpu_skip import surface_media_gpu_busy_skip

    class _Busy:
        reason = "holder_eta_exceeds_budget"
        eta_seconds = 230.0

    with patch(
        "utils.findings.emit_finding", side_effect=RuntimeError("sink down"),
    ):
        surface_media_gpu_busy_skip("media_scripts", _Busy(), task_id="t-1")


@pytest.mark.unit
def test_skip_finding_tolerates_a_busy_error_without_eta():
    """``eta_seconds`` is None when admission refuses for a reason other than a
    holder estimate; formatting it must not blow up the degraded path."""
    from modules.content.stages._media_gpu_skip import surface_media_gpu_busy_skip

    class _Busy:
        reason = "queue_depth"
        eta_seconds = None

    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)

    with patch("utils.findings.emit_finding", _capture):
        surface_media_gpu_busy_skip("video_director", _Busy(), task_id="t-2")

    assert captured["kind"] == "media_gpu_busy_skip"
    assert captured["severity"] == "info"
    assert "unknown" in captured["body"]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_skip_finding_is_log_only_by_default():
    """A bounded skip under load is the design working; routing it would page
    on ordinary render pressure."""
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["findings.media_gpu_busy_skip.delivery"] == "log_only"
    assert DEFAULTS["findings.media_gpu_busy_skip.min_severity"] == "info"


@pytest.mark.unit
def test_budget_default_is_seeded():
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["gpu_sched_media_max_wait_s"] == "120"


class TestTerminalGpuBusySkip:
    """A director skip is not the same class of event as a review skip
    (poindexter#1001).

    Both were reported at info as routine. But the director's skip leaves
    video_shot_list = {}, and the piece then publishes, gets claimed, pays for
    TTS + transcription + captions, skips both render lanes, and reports
    `QA'd 0 asset(s)` as success — retiring itself with no video. 27 pieces
    sat that way, each announced as a routine skip.
    """

    def _emit_capture(self, monkeypatch):
        captured: list[dict] = []
        monkeypatch.setattr(
            "utils.findings.emit_finding", lambda **kw: captured.append(kw),
        )
        return captured

    def test_default_skip_stays_advisory(self, monkeypatch):
        from modules.content.stages._media_gpu_skip import (
            surface_media_gpu_busy_skip,
        )

        captured = self._emit_capture(monkeypatch)
        surface_media_gpu_busy_skip(
            "video_review", SimpleNamespace(reason="busy", eta_seconds=12.0),
            task_id="t1",
        )

        assert captured[0]["severity"] == "info"
        assert "publish is not blocked" in captured[0]["body"]
        assert captured[0]["extra"]["terminal"] is False

    def test_terminal_skip_warns_and_names_the_loss(self, monkeypatch):
        from modules.content.stages._media_gpu_skip import (
            surface_media_gpu_busy_skip,
        )

        captured = self._emit_capture(monkeypatch)
        surface_media_gpu_busy_skip(
            "video_director", SimpleNamespace(reason="busy", eta_seconds=30.0),
            task_id="t2", terminal=True,
        )

        f = captured[0]
        assert f["severity"] == "warn"
        assert f["extra"]["terminal"] is True
        assert "cannot render video" in f["body"]
        # names the recovery path so the page is actionable
        assert "BackfillVideoShotListsJob" in f["body"]

    def test_the_director_call_site_passes_terminal(self):
        """Guard: the flag is worthless if the one caller that needs it
        forgets to pass it."""
        import inspect

        from modules.content.stages import generate_video_shot_list as mod

        src = inspect.getsource(mod)
        assert 'surface_media_gpu_busy_skip(\n' in src
        assert '"video_director"' in src
        director_call = src[src.index('"video_director", busy'):]
        assert "terminal=True" in director_call[:200]

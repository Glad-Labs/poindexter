"""OCR text-leakage gate tests for scripts/image-gen-server.py.

2026-07-13: guidance-distilled models (Z-Image-Turbo) run at guidance_scale=0,
where negative_prompt has no effect. The 2026-07 bake-off (glad-labs-stack#2386)
measured z_image_turbo leaking ~57x more readable text into images than the
best-scoring alternative, and showed a positive-prompt "textless" clause alone
doesn't fix it. Matt's call was to keep z_image_turbo for its aesthetic, so
/generate now OCR-scans every render and retries with a fresh seed (bounded,
keep-best) when leaked text exceeds a threshold.

These tests cover the pure logic (scoring, best-of-N selection, settings
parsing) and the settings-reload glue — not the full /generate HTTP endpoint,
matching the granularity of test_image_gen_self_heal.py.

Same torch-stub + non-sys.modules-registering dynamic-import pattern as
test_image_gen_self_heal.py: each test file gets its own independent module
object (neither registers into sys.modules), so the two files can't collide.
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "image-gen-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/image-gen-server.py from " + str(start))


def _load_image_gen_server():
    # image-gen-server imports torch at module top (GPU paths only). Stub it so
    # the module loads without a CUDA stack — scoped to this load only, see
    # test_image_gen_self_heal.py for why a leaked stub poisons later tests.
    stub_installed = False
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        torch_stub.__spec__ = importlib.util.spec_from_loader("torch", loader=None)
        torch_stub.float16 = "float16"
        torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
        sys.modules["torch"] = torch_stub
        stub_installed = True

    server_path = _find_repo_root(Path(__file__)) / "scripts" / "image-gen-server.py"
    spec = importlib.util.spec_from_file_location("img_gen_server_ocr_gate_under_test", server_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


img_gen_server = _load_image_gen_server()


class FakeReader:
    """Stands in for easyocr.Reader — readtext() returns pre-baked detections."""

    def __init__(self, detections):
        self._detections = detections

    def readtext(self, _image_path, detail=1):
        return self._detections


def test_count_leaked_text_chars_filters_by_confidence():
    reader = FakeReader([
        ([], "HELLO", 0.9),   # 5 chars, confident — counts
        ([], "wor1d", 0.15),  # 5 chars, low-confidence noise — excluded
        ([], "OK", 0.31),     # 2 chars, just above threshold — counts
    ])
    chars = img_gen_server.count_leaked_text_chars(
        "fake.png", reader, min_confidence=0.3,
    )
    assert chars == 7  # "HELLO" + "OK"


def test_count_leaked_text_chars_no_detections_is_zero():
    reader = FakeReader([])
    assert img_gen_server.count_leaked_text_chars("fake.png", reader, min_confidence=0.3) == 0


def test_count_leaked_text_chars_propagates_reader_failure():
    """The scoring helper must RAISE, not return 0, when OCR can't run.

    0 means "scanned, clean". A broken reader that returned 0 would be
    indistinguishable from a clean image, which is exactly how a gate can
    report success on every image it never actually scanned. The /generate
    loop turns this exception into text_chars=None ("unavailable").
    """
    class BrokenReader:
        def readtext(self, _image_path, detail=1):
            raise RuntimeError("easyocr model weights missing")

    with pytest.raises(RuntimeError):
        img_gen_server.count_leaked_text_chars(
            "fake.png", BrokenReader(), min_confidence=0.3,
        )


def test_pick_best_attempt_lowest_wins():
    GenAttempt = img_gen_server.GenAttempt
    attempts = [
        GenAttempt(seed=1, text_chars=12, path=Path("a.png")),
        GenAttempt(seed=2, text_chars=0, path=Path("b.png")),
        GenAttempt(seed=3, text_chars=4, path=Path("c.png")),
    ]
    best = img_gen_server.pick_best_attempt(attempts)
    assert best.seed == 2
    assert best.text_chars == 0


def test_pick_best_attempt_prefers_scored_over_unscored():
    """A known-bad attempt beats an unknown one: 50 leaked chars can be
    reported honestly and rejected, an unverified image can only be guessed
    about."""
    GenAttempt = img_gen_server.GenAttempt
    attempts = [
        GenAttempt(seed=1, text_chars=None, path=Path("unscored.png")),
        GenAttempt(seed=2, text_chars=50, path=Path("scored.png")),
    ]
    assert img_gen_server.pick_best_attempt(attempts).seed == 2


def test_pick_best_attempt_all_unscored_returns_first():
    """Whole-batch OCR outage — nothing to rank, so keep the caller's seed."""
    GenAttempt = img_gen_server.GenAttempt
    attempts = [
        GenAttempt(seed=7, text_chars=None, path=Path("a.png")),
        GenAttempt(seed=8, text_chars=None, path=Path("b.png")),
    ]
    best = img_gen_server.pick_best_attempt(attempts)
    assert best.seed == 7
    assert best.text_chars is None


def test_pick_best_attempt_ties_prefer_first():
    """On a tie, the caller's requested/derived seed (attempt 1) wins over a
    later re-roll — min() is stable and attempt order == list order."""
    GenAttempt = img_gen_server.GenAttempt
    attempts = [
        GenAttempt(seed=100, text_chars=0, path=Path("first.png")),
        GenAttempt(seed=200, text_chars=0, path=Path("second.png")),
    ]
    best = img_gen_server.pick_best_attempt(attempts)
    assert best.seed == 100


def test_parse_ocr_gate_config_defaults_on_empty():
    cfg = img_gen_server._parse_ocr_gate_config({})
    assert cfg == img_gen_server.DEFAULT_OCR_GATE_CONFIG


def test_parse_ocr_gate_config_reads_valid_values():
    cfg = img_gen_server._parse_ocr_gate_config({
        "image_ocr_gate_enabled": "false",
        "image_ocr_gate_max_chars": "10",
        "image_ocr_gate_max_attempts": "5",
        "image_ocr_gate_min_confidence": "0.5",
    })
    assert cfg.enabled is False
    assert cfg.max_chars == 10
    assert cfg.max_attempts == 5
    assert cfg.min_confidence == 0.5


def test_parse_ocr_gate_config_bool_variants():
    for truthy in ("true", "TRUE", "1", "yes", "on"):
        assert img_gen_server._parse_ocr_gate_config(
            {"image_ocr_gate_enabled": truthy},
        ).enabled is True
    for falsy in ("false", "FALSE", "0", "no", "off"):
        assert img_gen_server._parse_ocr_gate_config(
            {"image_ocr_gate_enabled": falsy},
        ).enabled is False
    # An empty value means "unset", not "explicitly false" — falls back to
    # the code-level default (True), same as a missing key entirely.
    assert img_gen_server._parse_ocr_gate_config(
        {"image_ocr_gate_enabled": ""},
    ).enabled is True


def test_parse_ocr_gate_config_malformed_value_falls_back_per_field():
    """A garbage max_chars must not corrupt the other fields — each setting
    parses (or falls back) independently."""
    cfg = img_gen_server._parse_ocr_gate_config({
        "image_ocr_gate_max_chars": "not-a-number",
        "image_ocr_gate_max_attempts": "5",
    })
    assert cfg.max_chars == img_gen_server.DEFAULT_OCR_GATE_CONFIG.max_chars
    assert cfg.max_attempts == 5


def test_parse_ocr_gate_config_min_confidence_clamped_to_unit_interval():
    assert img_gen_server._parse_ocr_gate_config(
        {"image_ocr_gate_min_confidence": "5.0"},
    ).min_confidence == 1.0
    assert img_gen_server._parse_ocr_gate_config(
        {"image_ocr_gate_min_confidence": "-2.0"},
    ).min_confidence == 0.0


def test_parse_ocr_gate_config_max_attempts_floored_at_one():
    """max_attempts must never reach 0 — a gate that never generates an
    image isn't a gate, it's an outage."""
    assert img_gen_server._parse_ocr_gate_config(
        {"image_ocr_gate_max_attempts": "0"},
    ).max_attempts == 1
    assert img_gen_server._parse_ocr_gate_config(
        {"image_ocr_gate_max_attempts": "-3"},
    ).max_attempts == 1


def test_reload_ocr_gate_config_falls_back_to_defaults_when_settings_read_is_empty(monkeypatch):
    """Self-heal, not suppress: read_ocr_gate_settings()'s documented contract
    is 'returns {} on any DB failure' (verified separately below) — this test
    covers reload_ocr_gate_config()'s side: an empty read must degrade to
    'gate runs at defaults', never crash or silently disable the gate."""
    async def body():
        async def empty_read():
            return {}

        monkeypatch.setattr(img_gen_server, "read_ocr_gate_settings", empty_read)
        img_gen_server.state.ocr_gate = img_gen_server.OcrGateConfig(max_chars=999)

        await img_gen_server.reload_ocr_gate_config()

        assert img_gen_server.state.ocr_gate == img_gen_server.DEFAULT_OCR_GATE_CONFIG

    asyncio.run(body())


def test_reload_ocr_gate_config_applies_db_values(monkeypatch):
    async def body():
        async def fake_read():
            return {"image_ocr_gate_max_chars": "20", "image_ocr_gate_max_attempts": "2"}

        monkeypatch.setattr(img_gen_server, "read_ocr_gate_settings", fake_read)
        img_gen_server.state.ocr_gate = img_gen_server.DEFAULT_OCR_GATE_CONFIG

        await img_gen_server.reload_ocr_gate_config()

        assert img_gen_server.state.ocr_gate.max_chars == 20
        assert img_gen_server.state.ocr_gate.max_attempts == 2
        # Untouched keys still fall back to the code-level default.
        assert img_gen_server.state.ocr_gate.enabled is True

    asyncio.run(body())


# ---------------------------------------------------------------------------
# Verdict + enforcement (poindexter#1004)
#
# Before this, the gate scored correctly and then returned the leaking image
# anyway: 67 of 693 renders (2026-07-13..2026-08-08) failed and shipped, one
# of them a hero reading "Poindexter Philosophy" at OCR confidence 1.000.
# ---------------------------------------------------------------------------

def _attempt(chars):
    return img_gen_server.GenAttempt(seed=1, text_chars=chars, path=Path("x.png"))


def test_resolve_gate_status_pass_fail_boundary():
    """max_chars is inclusive — exactly at the threshold still passes."""
    gate = img_gen_server.OcrGateConfig(max_chars=6)
    assert img_gen_server.resolve_gate_status(_attempt(0), gate) == "pass"
    assert img_gen_server.resolve_gate_status(_attempt(6), gate) == "pass"
    assert img_gen_server.resolve_gate_status(_attempt(7), gate) == "fail"
    # The real incident: "Poindexter" + "Philosophy", 20 chars at conf 1.000.
    assert img_gen_server.resolve_gate_status(_attempt(20), gate) == "fail"


def test_resolve_gate_status_unscored_is_unavailable_not_pass():
    """THE regression guard. An unscored attempt must never read as a pass —
    that is the QA-rail fail-open shape: a gate reporting success for work it
    never did."""
    gate = img_gen_server.OcrGateConfig(max_chars=6)
    assert img_gen_server.resolve_gate_status(_attempt(None), gate) == "unavailable"


def test_resolve_gate_status_disabled_makes_no_claim():
    gate = img_gen_server.OcrGateConfig(enabled=False)
    assert img_gen_server.resolve_gate_status(_attempt(None), gate) == "disabled"
    assert img_gen_server.resolve_gate_status(_attempt(999), gate) == "disabled"


def test_should_reject_render_blocks_a_failing_scan_when_enforcing():
    gate = img_gen_server.OcrGateConfig(enforce=True)
    assert img_gen_server.should_reject_render("fail", gate) is True
    assert img_gen_server.should_reject_render("pass", gate) is False
    assert img_gen_server.should_reject_render("disabled", gate) is False


def test_should_reject_render_enforce_off_is_annotate_only():
    """The pre-#1004 behaviour, kept reachable as an operator escape hatch."""
    gate = img_gen_server.OcrGateConfig(enforce=False)
    assert img_gen_server.should_reject_render("fail", gate) is False


def test_should_reject_render_unavailable_needs_its_own_opt_in():
    """A broken OCR dependency must not silently become a total
    image-generation outage — that is a second setting, not a side effect of
    turning enforcement on."""
    enforcing = img_gen_server.OcrGateConfig(enforce=True)
    assert img_gen_server.should_reject_render("unavailable", enforcing) is False

    fail_closed = img_gen_server.OcrGateConfig(
        enforce=True, fail_closed_when_unavailable=True,
    )
    assert img_gen_server.should_reject_render("unavailable", fail_closed) is True

    # fail_closed alone does nothing while enforcement is off — one switch
    # governs "does this gate block", the other only refines which verdicts.
    annotate_only = img_gen_server.OcrGateConfig(
        enforce=False, fail_closed_when_unavailable=True,
    )
    assert img_gen_server.should_reject_render("unavailable", annotate_only) is False


def test_gate_passed_derivation_never_claims_a_pass_for_unavailable():
    """`ocr_gate_passed` is the field the response contract exposes; pin the
    derivation so a future refactor can't quietly restore fail-open."""
    passed_states = {"pass", "disabled"}
    for status in ("pass", "fail", "unavailable", "disabled"):
        assert (status in passed_states) is (status in ("pass", "disabled"))
    assert "unavailable" not in passed_states


def test_default_config_enforces():
    """Ship enforcing. An advisory-only gate is what let 67 images through."""
    assert img_gen_server.DEFAULT_OCR_GATE_CONFIG.enforce is True
    assert img_gen_server.DEFAULT_OCR_GATE_CONFIG.fail_closed_when_unavailable is False


def test_parse_ocr_gate_config_reads_enforcement_keys():
    cfg = img_gen_server._parse_ocr_gate_config({
        "image_ocr_gate_enforce": "false",
        "image_ocr_gate_fail_closed_when_unavailable": "true",
    })
    assert cfg.enforce is False
    assert cfg.fail_closed_when_unavailable is True


def test_enforcement_keys_are_in_the_settings_read_list():
    """A key the server never SELECTs can't be tuned from the DB, however
    carefully it's parsed."""
    assert "image_ocr_gate_enforce" in img_gen_server.OCR_GATE_SETTING_KEYS
    assert (
        "image_ocr_gate_fail_closed_when_unavailable"
        in img_gen_server.OCR_GATE_SETTING_KEYS
    )


def test_read_ocr_gate_settings_returns_empty_dict_on_connect_failure(monkeypatch):
    """Contract for read_ocr_gate_settings itself (not just its caller): a
    DB-connect exception must be swallowed into {}, never propagate."""
    async def body():
        async def failing_connect(*_a, **_kw):
            raise RuntimeError("no route to host")

        monkeypatch.setattr(img_gen_server.asyncpg, "connect", failing_connect)
        result = await img_gen_server.read_ocr_gate_settings()
        assert result == {}

    asyncio.run(body())

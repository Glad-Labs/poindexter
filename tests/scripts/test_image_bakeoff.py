"""Unit tests for the pure logic in scripts/image_bakeoff.py.

Loaded via importlib so torch/diffusers/easyocr (lazy-imported inside the GPU
functions) are never needed — these tests run with only Pillow + pytest.
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "image_bakeoff",
    Path(__file__).resolve().parents[2] / "scripts" / "image_bakeoff.py",
)
bakeoff = importlib.util.module_from_spec(_spec)
# Register in sys.modules before exec: with `from __future__ import annotations`
# @dataclass resolves string annotations against sys.modules[cls.__module__].
sys.modules["image_bakeoff"] = bakeoff
_spec.loader.exec_module(bakeoff)


# --- Task 2: roster + fair-fight resolver ---------------------------------


def _model(mechanism, guidance=4.5):
    return bakeoff.BakeoffModel(
        key="x", repo="org/x", pipeline_cls="XPipeline", mechanism=mechanism,
        steps=8, guidance=guidance, dtype="bfloat16", license_tier="apache",
    )


def test_cfg_model_gets_negative_prompt_and_guidance():
    kw = bakeoff.resolve_generate_kwargs(_model("cfg"), "a server room")
    assert kw["negative_prompt"] == bakeoff.NEG_PROMPT
    assert kw["guidance_scale"] == 4.5
    assert kw["prompt"] == "a server room"  # unmodified


def test_distilled_model_gets_no_text_clause_and_zero_guidance():
    kw = bakeoff.resolve_generate_kwargs(_model("distilled"), "a server room")
    assert "negative_prompt" not in kw
    assert kw["guidance_scale"] == 0.0
    assert kw["prompt"].endswith(bakeoff.NO_TEXT_CLAUSE)
    assert kw["prompt"].startswith("a server room")


def test_roster_has_twelve_unique_keys():
    keys = [m.key for m in bakeoff.ROSTER]
    assert len(keys) == 12
    assert len(set(keys)) == 12


# --- Task 3: OCR text-leakage scorer --------------------------------------


class _FakeReader:
    def __init__(self, results):
        self._results = results

    def readtext(self, path, detail=1):
        return self._results


def test_count_text_chars_sums_confident_detections():
    reader = _FakeReader([("b", "HELLO", 0.9), ("b", "world", 0.8)])
    assert bakeoff.count_text_chars("x.png", reader) == 10


def test_count_text_chars_filters_low_confidence():
    reader = _FakeReader([("b", "HELLO", 0.9), ("b", "zz", 0.1)])
    assert bakeoff.count_text_chars("x.png", reader) == 5


def test_count_text_chars_zero_on_no_detections():
    assert bakeoff.count_text_chars("x.png", _FakeReader([])) == 0


# --- Task 4: contact-sheet assembler --------------------------------------


def test_contact_sheet_dimensions_three_rows():
    # 12 cells, 4 cols -> 3 rows
    w, h = bakeoff.contact_sheet_dimensions(12, cols=4, thumb=384, pad=8, label_h=24)
    assert w == 4 * 384 + 5 * 8            # cols*thumb + (cols+1)*pad
    assert h == 3 * (384 + 24) + 4 * 8     # rows*(thumb+label) + (rows+1)*pad


def test_contact_sheet_dimensions_partial_last_row():
    # 5 cells, 4 cols -> 2 rows
    _w, h = bakeoff.contact_sheet_dimensions(5, cols=4, thumb=384, pad=8, label_h=24)
    assert h == 2 * (384 + 24) + 3 * 8


def test_build_contact_sheet_writes_file(tmp_path):
    from PIL import Image
    img_a = tmp_path / "a.png"
    Image.new("RGB", (100, 100), "red").save(img_a)
    out = tmp_path / "sheet.png"
    result = bakeoff.build_contact_sheet(
        [("model_a", str(img_a)), ("model_b", None)], str(out), cols=2,
    )
    assert result == str(out)
    assert out.exists()
    assert Image.open(out).size == bakeoff.contact_sheet_dimensions(2, 2, 384, 8, 24)


# --- Task 5: run records + results aggregation ----------------------------


def test_summarize_ranks_by_text_chars_and_flags_vram():
    roster = [
        bakeoff.BakeoffModel("a", "o/a", "P", "cfg", 8, 4.0, "bfloat16", "apache"),
        bakeoff.BakeoffModel("b", "o/b", "P", "distilled", 8, 0.0, "bfloat16", "mit"),
    ]
    records = [
        bakeoff.RunRecord("a", "p1", "a1.png", text_chars=2, latency_s=3.0, peak_vram_gb=10.0),
        bakeoff.RunRecord("a", "p2", "a2.png", text_chars=4, latency_s=5.0, peak_vram_gb=12.0),
        bakeoff.RunRecord("b", "p1", "b1.png", text_chars=40, latency_s=2.0, peak_vram_gb=20.0),
        bakeoff.RunRecord("b", "p2", None, text_chars=None, latency_s=None, peak_vram_gb=None, error="OOM"),
    ]
    summary = bakeoff.summarize(records, roster, consumer_gb=16.0)
    assert [row["model"] for row in summary] == ["a", "b"]  # a leaks less -> first
    row_a, row_b = summary
    assert row_a["mean_text_chars"] == 3.0
    assert row_a["max_peak_vram_gb"] == 12.0
    assert row_a["consumer_ok"] is True          # 12 <= 16
    assert row_b["consumer_ok"] is False         # 20 > 16
    assert row_b["n_error"] == 1


def test_results_markdown_contains_header_and_rows():
    summary = [{"model": "a", "mean_text_chars": 3.0, "max_peak_vram_gb": 12.0,
                "mean_latency_s": 4.0, "consumer_ok": True, "license_tier": "apache",
                "n_ok": 2, "n_error": 0}]
    md = bakeoff.results_markdown(summary)
    assert "| model |" in md
    assert "| a |" in md

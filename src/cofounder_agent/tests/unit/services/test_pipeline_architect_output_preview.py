"""Task-trace capture — the per-node ``output_preview`` helper.

``_preview`` builds a bounded, human-readable snapshot of a node's changed
output values for ``atom_runs.output_preview`` (the readable preview the
console trace deep-dive shows). It truncates *per value* so a single huge
value (e.g. a 5000-char draft) never buries the other small keys.
"""
from __future__ import annotations

from services.pipeline_architect import _preview, _preview_max_bytes


def test_preview_truncates_large_values_but_keeps_small_keys():
    out = {"draft": "x" * 5000, "title": "Hello"}
    s = _preview(out, ["draft", "title"], max_bytes=200)
    assert "title" in s and "Hello" in s          # small key survives intact
    assert "x" * 5000 not in s                     # huge value was truncated
    # Bounded: each value gets a per-key budget (max_bytes // n) plus a little
    # JSON syntax; never the unbounded 5000-char blob.
    assert len(s.encode()) <= 200 + 80


def test_preview_skips_control_keys_and_empty():
    # Leading-underscore control channels (_halt, _halt_reason) are not output.
    assert _preview({"_halt": True, "_halt_reason": "x"}, ["_halt", "_halt_reason"], 2048) == ""
    assert _preview({}, [], 2048) == ""
    # A key listed but absent from the dict is ignored.
    assert _preview({"a": "1"}, ["a", "missing"], 2048) == '{"a": "1"}'


def test_preview_max_bytes_reads_setting_with_fallback():
    class _SC:
        def __init__(self, v):
            self._v = v

        def get(self, key, default=None):
            return self._v

    assert _preview_max_bytes(_SC("512")) == 512
    assert _preview_max_bytes(None) == 2048          # no site_config → default
    assert _preview_max_bytes(_SC("garbage")) == 2048  # unparseable → default

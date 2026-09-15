"""operating_record — the install facts qa.self_claim checks capabilities
and install specs against. Pure parsers plus the loader over a fake config."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services import operating_record as orec


def test_parse_gpu_specs_from_gpu_model_and_csv():
    assert orec.parse_gpu_specs("NVIDIA RTX 5090 (32GB VRAM)") == (orec.GpuFact("rtx 5090", 32.0),)
    assert orec.parse_gpu_specs("RTX 5090:32,RTX 3090:24") == (
        orec.GpuFact("rtx 5090", 32.0), orec.GpuFact("rtx 3090", 24.0),
    )
    assert orec.parse_gpu_specs("GeForce RTX5090") == (orec.GpuFact("rtx 5090", None),)
    assert orec.parse_gpu_specs("") == ()
    assert orec.parse_gpu_specs("a cpu-only box") == ()


def test_normalise_model_names_drops_prefixes_and_adds_bases():
    names = orec.normalise_model_names([
        {"model": "ollama/glm-4.7-5090:latest"}, {"model": "anthropic/claude-sonnet-5"}, {"model": None},
    ])
    assert {"glm-4.7-5090:latest", "glm-4.7-5090", "anthropic/claude-sonnet-5", "claude-sonnet-5"} <= names


def test_host_ram_reads_meminfo(tmp_path):
    f = tmp_path / "meminfo"
    f.write_text("MemTotal:       63393988 kB\nMemFree: 1 kB\n")
    assert orec.host_ram_gb(str(f)) == 60.5
    assert orec.host_ram_gb(str(tmp_path / "missing")) is None


def test_name_is_known_by_equality_and_containment():
    rec = orec.OperatingRecord(
        known_names=frozenset({"ollama", "google search console", "claude", "poindexter"}),
        model_names=frozenset({"qwen3.6:27b", "qwen3.6"}),
    )
    assert orec.name_is_known("Ollama", rec)
    assert orec.name_is_known("Google Search Console", rec)
    assert orec.name_is_known("Claude Sonnet", rec)          # contains a known name
    assert orec.name_is_known("Poindexter Studio", rec)
    assert orec.name_is_known("Qwen3.6", rec)                # a model that actually ran
    assert not orec.name_is_known("Jettison", rec)
    assert not orec.name_is_known("Kubernetes", rec)


class _Config:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=""):
        return self._v.get(key, default)

    def all(self):
        return dict(self._v)


@pytest.mark.asyncio
async def test_loader_merges_settings_plugins_host_and_models(tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 63393988 kB\n")
    monkeypatch.setattr(orec, "host_ram_gb", lambda path="/proc/meminfo": 60.5)
    cfg = _Config({
        "qa_self_claim_known_components": "ollama, grafana",
        "qa_self_claim_product_names": "poindexter",
        "site_name": "Glad Labs",
        "plugin.tts_provider.chatterbox.base_url": "http://x",
        "plugin.image_provider.pexels.enabled": "true",
        "gpu_model": "NVIDIA RTX 5090 (32GB VRAM)",
        "operating_record_gpus": "",
        "operating_record_ram_gb": "auto",
    })
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"model": "ollama/phi4:14b"}])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    rec = await orec.load_operating_record(cfg, pool)
    assert {"ollama", "grafana", "poindexter", "glad labs", "chatterbox", "pexels"} <= rec.known_names
    assert rec.ram_gb == 60.5
    assert rec.gpus == (orec.GpuFact("rtx 5090", 32.0),)
    assert "phi4" in rec.model_names and "phi4:14b" in rec.model_names
    assert rec.sources["gpus"] == "gpu_model"


@pytest.mark.asyncio
async def test_loader_prefers_explicit_gpu_and_ram_settings():
    cfg = SimpleNamespace(get=lambda k, d="": {
        "operating_record_gpus": "RTX 5090:32,RTX 3090:24", "operating_record_ram_gb": "64",
    }.get(k, d))
    rec = await orec.load_operating_record(cfg, None)
    assert [g.name for g in rec.gpus] == ["rtx 5090", "rtx 3090"] and rec.total_vram_gb == 56.0
    assert rec.ram_gb == 64.0 and rec.sources["ram"] == "operating_record_ram_gb"
    assert rec.model_names == frozenset()  # no pool → no model layer, no guess

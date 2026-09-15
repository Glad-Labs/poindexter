"""Operating record — the facts about THIS install that a draft's claims
about our own system are checked against.

The truth-oriented QA rails compare a draft with its research bundle. A
sentence like "We also run Jettison" or "on a 5090 with 128GB of system RAM"
has no research bundle to compare with: its corpus is the install itself. The
2026-09-14 approval-queue review caught both of those by hand at QA 95-97;
``qa.self_claim`` had no record to check them against. This module builds
that record from what the process can see, and only from that — a fact it
cannot derive is absent, never guessed, so a check that needs it is skipped
rather than faked (the ``qa.self_claim`` "reduced coverage, never a fake
verdict" contract).

Sources, in order of trust:

- **Names we run or use** — ``qa_self_claim_known_components`` (operator CSV,
  seeded with the stack), ``qa_self_claim_product_names`` + ``site_name``, the
  ``plugin.<kind>.<name>.*`` segments of every settings key (every wired
  provider names itself there), and the model identifiers in ``cost_logs``
  for the trailing 90 days (what actually ran).
- **Host memory** — ``/proc/meminfo`` ``MemTotal`` as the worker sees it, or
  ``operating_record_ram_gb`` when the operator pins it.
- **GPUs** — ``operating_record_gpus`` (``"RTX 5090:32,RTX 3090:24"``), falling
  back to parsing ``gpu_model`` (``"NVIDIA RTX 5090 (32GB VRAM)"``).

Pure helpers (``parse_gpu_specs``, ``normalise_model_names``, ``name_is_known``)
carry the logic so the rail's tests need no host.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Seed for ``qa_self_claim_known_components`` — what a Poindexter install
#: actually runs or leans on. Operators extend the setting; the rail never
#: guesses. Deliberately NOT here: engines we evaluated but do not run (vLLM,
#: SGLang), and vendors we do not pay (OpenAI).
DEFAULT_KNOWN_COMPONENTS = (
    "poindexter, python, postgres, postgresql, pgvector, docker, docker compose, "
    "ollama, litellm, langgraph, langfuse, llamaindex, ragas, deepeval, prefect, fastapi, "
    "next.js, nextjs, vercel, cloudflare, r2, grafana, prometheus, loki, tempo, pyroscope, "
    "glitchtip, sentry, uptime kuma, alertmanager, comfyui, wan, wan 2.2, wan 2.1, qwen, "
    "qwen image, flux, flux.2, sdxl, z-image, kokoro, chatterbox, speaches, whisper, "
    "faster-whisper, pexels, postiz, livekit, tailscale, telegram, discord, github, "
    "github actions, dependabot, playwright, ffmpeg, pgadmin, mintlify, anthropic, claude, "
    "claude sonnet, gemma, phi, phi4, granite, llama.cpp, nvidia, cuda, rtx 5090, rtx 3090, "
    "pop!_os, ubuntu, linux, systemd, google search console, search console, google analytics, "
    "adsense, youtube, linkedin, x, twitter, reddit, mastodon, tiktok, instagram, dev.to, "
    "hacker news, wikipedia, restic"
)

_GPU_NAME_RE = re.compile(
    r"\b((?:geforce\s+)?(?:rtx|gtx)\s?\d{4}(?:\s?(?:ti|super))?|a100|h100|l40s?|"
    r"radeon\s+rx\s?\d{4}[a-z]*)\b",
    re.IGNORECASE,
)
_VRAM_RE = re.compile(r"(\d{1,3})\s?(?:gb|g)\b", re.IGNORECASE)
_OLLAMA_PREFIX_RE = re.compile(r"^ollama(?:_chat)?/")


@dataclass(frozen=True)
class GpuFact:
    name: str  # normalised, e.g. "rtx 5090"
    vram_gb: float | None = None


@dataclass(frozen=True)
class OperatingRecord:
    known_names: frozenset[str] = frozenset()
    ram_gb: float | None = None
    gpus: tuple[GpuFact, ...] = ()
    model_names: frozenset[str] = frozenset()
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def has_gpus(self) -> bool:
        return bool(self.gpus)

    @property
    def total_vram_gb(self) -> float | None:
        vals = [g.vram_gb for g in self.gpus if g.vram_gb]
        return sum(vals) if vals else None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _csv(raw: Any) -> list[str]:
    return [_norm(p) for p in str(raw or "").split(",") if _norm(p)]


def parse_gpu_specs(raw: str | None) -> tuple[GpuFact, ...]:
    """``"RTX 5090:32,RTX 3090:24"`` or ``"NVIDIA RTX 5090 (32GB VRAM)"`` →
    GPU facts. Each comma-separated part yields at most one GPU; the first
    ``<n>GB`` in the part is its VRAM."""
    out: list[GpuFact] = []
    for part in str(raw or "").split(","):
        if not part.strip():
            continue
        name_m = _GPU_NAME_RE.search(part)
        if not name_m:
            continue
        name = _norm(name_m.group(1)).replace("geforce ", "")
        name = re.sub(r"^(rtx|gtx)\s?(\d)", r"\1 \2", name)  # "rtx5090" → "rtx 5090"
        vram: float | None = None
        tail = part[name_m.end():]
        vram_m = _VRAM_RE.search(tail) or re.search(r":\s*(\d{1,3})\b", tail)
        if vram_m:
            try:
                vram = float(vram_m.group(1))
            except ValueError:
                vram = None
        out.append(GpuFact(name=name, vram_gb=vram))
    return tuple(out)


def normalise_model_names(rows: Any) -> frozenset[str]:
    """``cost_logs.model`` values → lowercased identifiers, with the
    ``ollama/`` prefix dropped and the base name (before ``:``) added too."""
    names: set[str] = set()
    for row in rows or ():
        raw = row.get("model") if isinstance(row, dict) else (row["model"] if row else None)
        if not raw:
            continue
        m = _OLLAMA_PREFIX_RE.sub("", _norm(raw))
        names.add(m)
        base = m.split(":", 1)[0]
        names.add(base)
        names.add(base.split("/")[-1])
    return frozenset(n for n in names if n)


def host_ram_gb(meminfo_path: str = "/proc/meminfo") -> float | None:
    """Host memory in GiB as this process sees it, or None."""
    try:
        with open(meminfo_path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = float(line.split()[1])
                    return round(kb / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError) as exc:
        logger.warning("[operating_record] host RAM unavailable (%s) — RAM checks skip", exc)
    return None


def name_is_known(name: str, record: OperatingRecord) -> bool:
    """A claimed product/tool name is known when it equals, contains, or is
    contained in a known component or a model identifier."""
    n = _norm(name)
    if not n:
        return True
    n_bare = re.sub(r"[^a-z0-9 ]", "", n)
    for known in record.known_names | record.model_names:
        k = re.sub(r"[^a-z0-9 ]", "", known)
        if not k:
            continue
        if n == known or n_bare == k or n_bare in k or k in n_bare:
            return True
    return False


async def load_operating_record(site_config: Any, pool: Any = None) -> OperatingRecord:
    """Assemble the record from settings, the host and (optionally) cost_logs."""
    sources: dict[str, str] = {}
    names: set[str] = set()

    def _get(key: str, default: str = "") -> str:
        try:
            return str(site_config.get(key, default) or default)
        except Exception as exc:  # noqa: BLE001 — a stubbed config must not sink the rail
            logger.warning("[operating_record] could not read %s: %s", key, exc)
            return default

    names.update(_csv(_get("qa_self_claim_known_components", DEFAULT_KNOWN_COMPONENTS)))
    names.update(_csv(_get("qa_self_claim_product_names", "poindexter")))
    site_name = _norm(_get("site_name"))
    if site_name:
        names.add(site_name)
    sources["names"] = "qa_self_claim_known_components + product names + site_name"
    all_keys = getattr(site_config, "all", None)
    if callable(all_keys):
        try:
            for key in all_keys():
                if key.startswith("plugin."):
                    parts = key.split(".")
                    if len(parts) >= 3 and parts[2]:
                        names.add(_norm(parts[2].replace("_", " ")))
                        names.add(_norm(parts[2]))
            sources["names"] += " + plugin.* settings"
        except (TypeError, AttributeError) as exc:
            logger.warning("[operating_record] settings snapshot unavailable: %s", exc)

    ram_raw = _norm(_get("operating_record_ram_gb", "auto"))
    ram: float | None
    if ram_raw and ram_raw != "auto":
        try:
            ram = float(ram_raw)
            sources["ram"] = "operating_record_ram_gb"
        except ValueError:
            logger.warning("[operating_record] operating_record_ram_gb=%r is not a number; using /proc/meminfo", ram_raw)
            ram = host_ram_gb()
            sources["ram"] = "/proc/meminfo"
    else:
        ram = host_ram_gb()
        sources["ram"] = "/proc/meminfo"

    gpus = parse_gpu_specs(_get("operating_record_gpus"))
    if gpus:
        sources["gpus"] = "operating_record_gpus"
    else:
        gpus = parse_gpu_specs(_get("gpu_model"))
        if gpus:
            sources["gpus"] = "gpu_model"

    models: frozenset[str] = frozenset()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT model FROM cost_logs "
                    "WHERE created_at > now() - interval '90 days' AND model IS NOT NULL"
                )
            models = normalise_model_names(rows)
            sources["models"] = "cost_logs (90d)"
        except Exception as exc:  # noqa: BLE001 — DB layer is optional coverage
            logger.warning("[operating_record] cost_logs model list skipped (reduced coverage): %s", exc)

    return OperatingRecord(
        known_names=frozenset(names), ram_gb=ram, gpus=gpus, model_names=models, sources=sources,
    )


__all__ = [
    "DEFAULT_KNOWN_COMPONENTS",
    "GpuFact",
    "OperatingRecord",
    "host_ram_gb",
    "load_operating_record",
    "name_is_known",
    "normalise_model_names",
    "parse_gpu_specs",
]

"""Image-model bake-off harness (throwaway one-off).

Generates a fixed prompt set across a roster of open text-to-image models,
scores OCR text-leakage / VRAM / latency, and assembles contact sheets +
a results table. Runs in its own venv; never touches the live image-gen server.

torch / diffusers / easyocr are imported lazily inside the GPU/OCR functions so
the pure-logic helpers (resolver, scorer, sheet, aggregation) stay testable with
only Pillow + pytest.

See docs/superpowers/specs/2026-07-12-image-model-bakeoff-design.md.
"""
from __future__ import annotations

import sys

# Windows consoles default to cp1252, which crashes on the Unicode block chars
# that easyocr / diffusers download progress bars emit. Force UTF-8 so any
# library's progress output can't kill the run mid-download. (getattr keeps the
# type checker happy — TextIO doesn't declare reconfigure, TextIOWrapper has it.)
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from statistics import mean

from PIL import Image, ImageDraw


NEG_PROMPT = (
    "text, words, letters, numbers, signage, UI, labels, "
    "watermark, captions, writing, logo"
)
NO_TEXT_CLAUSE = "clean textless composition, no text, no signage, no UI, no labels"


# ---------------------------------------------------------------------------
# Roster + fair-fight config (Task 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BakeoffModel:
    key: str
    repo: str
    # Pinned commit SHA for `repo`. A bake-off is a comparison, and an unpinned
    # repo makes the result unreproducible — re-running months later would
    # silently score different weights under the same key. It is also the same
    # supply-chain exposure the live servers have. SHAs are upstream `main` as
    # of the 2026-07-17 pin (the two also used by the live image server were
    # additionally verified against this host's HF cache). Required by
    # tests/unit/scripts/test_hf_revision_pinning.py.
    revision: str
    pipeline_cls: str            # diffusers pipeline class name
    mechanism: str               # "cfg" | "distilled"
    steps: int
    guidance: float              # used only for CFG models
    dtype: str                   # "bfloat16" | "float16"
    license_tier: str            # "apache" | "mit" | "community" | "custom"
    gated: bool = False
    # SDXL-Lightning-style LoRA + trailing scheduler (mirrors the live server's
    # ModelConfig); None/False for everything else. A LoRA is a separate repo,
    # so it carries its own pin.
    lora_repo: str | None = None
    lora_weight_name: str | None = None
    lora_revision: str | None = None
    scheduler_trailing: bool = False
    use_fp16_variant: bool = False
    notes: str = ""


# NOTE: repo IDs / pipeline_cls for brand-new models are validated in the Task 6
# dry run; trim any whose pipeline isn't importable from diffusers git main.
ROSTER: list[BakeoffModel] = [
    BakeoffModel(
        "sdxl_lightning", "stabilityai/stable-diffusion-xl-base-1.0",
        "462165984030d82259a11f4367a4eed129e94a7b",
        "StableDiffusionXLPipeline", "distilled", 4, 0.0, "float16", "openrail",
        lora_repo="ByteDance/SDXL-Lightning",
        lora_weight_name="sdxl_lightning_4step_lora.safetensors",
        lora_revision="c9a24f48e1c025556787b0c58dd67a091ece2e44",
        scheduler_trailing=True, use_fp16_variant=True,
        notes="old baseline (SDXL + 4-step Lightning LoRA)",
    ),
    BakeoffModel(
        "z_image_turbo", "Tongyi-MAI/Z-Image-Turbo",
        "f332072aa78be7aecdf3ee76d5c247082da564a6",
        "ZImagePipeline", "distilled", 9, 0.0, "bfloat16", "apache",
        notes="current control",
    ),
    BakeoffModel(
        "chroma1_hd", "lodestones/Chroma1-HD",
        "0e0c60ece1e82b17cb7f77342d765ba5024c40c0",
        "ChromaPipeline", "cfg", 26, 4.5, "bfloat16", "apache",
    ),
    BakeoffModel(
        "chroma1_flash", "lodestones/Chroma1-Flash",
        "093c4f63b60507234aa53a073f1f9f565b3f4336",
        "ChromaPipeline", "distilled", 8, 0.0, "bfloat16", "apache",
    ),
    BakeoffModel(
        "cogview4", "zai-org/CogView4-6B",
        "63a52b7f6dace7033380cd6da14d0915eab3e6b5",
        "CogView4Pipeline", "cfg", 50, 3.5, "bfloat16", "apache",
    ),
    BakeoffModel(
        "lumina2", "Alpha-VLLM/Lumina-Image-2.0",
        "53504abd8178b30685b6c4c7a4cd181ff78b73e9",
        "Lumina2Pipeline", "cfg", 30, 4.0, "bfloat16", "apache",
    ),
    BakeoffModel(
        "flux2_klein", "black-forest-labs/FLUX.2-klein-4B",
        "e7b7dc27f91deacad38e78976d1f2b499d76a294",
        "Flux2Pipeline", "distilled", 8, 0.0, "bfloat16", "apache",
        notes="mechanism validated in dry run",
    ),
    BakeoffModel(
        "hidream_fast", "HiDream-ai/HiDream-I1-Fast",
        "4856a5d8cd6fbd194780ed9f289bdf696d3afc10",
        "HiDreamImagePipeline", "distilled", 16, 0.0, "bfloat16", "mit",
    ),
    BakeoffModel(
        "qwen_image", "Qwen/Qwen-Image",
        "75e0b4be04f60ec59a75f475837eced720f823b6",
        "QwenImagePipeline", "cfg", 30, 4.0, "bfloat16", "apache",
    ),
    BakeoffModel(
        "flux1_schnell", "black-forest-labs/FLUX.1-schnell",
        "741f7c3ce8b383c54771c7003378a50191e9efe9",
        "FluxPipeline", "distilled", 4, 0.0, "bfloat16", "apache", gated=True,
    ),
    BakeoffModel(
        "sd35_medium", "stabilityai/stable-diffusion-3.5-medium",
        "b940f670f0eda2d07fbb75229e779da1ad11eb80",
        "StableDiffusion3Pipeline", "cfg", 40, 4.5, "bfloat16", "community",
        gated=True,
    ),
    BakeoffModel(
        "krea2_turbo", "krea/Krea-2-Turbo",
        "1161245028ef398cd0a951101b2bbf486464f841",
        "Krea2Pipeline", "distilled", 8, 0.0, "bfloat16", "custom",
        notes="eval-only; custom license (not gated)",
    ),
]


def resolve_generate_kwargs(m: BakeoffModel, prompt: str) -> dict:
    """Fair-fight generate kwargs.

    CFG models get the real negative prompt at their guidance; distilled models
    get guidance 0 + a positive no-text clause (the only steering a guidance-free
    model responds to).
    """
    if m.mechanism == "cfg":
        return {
            "prompt": prompt,
            "negative_prompt": NEG_PROMPT,
            "num_inference_steps": m.steps,
            "guidance_scale": m.guidance,
        }
    return {
        "prompt": f"{prompt}, {NO_TEXT_CLAUSE}",
        "num_inference_steps": m.steps,
        "guidance_scale": 0.0,
    }


# ---------------------------------------------------------------------------
# OCR text-leakage scorer (Task 3)
# ---------------------------------------------------------------------------


def count_text_chars(image_path: str, reader) -> int:
    """Total detected text characters in an image (lower = better).

    `reader` is an easyocr.Reader (or any object exposing
    `.readtext(path, detail=1) -> list[(box, text, confidence)]`).
    Confidence < 0.3 detections are dropped as noise.
    """
    detections = reader.readtext(image_path, detail=1)
    return sum(len(text) for _box, text, conf in detections if conf >= 0.3)


# ---------------------------------------------------------------------------
# Contact-sheet assembler (Task 4)
# ---------------------------------------------------------------------------


def contact_sheet_dimensions(
    n: int, cols: int, thumb: int, pad: int, label_h: int
) -> tuple[int, int]:
    """Canvas (width, height) for an n-cell grid of thumb-sized cells."""
    rows = max(1, math.ceil(n / cols))
    width = cols * thumb + (cols + 1) * pad
    height = rows * (thumb + label_h) + (rows + 1) * pad
    return width, height


def build_contact_sheet(
    cells: list[tuple[str, str | None]],
    out_path: str,
    *,
    cols: int = 4,
    thumb: int = 384,
    pad: int = 8,
    label_h: int = 24,
) -> str:
    """Render labelled thumbnails in a grid and save to out_path.

    cells is (label, image_path); a None/missing image_path renders a gray
    placeholder so a failed model still occupies its slot.
    """
    width, height = contact_sheet_dimensions(len(cells), cols, thumb, pad, label_h)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for i, (label, path) in enumerate(cells):
        r, c = divmod(i, cols)
        x = pad + c * (thumb + pad)
        y = pad + r * (thumb + label_h + pad)
        if path:
            try:
                im = Image.open(path).convert("RGB").resize((thumb, thumb))
                canvas.paste(im, (x, y))
            except OSError:
                draw.rectangle([x, y, x + thumb, y + thumb], fill="gray")
        else:
            draw.rectangle([x, y, x + thumb, y + thumb], fill="gray")
        draw.text((x + 4, y + thumb + 4), label, fill="black")
    canvas.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Run records + results aggregation (Task 5)
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    model: str
    prompt_id: str
    image_path: str | None
    text_chars: int | None
    latency_s: float | None
    peak_vram_gb: float | None
    error: str | None = None


def summarize(
    records: list[RunRecord],
    roster: list[BakeoffModel],
    consumer_gb: float = 16.0,
) -> list[dict]:
    """Aggregate per-(model,prompt) records into per-model summary rows,
    sorted by mean text-leakage ascending (best first)."""
    by_key = {m.key: m for m in roster}
    grouped: dict[str, list[RunRecord]] = {}
    for rec in records:
        grouped.setdefault(rec.model, []).append(rec)

    rows: list[dict] = []
    for key, recs in grouped.items():
        ok = [r for r in recs if r.error is None]
        text_vals = [r.text_chars for r in ok if r.text_chars is not None]
        lat_vals = [r.latency_s for r in ok if r.latency_s is not None]
        vram_vals = [r.peak_vram_gb for r in ok if r.peak_vram_gb is not None]
        max_vram = max(vram_vals) if vram_vals else None
        rows.append({
            "model": key,
            "mean_text_chars": mean(text_vals) if text_vals else float("inf"),
            "max_peak_vram_gb": max_vram,
            "mean_latency_s": round(mean(lat_vals), 2) if lat_vals else None,
            "consumer_ok": (max_vram is not None and max_vram <= consumer_gb),
            "license_tier": by_key[key].license_tier if key in by_key else "?",
            "n_ok": len(ok),
            "n_error": len(recs) - len(ok),
        })
    rows.sort(key=lambda r: r["mean_text_chars"])
    return rows


def results_markdown(summary: list[dict]) -> str:
    """Render the per-model summary as a markdown table."""
    header = (
        "| model | mean_text_chars | max_vram_gb | consumer_ok | "
        "mean_latency_s | license | ok | err |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for r in summary:
        lines.append(
            f"| {r['model']} | {r['mean_text_chars']} | {r['max_peak_vram_gb']} | "
            f"{r['consumer_ok']} | {r['mean_latency_s']} | {r['license_tier']} | "
            f"{r['n_ok']} | {r['n_error']} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt loader + GPU model runner (Task 6) — torch/diffusers imported lazily
# ---------------------------------------------------------------------------


def load_prompts(path: str) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [(row["id"], row["text"]) for row in data]


def load_pipeline(m: BakeoffModel):
    """Import the model's diffusers pipeline class and load it onto cuda:0.

    Handles the SDXL-Lightning LoRA + trailing-scheduler recipe when lora_repo
    is set. Raises on unknown class / download 403 (gated, unaccepted) / OOM —
    the caller converts the exception into a RunRecord error.
    """
    import diffusers
    import torch
    from huggingface_hub import hf_hub_download

    dtype = torch.bfloat16 if m.dtype == "bfloat16" else torch.float16

    if m.lora_repo:
        # SDXL + distilled LoRA (Lightning): load base, fuse the LoRA, switch to
        # a trailing-timestep scheduler so the few-step guidance-0 recipe works.
        from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline
        kwargs = {"torch_dtype": dtype}
        if m.use_fp16_variant:
            kwargs["variant"] = "fp16"
        pipe = StableDiffusionXLPipeline.from_pretrained(
            m.repo, revision=m.revision, **kwargs,
        )
        pipe.load_lora_weights(
            hf_hub_download(m.lora_repo, m.lora_weight_name, revision=m.lora_revision)
        )
        pipe.fuse_lora()
        if m.scheduler_trailing:
            pipe.scheduler = EulerDiscreteScheduler.from_config(
                pipe.scheduler.config, timestep_spacing="trailing"
            )
        return pipe.to("cuda:0")

    if not hasattr(diffusers, m.pipeline_cls):
        raise RuntimeError(
            f"diffusers has no {m.pipeline_cls} (upgrade git main or trim {m.key})"
        )
    cls = getattr(diffusers, m.pipeline_cls)
    return cls.from_pretrained(m.repo, torch_dtype=dtype, revision=m.revision).to("cuda:0")


def _unload(pipe) -> None:
    import gc

    import torch

    del pipe
    # nn.Module graphs commonly hold reference cycles (autograd hooks, buffer
    # registries) that refcounting alone won't collect — without gc.collect()
    # first, empty_cache() has nothing to reclaim and VRAM from the previous
    # model leaks into the next model's peak-memory reading.
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()


def run_one_model(
    m: BakeoffModel,
    prompts: list[tuple[str, str]],
    out_dir: str,
    reader,
) -> list[RunRecord]:
    """Generate + score every prompt for one model. Never raises: a load or
    per-image failure is recorded as RunRecord.error and the run continues.

    Always frees GPU memory before returning, including on a load failure —
    from_pretrained()/.to("cuda") can partially allocate before raising (e.g.
    OOM mid weight-transfer), and without an explicit empty_cache() that
    residue is only reclaimed once Python's GC gets around to it, which can
    poison the NEXT model's load in a tight-VRAM sequential sweep.
    """
    import torch

    os.makedirs(out_dir, exist_ok=True)
    pipe = None
    try:
        try:
            pipe = load_pipeline(m)
        except Exception as exc:  # noqa: BLE001 — bake-off records, never crashes
            return [
                RunRecord(m.key, pid, None, None, None, None, error=f"load: {exc}")
                for pid, _ in prompts
            ]

        records: list[RunRecord] = []
        for pid, text in prompts:
            kwargs = resolve_generate_kwargs(m, text)
            img_path = os.path.join(out_dir, f"{m.key}__{pid}.png")
            try:
                torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                image = pipe(**kwargs).images[0]
                latency = time.perf_counter() - t0
                image.save(img_path)
                peak_gb = torch.cuda.max_memory_allocated() / 1024**3
                chars = count_text_chars(img_path, reader)
                records.append(
                    RunRecord(m.key, pid, img_path, chars, latency, peak_gb)
                )
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                records.append(
                    RunRecord(m.key, pid, None, None, None, None, error="OOM")
                )
            except Exception as exc:  # noqa: BLE001
                records.append(
                    RunRecord(m.key, pid, None, None, None, None, error=str(exc))
                )
        return records
    finally:
        if pipe is not None:
            _unload(pipe)
        else:
            # load_pipeline() raised before returning a pipe — a partial
            # weight-transfer allocation may still be resident; defensively
            # clear it so it can't bleed into the next model.
            import gc

            gc.collect()
            torch.cuda.empty_cache()


# ---------------------------------------------------------------------------
# CLI orchestrator (Task 7) — easyocr imported lazily
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Image-model bake-off harness")
    parser.add_argument("--models", default="", help="comma-separated model keys; default all")
    parser.add_argument("--prompts", default="scripts/image_bakeoff_prompts.json")
    parser.add_argument("--out", default="scripts/image_bakeoff_results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    wanted = set(filter(None, args.models.split(",")))
    roster = [m for m in ROSTER if not wanted or m.key in wanted]
    prompts = load_prompts(args.prompts)

    if args.dry_run:
        print(f"Would run {len(roster)} models x {len(prompts)} prompts:")
        for m in roster:
            gate = "GATED" if m.gated else "open"
            print(f"  {m.key:16} {m.mechanism:9} {m.license_tier:10} {gate}")
        return 0

    import easyocr

    reader = easyocr.Reader(["en"], gpu=False, verbose=False)

    all_records: list[RunRecord] = []
    for m in roster:
        print(f"=== {m.key} ===", flush=True)
        recs = run_one_model(m, prompts, os.path.join(args.out, "images"), reader)
        for r in recs:
            flag = r.error or f"{r.text_chars} chars, {r.peak_vram_gb:.1f}GB"
            print(f"  {r.prompt_id:22} {flag}", flush=True)
        all_records.extend(recs)

    # Per-prompt contact sheets (one grid per prompt, all models side by side).
    os.makedirs(os.path.join(args.out, "sheets"), exist_ok=True)
    by_prompt: dict[str, list[tuple[str, str | None]]] = {}
    for r in all_records:
        by_prompt.setdefault(r.prompt_id, []).append((r.model, r.image_path))
    for pid, cells in by_prompt.items():
        build_contact_sheet(cells, os.path.join(args.out, "sheets", f"{pid}.png"))

    summary = summarize(all_records, roster)
    md = results_markdown(summary)
    with open(os.path.join(args.out, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print("\n" + md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
